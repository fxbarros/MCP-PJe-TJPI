"""Cliente de automacao do PJe-TJPI (1o Grau) via Playwright.

Funcionalidades:
- Login automatico (CPF + senha + 2FA TOTP)
- Troca automatica de perfil (advogado/procurador)
- Consulta de expedientes pendentes
- Consulta de processo por numero CNJ
- Busca por nome, CPF, CNPJ, OAB
- Listagem e leitura de documentos (HTML e PDF)
- Tratamento automatico do aviso da Resolucao CNJ 121/2010
"""
import asyncio
import functools
import html as html_module
import io
import re
import sys
import time

import pdfplumber
import pyotp
from playwright.async_api import async_playwright

URL_BASE = "https://pje.tjpi.jus.br/1g"


def _log(msg: str) -> None:
    """Loga em stderr (stdout quebra o transporte stdio do MCP)."""
    print(msg, file=sys.stderr, flush=True)


def _serializa(metodo):
    """Serializa metodos publicos com o lock de operacao do cliente.

    O MCP client pode disparar tool calls em PARALELO; como o singleton
    compartilha uma unica page do Playwright, duas operacoes simultaneas
    navegariam uma por cima da outra e corromperiam o scraping.
    OBS: o lock NAO e reentrante - metodo decorado nao pode chamar outro
    metodo decorado (use os helpers privados _abrir_autos_processo etc.).
    """
    @functools.wraps(metodo)
    async def wrapper(self, *args, **kwargs):
        async with self._op_lock:
            return await metodo(self, *args, **kwargs)
    return wrapper


def _html_para_texto(html: str) -> str:
    """Extrai texto legivel de HTML sem precisar renderizar numa page."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h[1-6]>", "\n", html)
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = html_module.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" ?\n ?", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


class PJeClient:
    """Cliente para automatizar consultas ao PJe-TJPI - 1o Grau."""

    def __init__(self, cpf, senha, totp_seed, persona="advogado", headless=True):
        """Inicializa o cliente.

        Args:
            cpf: CPF do usuario (so numeros)
            senha: Senha do PDPJ
            totp_seed: Seed TOTP em Base32 (gerada ao configurar 2FA)
            persona: "advogado" (default) ou "procurador"
            headless: True (default) - desde 2026-05-05 o Chromium headless
                funciona no PJe-TJPI (a suspeita antiga de bloqueio nao se
                reproduziu; ver nota em cliente_singleton). Use headless=False
                (ou PJE_HEADLESS=0 via singleton) pra debug visual.
        """
        self.cpf = cpf
        self.senha = senha
        self.totp = pyotp.TOTP(totp_seed)
        self.headless = headless

        if persona not in ("advogado", "procurador"):
            raise ValueError(
                f"Persona invalida: {persona}. Use 'advogado' ou 'procurador'."
            )
        self.persona = persona

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._op_lock = asyncio.Lock()  # serializa operacoes (page compartilhada)
        self._ultimo_processo_foi_terceiro = False
        self._ultimo_processo_id = None  # ID interno do PJe extraido da URL dos autos
        self._ultimo_processo_cnj = None  # CNJ (so digitos) a que o id acima pertence
        self._ultimo_processo_ca = None  # Codigo de autenticacao da sessao (URL dos autos)

    async def __aenter__(self):
        await self._iniciar()
        await self._login()
        await self._trocar_perfil()
        return self

    async def __aexit__(self, *args):
        await self._fechar()

    # ========== SETUP / TEARDOWN ==========

    async def _iniciar(self):
        """Inicia o browser Playwright.

        IMPORTANTE: roda HEADED (headless=False) por default porque o PJe-TJPI
        bloqueia Chromium headless. Singleton mantem 1 janela aberta por ate 5min,
        entao nao fica piscando entre tool calls.
        """
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        self._page = await self._context.new_page()

        # Handler global para dialogs (Resolucao CNJ 121 etc.)
        self._context.on("page", self._setup_dialog_handler)
        self._setup_dialog_handler(self._page)

    def _setup_dialog_handler(self, page):
        """Aceita automaticamente qualquer dialog de confirmacao do PJe."""
        async def handle_dialog(dialog):
            msg = dialog.message[:200]
            _log(f"[DIALOG] Aceitando: {msg[:100]}...")
            # Resolucao CNJ 121 = processo de terceiro
            if "Resolução" in msg or "121" in msg or "não faz parte" in msg:
                self._ultimo_processo_foi_terceiro = True
            await dialog.accept()
        page.on("dialog", handle_dialog)

    async def _fechar(self):
        """Fecha browser e Playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

    # ========== LOGIN E TROCA DE PERFIL ==========

    async def _codigo_totp(self):
        """Gera codigo TOTP, esperando se estiver perto de virar ciclo.

        Espera com asyncio.sleep (nao time.sleep) pra nao travar o event
        loop do servidor MCP enquanto aguarda o proximo ciclo.
        """
        restante = self.totp.interval - (int(time.time()) % self.totp.interval)
        if restante < 3:
            await asyncio.sleep(restante + 1)
        return self.totp.now()

    async def _login(self):
        """Faz login no SSO do CNJ com CPF + senha + TOTP.

        Wrapper com 1 retry pra tolerar flakes esporadicas (p.ex. servidor
        redirecionando antes do 2FA aparecer, TOTP no fim do ciclo, etc.).
        """
        for tentativa in range(2):
            try:
                return await self._login_uma_vez()
            except Exception as e:
                if tentativa == 0:
                    _log(
                        f"[LOGIN] Falha na 1a tentativa ({e.__class__.__name__}: "
                        f"{str(e)[:120]}), retry em 5s..."
                    )
                    await asyncio.sleep(5)
                else:
                    raise

    async def _login_uma_vez(self):
        """Implementacao do login (1 tentativa).

        Se ja existe sessao valida nos cookies, pula o formulario de login -
        basta navegar pra login.seam e o PJe redireciona direto pro painel.
        Tambem detecta quando o servidor PULA o 2FA apos CPF+senha (cookie
        de "trust this device" ou sessao parcial) - nesse caso retorna sem
        tentar preencher o codigo TOTP.
        """
        _log(f"[LOGIN] Iniciando...")
        await self._page.goto(f"{URL_BASE}/login.seam", wait_until="domcontentloaded")

        # Se ja esta logado, login.seam redireciona pro painel ou mostra a mesma pagina
        # mas SEM o formulario de login. Detecta verificando se input#username existe.
        # (o proprio wait_for_selector ja da o tempo de renderizacao - sem sleep fixo)
        try:
            await self._page.wait_for_selector("input#username", timeout=4000)
            tem_form_login = True
        except Exception:
            tem_form_login = False

        if not tem_form_login:
            url_atual = self._page.url
            _log(f"[LOGIN] Ja logado (sessao em cache). URL atual: {url_atual}")
            return

        # CPF + senha
        await self._page.fill("input#username", self.cpf)
        await self._page.fill("input#password", self.senha)
        try:
            await self._page.click("input#kc-login", timeout=3000)
        except Exception:
            # Fallback com timeout curto: se o SSO ja redirecionou (sessao em
            # cache), button[type='submit'] nao existe mais. Sem timeout
            # explicito, o click default de 30s ficava pendurado e estourava o
            # timeout do MCP. Ultimo recurso: Enter no campo de senha submete o
            # form Keycloak de forma confiavel.
            try:
                await self._page.click("button[type='submit']", timeout=3000)
            except Exception:
                await self._page.press("input#password", "Enter")
        await self._page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Se o servidor pulou o 2FA (cookie de trust ou sessao parcial),
        # ja estamos logados - vai direto pra home.seam ou similar.
        url_pos_senha = self._page.url
        if any(p in url_pos_senha for p in ["home.seam", "painel", "ssoCallback", "Painel"]):
            _log(f"[LOGIN] Servidor pulou 2FA, ja logado. URL: {url_pos_senha}")
            return

        # 2FA
        codigo = await self._codigo_totp()
        await self._page.wait_for_selector("input[type='text']", timeout=15000)
        await self._page.fill("input[type='text']", codigo)
        try:
            await self._page.click("input#kc-login", timeout=2000)
        except Exception:
            try:
                await self._page.click("button[type='submit']", timeout=2000)
            except Exception:
                await self._page.press("input[type='text']", "Enter")
        await self._page.wait_for_load_state("domcontentloaded", timeout=30000)
        _log(f"[LOGIN] OK")

    async def _trocar_perfil(self):
        """Abre o dropdown do usuario e troca para a persona desejada.

        Tolerante: se nao achar o dropdown ou o link da persona, assume que
        ja esta no perfil correto (sessao em cache).
        """
        _log(f"[PERFIL] Trocando para perfil: {self.persona}")

        # Abre o dropdown do menu do usuario (o timeout do click ja espera o
        # elemento aparecer - sem sleep fixo antes)
        clicou_dropdown = False
        for sel in ["li.menu-usuario a.dropdown-toggle", "a.dropdown-toggle"]:
            try:
                await self._page.click(sel, timeout=5000)
                clicou_dropdown = True
                break
            except Exception:
                continue

        if not clicou_dropdown:
            _log(f"[PERFIL] Dropdown nao encontrado - assumindo perfil ja correto")
            return

        await asyncio.sleep(0.5)  # animacao do dropdown

        # Clica no link da persona correta
        if self.persona == "procurador":
            seletores = [
                "a:has-text('Procuradoria')",
                "a:has-text('Procurador')",
            ]
        else:
            seletores = [
                "a:has-text('Advogado(a)')",
                "a:has-text('Advogado')",
            ]

        clicou = False
        for sel in seletores:
            try:
                await self._page.click(sel, timeout=4000)
                clicou = True
                break
            except Exception:
                continue

        if not clicou:
            _log(f"[PERFIL] Link {self.persona} nao encontrado - perfil pode ja estar correto")
            return

        await self._page.wait_for_load_state("domcontentloaded", timeout=15000)
        _log(f"[PERFIL] OK - persona: {self.persona}")

    # ========== EXPEDIENTES PENDENTES ==========

    @_serializa
    async def expedientes_pendentes(self):
        """Lista expedientes pendentes de ciencia/resposta."""
        _log(f"[EXP] Navegando pro painel...")
        await self._page.goto(
            f"{URL_BASE}/Painel/painel_usuario/advogado.seam",
            wait_until="domcontentloaded"
        )
        await asyncio.sleep(3)

        titulo = await self._page.title()
        resultado = {
            "titulo_pagina": titulo,
            "contadores": {},
            "expedientes": [],
        }

        # Extrai contadores das categorias
        try:
            texto_pagina = await self._page.inner_text("body")
            padroes = [
                r"(Pendentes de ciência ou de resposta)\s*(\d+)",
                r"(Apenas pendentes de ciência)\s*(\d+)",
                r"(Ciência dada pelo destinatário direto ou indireto - pendente de resposta)\s*(\d+)",
                r"(Ciência dada pelo Judiciário - pendente de resposta)\s*(\d+)",
                r"(Cujo prazo findou nos últimos 10 dias - sem resposta)\s*(\d+)",
                r"(Sem prazo)\s*(\d+)",
                r"(Respondidos nos últimos 10 dias)\s*(\d+)",
            ]
            for pad in padroes:
                m = re.search(pad, texto_pagina, re.DOTALL)
                if m:
                    resultado["contadores"][m.group(1).strip()] = int(m.group(2))
        except Exception:
            pass

        # Expande a categoria principal
        try:
            await self._page.click(
                "text=Pendentes de ciência ou de resposta", timeout=3000
            )
            await asyncio.sleep(2)
        except Exception:
            pass

        # Itera pelas comarcas e extrai expedientes detalhados
        comarcas_processadas = set()
        for _ in range(5):
            comarcas_els = await self._page.query_selector_all(
                "text=/Comarca de \\w+/"
            )
            if not comarcas_els:
                break
            houve_click = False
            for el in comarcas_els:
                try:
                    nome = (await el.inner_text()).strip()
                    if nome in comarcas_processadas:
                        continue
                    comarcas_processadas.add(nome)
                    await el.click()
                    await asyncio.sleep(1)
                    try:
                        await self._page.click(
                            "text=Caixa de entrada", timeout=2000
                        )
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                    texto_atual = await self._page.inner_text("body")
                    for m in re.finditer(
                        r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇa-záéíóúâêôãõç\s]+?)\s*"
                        r"(Despacho|Decisão|Sentença|Intimação)\s*\((\d+)\).*?"
                        r"Prazo:\s*(\d+\s*dias?).*?"
                        r"Data limite prevista para manifestação:\s*"
                        r"(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2})"
                        r".*?(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})",
                        texto_atual,
                        re.DOTALL,
                    ):
                        item = {
                            "comarca": nome,
                            "parte": m.group(1).strip()[-100:],
                            "tipo": m.group(2),
                            "id_expediente": m.group(3),
                            "prazo": m.group(4),
                            "data_limite": m.group(5),
                            "numero_processo": m.group(6),
                        }
                        if item not in resultado["expedientes"]:
                            resultado["expedientes"].append(item)
                    houve_click = True
                except Exception:
                    continue
            if not houve_click:
                break

        return resultado

    # ========== CONSULTA DE PROCESSO POR CNJ ==========

    async def _abrir_autos_processo(self, numero_cnj):
        """Busca pelo numero CNJ e abre os autos. Retorna a page dos autos."""
        self._ultimo_processo_foi_terceiro = False
        _log(f"[PROC] Buscando {numero_cnj}...")
        await self._page.goto(
            f"{URL_BASE}/Processo/ConsultaProcesso/listView.seam",
            wait_until="domcontentloaded"
        )

        # Parse do CNJ em 5 segmentos (pula o J fixo)
        so_digitos = re.sub(r"\D", "", numero_cnj)
        if len(so_digitos) != 20:
            raise ValueError(
                f"Numero CNJ invalido (esperado 20 digitos, recebi {len(so_digitos)}): {numero_cnj}"
            )
        partes = [
            so_digitos[0:7],    # NNNNNNN
            so_digitos[7:9],    # DD
            so_digitos[9:13],   # AAAA
            so_digitos[14:16],  # TR (pula o J fixo)
            so_digitos[16:20],  # OOOO
        ]
        _log(f"[PROC] Partes do numero: {partes}")

        # PJe-TJPI tem 6 campos de input; os campos J e TR ja vem preenchidos.
        # wait_for_selector espera o form renderizar (substitui sleep fixo).
        await self._page.wait_for_selector(
            "input[id*='numeroProcesso']", timeout=10000
        )
        inputs = await self._page.query_selector_all(
            "input[id*='numeroProcesso']"
        )
        _log(f"[PROC] Campos encontrados: {len(inputs)}")

        if len(inputs) >= 6:
            await inputs[0].fill(partes[0])  # N: 0801447
            await inputs[1].fill(partes[1])  # DD: 61
            await inputs[2].fill(partes[2])  # AAAA: 2024
            # Pula indice 3 (J) e 4 (TR) - ja vem preenchidos
            await inputs[5].fill(partes[4])  # OOOO: 0037
            _log(f"[PROC] Preencheu: N={partes[0]} DD={partes[1]} AAAA={partes[2]} OOOO={partes[4]}")
        elif len(inputs) >= 5:
            for i, valor in enumerate(partes):
                await inputs[i].fill(valor)
        else:
            if inputs:
                await inputs[0].fill(numero_cnj)

        # Clica em Pesquisar
        for sel in [
            "button:has-text('Pesquisar')",
            "input[value='Pesquisar']",
            "button[type='submit']",
            "input[type='submit']",
        ]:
            try:
                await self._page.click(sel, timeout=2000)
                break
            except Exception:
                continue

        await self._page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Clica no link do processo no resultado (o wait_for_selector ja
        # espera o resultado da busca renderizar - sem sleep fixo antes)
        try:
            link = await self._page.wait_for_selector(
                f"a:has-text('{numero_cnj}'), a:has-text('{partes[0]}')",
                timeout=8000
            )
        except Exception:
            link = None
        paginas_antes = set(self._context.pages)
        if link:
            await link.click()
        else:
            links = await self._page.query_selector_all("table a")
            if links:
                await links[0].click()
            else:
                raise Exception(f"Processo {numero_cnj} nao encontrado")

        # Autos normalmente abrem em NOVA aba; com singleton pode haver abas
        # residuais, entao NAO confiar em pages[-1] cegamente: espera surgir
        # uma aba nova OU a propria page navegar pra tela de detalhe.
        aba_autos = None
        for _ in range(30):  # ate ~15s
            novas = [p for p in self._context.pages if p not in paginas_antes]
            if novas:
                aba_autos = novas[-1]
                break
            if "Detalhe" in self._page.url or "listProcessoCompleto" in self._page.url:
                aba_autos = self._page
                break
            await asyncio.sleep(0.5)
        if aba_autos is None:
            aba_autos = self._context.pages[-1]
            _log("[PROC] AVISO: aba dos autos nao detectada; usando a ultima aba")

        await aba_autos.wait_for_load_state("domcontentloaded", timeout=15000)
        # Espera um elemento concreto da tela dos autos (timeline/arvore de docs)
        try:
            await aba_autos.wait_for_selector(
                "[onclick*='abrirLinkDocumento'], #divTimeLine, a.btn-menu-abas",
                timeout=10000,
            )
        except Exception:
            await asyncio.sleep(1)  # fallback conservador
        _log(f"[PROC] Autos abertos em: {aba_autos.url}")

        # Extrai processo_id e codigo de autenticacao da URL dos autos
        # URL real: .../listProcessoCompletoAdvogado.seam?id=1660665&ca=4e624ca28cbd...
        # (parametro 'id' e nao 'idProcesso'; 'ca' e codigo de autenticacao da sessao)
        m_pid = re.search(r"[?&]id=(\d+)", aba_autos.url)
        if m_pid:
            self._ultimo_processo_id = m_pid.group(1)
            self._ultimo_processo_cnj = so_digitos  # vincula o id ao CNJ aberto
            _log(f"[PROC] processo_id={self._ultimo_processo_id}")
        else:
            self._ultimo_processo_id = None
            self._ultimo_processo_cnj = None
            _log(f"[PROC] AVISO: nao consegui extrair id da URL: {aba_autos.url}")

        m_ca = re.search(r"[?&]ca=([0-9a-f]+)", aba_autos.url)
        if m_ca:
            self._ultimo_processo_ca = m_ca.group(1)
            _log(f"[PROC] ca={self._ultimo_processo_ca[:20]}...")
        else:
            self._ultimo_processo_ca = None

        return aba_autos

    async def _fechar_aba_autos(self, aba):
        """Fecha a aba dos autos se (e somente se) nao for a page principal.

        O criterio antigo (len(pages) > 1) podia fechar a page principal
        quando havia abas residuais acumuladas no singleton.
        """
        if aba is not None and aba is not self._page:
            try:
                await aba.close()
            except Exception:
                pass

    @staticmethod
    def _extrair_partes(texto):
        """Extrai polo ativo/passivo do cabecalho dos autos.

        O PJe exibe literalmente 'Não encontrado' quando um dos polos nao
        esta cadastrado (ex.: jurisdicao voluntaria sem reu) - nesse caso
        devolve None no polo em vez de propagar o texto da tela.
        """
        m = re.search(
            r"(?:[A-Z]{2,6}\s+\d{7}-\d{2}[\d.]+\s*)(.+?\s+X\s+.+?)(?=\s*\n|\s*Ícone)",
            texto, re.DOTALL
        )
        if not m:
            return None
        bruto = m.group(1).strip()
        partes = {"partes": bruto}
        pedacos = re.split(r"\s+X\s+", bruto, maxsplit=1)
        if len(pedacos) == 2:
            ativo, passivo = (p.strip() for p in pedacos)
            partes["polo_ativo"] = ativo
            if passivo.lower() in ("não encontrado", "nao encontrado"):
                partes["polo_passivo"] = None
                partes["partes"] = ativo
                partes["observacao_partes"] = (
                    "Polo passivo nao cadastrado no PJe (a tela exibe 'Não encontrado')."
                )
            else:
                partes["polo_passivo"] = passivo
        return partes

    @_serializa
    async def buscar_processo(self, numero_cnj):
        """Retorna dados basicos de um processo pelo CNJ."""
        aba = await self._abrir_autos_processo(numero_cnj)
        try:
            texto = await aba.inner_text("body")

            resultado = {
                "numero_cnj": numero_cnj,
                "url_autos": aba.url,
                "processo_de_terceiro": self._ultimo_processo_foi_terceiro,
            }
            if self._ultimo_processo_foi_terceiro:
                resultado["aviso"] = (
                    "AVISO: voce nao e advogado/parte neste processo. "
                    "O acesso foi registrado conforme Resolucao CNJ 121/2010."
                )

            # Cabecalho
            m = re.search(r"([A-Z]{2,6})\s+(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})", texto)
            if m:
                resultado["classe_sigla"] = m.group(1)
                resultado["numero"] = m.group(2)

            partes = self._extrair_partes(texto)
            if partes:
                resultado.update(partes)

            return resultado
        finally:
            await self._fechar_aba_autos(aba)

    @staticmethod
    def _extrair_movimentacoes(texto):
        """Extrai movimentacoes do texto da timeline dos autos."""
        movimentacoes = []
        for m in re.finditer(
            r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\(\)\-\./]{8,}?)\n"
            r"(?:(\d+)\s*-\s*([^\n]+)\n)?"
            r"(\d{2}:\d{2})\n"
            r"(\d{1,2}\s+\w{3}\s+\d{4})",
            texto,
        ):
            mov = {
                "tipo": m.group(1).strip().rstrip("."),
                "hora": m.group(4),
                "data": m.group(5),
            }
            if m.group(2):
                mov["id"] = m.group(2)
                mov["descricao"] = m.group(3).strip()
            movimentacoes.append(mov)
        return movimentacoes

    @_serializa
    async def ultimas_movimentacoes(self, numero_cnj, limite=5):
        """Retorna as ultimas N movimentacoes do processo."""
        aba = await self._abrir_autos_processo(numero_cnj)
        try:
            texto = await aba.inner_text("body")
            movimentacoes = self._extrair_movimentacoes(texto)
            return {
                "numero_cnj": numero_cnj,
                "total_encontrado": len(movimentacoes),
                "movimentacoes": movimentacoes[:limite],
            }
        finally:
            await self._fechar_aba_autos(aba)

    @_serializa
    async def relatorio_processo(self, numero_cnj):
        """Relatorio completo: dados + movimentacoes + documentos."""
        aba = await self._abrir_autos_processo(numero_cnj)
        try:
            texto = await aba.inner_text("body")

            relatorio = {
                "numero_cnj": numero_cnj,
                "url_autos": aba.url,
                "processo_de_terceiro": self._ultimo_processo_foi_terceiro,
            }

            m = re.search(r"([A-Z]{2,6})\s+(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})", texto)
            if m:
                relatorio["classe_sigla"] = m.group(1)

            partes = self._extrair_partes(texto)
            if partes:
                relatorio.update(partes)

            movs = self._extrair_movimentacoes(texto)
            relatorio["ultimas_movimentacoes"] = movs[:10]
            relatorio["total_movimentacoes_encontradas"] = len(movs)

            # Documentos principais
            docs = []
            for m in re.finditer(
                r"(\d{7,})\s*-\s*(Petição Inicial|Documentos \([^)]+\)|"
                r"DOCUMENTO COMPROBATÓRIO[^\n]*|Procuração \([^)]+\)|"
                r"Petição \([^)]+\))",
                texto,
            ):
                docs.append({"id": m.group(1), "descricao": m.group(2).strip()})
            relatorio["documentos"] = docs[:20]

            return relatorio
        finally:
            await self._fechar_aba_autos(aba)

    # ========== BUSCA POR DIFERENTES CRITERIOS ==========

    @_serializa
    async def _buscar_por_campo(self, campo, valor, limite=20):
        """Busca processos preenchendo um campo especifico da consulta.

        campo: 'nome_parte', 'nome_advogado', 'cpf', 'cnpj', 'oab'
        valor: str (ou tupla (numero, uf) para OAB)
        """
        _log(f"[BUSCA] Preenchendo {campo}={valor}")
        await self._page.goto(
            f"{URL_BASE}/Processo/ConsultaProcesso/listView.seam",
            wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)

        mapa_seletores = {
            "nome_parte": [
                "input[id*='nomeParte']",
                "input[name*='nomeParte']",
            ],
            "nome_advogado": [
                "input[id*='nomeRepresentante']",
                "input[id*='nomeAdvogado']",
                "input[id*='representante']",
            ],
        }

        preencheu = False
        if campo in ("cpf", "cnpj"):
            # CPF e CNPJ dividem o mesmo campo: fPP:dpDec:documentoParte
            # O radio CPF ja vem marcado por padrao; CNPJ precisa ser clicado
            try:
                if campo == "cnpj":
                    for sel in [
                        "input#cnpj",
                        "input[type='radio'][id='cnpj']",
                    ]:
                        try:
                            await self._page.click(sel, timeout=2000)
                            _log(f"[BUSCA] Radio CNPJ marcado via {sel}")
                            break
                        except Exception:
                            continue
                    await asyncio.sleep(0.5)

                for sel in [
                    "input[id='fPP:dpDec:documentoParte']",
                    "input[id*='documentoParte']",
                    "input[name*='documentoParte']",
                ]:
                    try:
                        el = await self._page.wait_for_selector(sel, timeout=3000)
                        await el.fill(valor)
                        preencheu = True
                        _log(f"[BUSCA] Preencheu {campo} em {sel}")
                        break
                    except Exception:
                        continue
            except Exception as e:
                _log(f"[BUSCA] Erro CPF/CNPJ: {e}")
        elif campo in mapa_seletores:
            for sel in mapa_seletores[campo]:
                try:
                    el = await self._page.wait_for_selector(sel, timeout=3000)
                    await el.fill(valor)
                    preencheu = True
                    _log(f"[BUSCA] Preencheu {campo} em {sel}")
                    break
                except Exception:
                    continue
        elif campo == "oab":
            valor_numero, valor_uf = valor
            try:
                for sel in [
                    "input[id*='numeroOAB']",
                    "input[name*='numeroOAB']",
                ]:
                    try:
                        el = await self._page.wait_for_selector(sel, timeout=2000)
                        await el.fill(str(valor_numero))
                        preencheu = True
                        break
                    except Exception:
                        continue
                for sel in [
                    "select[id*='ufOAB']",
                    "select[id*='ufOABCombo']",
                ]:
                    try:
                        el = await self._page.wait_for_selector(sel, timeout=2000)
                        await el.select_option(value=valor_uf.upper())
                        break
                    except Exception:
                        continue
            except Exception as e:
                _log(f"[BUSCA] Erro OAB: {e}")

        if not preencheu:
            return {"erro": f"Nao consegui preencher campo {campo}", "campo": campo}

        # Pesquisar
        for sel in [
            "button:has-text('Pesquisar')",
            "input[value='Pesquisar']",
            "button[type='submit']",
        ]:
            try:
                await self._page.click(sel, timeout=2000)
                break
            except Exception:
                continue

        await self._page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        # Extrai resultados - filtra so linhas que tem numero CNJ (evita calendario)
        resultados = await self._page.evaluate("""
        () => {
            const regexCnj = /\\d{7}-\\d{2}\\.\\d{4}\\.\\d\\.\\d{2}\\.\\d{4}/;
            const out = [];
            document.querySelectorAll('table tr').forEach(tr => {
                const textoLinha = tr.textContent || '';
                if (!regexCnj.test(textoLinha)) return;
                const celulas = tr.querySelectorAll('td');
                if (celulas.length < 3) return;
                const textos = Array.from(celulas).map(td =>
                    (td.textContent || '').trim().replace(/\\s\\s+/g, ' ')
                );
                out.push({ colunas: textos });
            });
            return out;
        }
        """)

        processados = []
        for r in resultados[:limite]:
            cols = r.get("colunas", [])
            item = {"raw": cols}
            for col in cols:
                m = re.search(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", col)
                if m:
                    item["numero_cnj"] = m.group(0)
                    break
            if len(cols) >= 6:
                item["orgao_julgador"] = cols[2] if len(cols) > 2 else ""
                item["autuado_em"] = cols[3] if len(cols) > 3 else ""
                item["classe"] = cols[4] if len(cols) > 4 else ""
                item["polo_ativo"] = cols[5] if len(cols) > 5 else ""
                item["polo_passivo"] = cols[6] if len(cols) > 6 else ""
                item["ultima_movimentacao"] = cols[7] if len(cols) > 7 else ""
            processados.append(item)

        # Total de resultados
        try:
            pagina_texto = await self._page.inner_text("body")
            m_total = re.search(r"(\d+)\s*resultados?\s*encontrados?", pagina_texto)
            total = int(m_total.group(1)) if m_total else len(processados)
        except Exception:
            total = len(processados)

        return {
            "campo_busca": campo,
            "valor_busca": valor if not isinstance(valor, tuple) else f"{valor[0]}/{valor[1]}",
            "total_encontrados": total,
            "retornados": len(processados),
            "limite_aplicado": limite,
            "resultados": processados,
        }

    async def buscar_por_nome_parte(self, nome, limite=20):
        """Busca processos pelo nome de uma parte (autor/reu)."""
        return await self._buscar_por_campo("nome_parte", nome, limite)

    async def buscar_por_nome_advogado(self, nome, limite=20):
        """Busca processos pelo nome do advogado."""
        return await self._buscar_por_campo("nome_advogado", nome, limite)

    async def buscar_por_cpf(self, cpf, limite=20):
        """Busca processos pelo CPF de uma parte."""
        cpf_limpo = "".join(c for c in cpf if c.isdigit())
        return await self._buscar_por_campo("cpf", cpf_limpo, limite)

    async def buscar_por_cnpj(self, cnpj, limite=20):
        """Busca processos pelo CNPJ de uma parte."""
        cnpj_limpo = "".join(c for c in cnpj if c.isdigit())
        return await self._buscar_por_campo("cnpj", cnpj_limpo, limite)

    async def buscar_por_oab(self, numero_oab, uf="PI", limite=20):
        """Busca processos pelo numero OAB do advogado."""
        return await self._buscar_por_campo("oab", (numero_oab, uf), limite)

    # ========== DOCUMENTOS ==========

    @_serializa
    async def listar_documentos(self, numero_cnj):
        """Lista todos os documentos do processo (id + tipo).

        IMPORTANTE: a arvore lateral do PJe usa lazy-loading. Forcamos scroll
        ate o fim pra carregar TODOS os documentos antes de extrair.
        """
        aba = await self._abrir_autos_processo(numero_cnj)
        try:
            docs = await self._extrair_documentos_da_aba(aba)
            return {
                "numero_cnj": numero_cnj,
                "total": len(docs),
                "documentos": docs,
            }
        finally:
            await self._fechar_aba_autos(aba)

    async def _extrair_documentos_da_aba(self, aba):
        """Forca o lazy-load da arvore e extrai [{id, tipo}, ...] da aba dos autos."""
        # Forca lazy-loading: alem de scrollar containers conhecidos, faz
        # scrollIntoView no ULTIMO doc visivel (gatilho mais confiavel pro
        # infinite-scroll do PJe). So para apos 2 leituras estaveis seguidas.
        _log("[DOC] Forcando lazy-load da arvore...")
        anterior = -1
        estaveis = 0
        for tentativa in range(40):
            atual = await aba.evaluate(
                "() => document.querySelectorAll('[onclick*=\"abrirLinkDocumento\"]').length"
            )
            if atual == anterior:
                estaveis += 1
                if estaveis >= 2:
                    _log(f"[DOC] Estabilizou em {atual} docs apos {tentativa} scrolls")
                    break
            else:
                estaveis = 0
            anterior = atual
            await aba.evaluate("""
                () => {
                    const docs = document.querySelectorAll('[onclick*="abrirLinkDocumento"]');
                    if (docs.length) {
                        docs[docs.length - 1].scrollIntoView({block: 'end'});
                    }
                    const sels = ['#tabPanelDocs', '.scroll-y', '.documentos-panel',
                                  '#documentos', '#divTimeLine', 'aside',
                                  '.barra-de-tarefas'];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el) { el.scrollTop = el.scrollHeight; }
                    }
                    window.scrollTo(0, document.body.scrollHeight);
                }
            """)
            await asyncio.sleep(0.6)

        js_code = """
        () => {
            const resultado = [];
            const vistos = new Set();
            const re = new RegExp('^(\\\\d{6,9})\\\\s*[-–]\\\\s*(.+)$');

            document.querySelectorAll('span.title, a span').forEach(span => {
                const t = (span.textContent || '').trim();
                const m = t.match(re);
                if (m && !vistos.has(m[1])) {
                    vistos.add(m[1]);
                    resultado.push({ id: m[1], tipo: m[2].trim() });
                }
            });

            document.querySelectorAll('[onclick]').forEach(el => {
                const oc = el.getAttribute('onclick') || '';
                const m = oc.match(/abrirLinkDocumento\\\\(['\\"](\\\\d+)['\\"]\\\\)/);
                if (m && !vistos.has(m[1])) {
                    vistos.add(m[1]);
                    let tipo = 'desconhecido';
                    const parent = el.closest('.media-body, div');
                    if (parent) {
                        const span = parent.querySelector('span.title, span');
                        if (span) tipo = span.textContent.trim().replace(/^\\\\d+\\\\s*[-–]\\\\s*/, '');
                    }
                    resultado.push({ id: m[1], tipo: tipo });
                }
            });

            return resultado;
        }
        """

        try:
            docs = await aba.evaluate(js_code)
        except Exception as e:
            _log(f"[DOC] Erro no JS: {e}")
            docs = []

        if not docs:
            _log(f"[DOC] JS vazio, tentando fallback com regex no HTML")
            html = await aba.content()
            vistos = set()
            for m in re.finditer(r"abrirLinkDocumento\(['\"](\d{6,9})['\"]\)", html):
                if m.group(1) not in vistos:
                    vistos.add(m.group(1))
                    docs.append({"id": m.group(1), "tipo": "(tipo nao extraido)"})

        return docs

    def _url_documento_rest(self, documento_id):
        """URL REST de download direto de um documento (mesma da arvore)."""
        return (
            f"{URL_BASE}/seam/resource/rest/pje-legacy/documento/download/"
            f"TJPI/1g/{self._ultimo_processo_id}/{documento_id}"
        )

    async def _ler_documento_rest(self, numero_cnj, id_documento, max_paginas=30):
        """Le o teor de um documento via endpoint REST (sem abrir nova aba).

        Requer que os autos do processo ja tenham sido abertos nesta sessao
        (pra _ultimo_processo_id estar setado). E o mesmo endpoint que o
        abrirLinkDocumento da arvore abre - aqui baixamos direto via
        APIRequestContext, evitando nova aba + carga dupla da page.
        """
        url_doc = self._url_documento_rest(id_documento)
        _log(f"[DOC] GET {url_doc}")

        resultado = {
            "id_documento": str(id_documento),
            "numero_cnj": numero_cnj,
            "url": url_doc,
        }

        resp = await self._context.request.get(url_doc, timeout=60000)
        if not resp.ok:
            resultado["erro"] = f"Download falhou: HTTP {resp.status}"
            return resultado

        corpo = await resp.body()
        content_type = resp.headers.get("content-type", "")
        is_pdf = "pdf" in content_type.lower() or corpo[:5] == b"%PDF-"

        if is_pdf:
            resultado["formato"] = "pdf"
            try:
                with pdfplumber.open(io.BytesIO(corpo)) as pdf:
                    total_paginas = len(pdf.pages)
                    paginas = []
                    for page in pdf.pages[:max_paginas]:
                        paginas.append(page.extract_text() or "")
                    resultado["texto"] = "\n\n".join(paginas)
                    resultado["num_paginas"] = total_paginas
                    if total_paginas > max_paginas:
                        # Sinaliza truncamento EXPLICITAMENTE - analise juridica
                        # sobre teor incompleto sem aviso e' inaceitavel.
                        resultado["truncado"] = True
                        resultado["paginas_extraidas"] = max_paginas
                        resultado["aviso"] = (
                            f"ATENCAO: documento tem {total_paginas} paginas, mas "
                            f"apenas as {max_paginas} primeiras foram extraidas. "
                            f"Pra teor completo use baixar_documento e leia o PDF."
                        )
            except Exception as e:
                resultado["erro"] = f"Falhou ao extrair PDF: {e}"
        else:
            resultado["formato"] = "html"
            try:
                resultado["texto"] = _html_para_texto(
                    corpo.decode("utf-8", errors="replace")
                )
            except Exception as e:
                resultado["erro"] = f"Falhou ao extrair HTML: {e}"

        return resultado

    @_serializa
    async def ler_documento(self, numero_cnj, id_documento, max_paginas=30):
        """Le o teor de um documento especifico (HTML ou PDF)."""
        cnj_digitos = re.sub(r"\D", "", numero_cnj)
        aba = None
        # So reabre os autos se o processo_id em cache nao for DESTE processo
        if not self._ultimo_processo_id or self._ultimo_processo_cnj != cnj_digitos:
            aba = await self._abrir_autos_processo(numero_cnj)
        try:
            return await self._ler_documento_rest(numero_cnj, id_documento, max_paginas)
        finally:
            await self._fechar_aba_autos(aba)

    @_serializa
    async def ler_documento_filtrado(self, numero_cnj, padrao_tipo, max_paginas=30):
        """Abre os autos UMA vez, lista os docs, escolhe o de maior ID cujo
        tipo casa com padrao_tipo (regex case-insensitive) e le o teor.

        Substitui o fluxo antigo listar_documentos + ler_documento dos tools
        ultima_decisao/ultimo_despacho, que abria os autos DUAS vezes.
        """
        aba = await self._abrir_autos_processo(numero_cnj)
        try:
            docs = await self._extrair_documentos_da_aba(aba)
            regex = re.compile(padrao_tipo, re.IGNORECASE)
            filtrados = [d for d in docs if regex.search(d.get("tipo", ""))]
            if not filtrados:
                return {
                    "encontrado": False,
                    "numero_cnj": numero_cnj,
                    "total_documentos": len(docs),
                }
            alvo = max(filtrados, key=lambda d: int(d["id"]))
            teor = await self._ler_documento_rest(numero_cnj, alvo["id"], max_paginas)
            return {
                "encontrado": True,
                "tipo_documento": alvo["tipo"],
                "documento_id": alvo["id"],
                **teor,
            }
        finally:
            await self._fechar_aba_autos(aba)

    @_serializa
    async def garantir_processo_id(self, numero_cnj):
        """Garante que _ultimo_processo_id pertence a ESTE processo.

        Abre (e fecha) os autos se necessario. Usado pelo downloader antes
        de montar URLs REST - evita usar um processo_id que sobrou de outro
        processo consultado antes na mesma sessao do singleton.
        """
        cnj_digitos = re.sub(r"\D", "", numero_cnj)
        if self._ultimo_processo_id and self._ultimo_processo_cnj == cnj_digitos:
            return self._ultimo_processo_id
        aba = await self._abrir_autos_processo(numero_cnj)
        await self._fechar_aba_autos(aba)
        if not self._ultimo_processo_id:
            raise RuntimeError(
                f"Nao consegui extrair processo_id da URL dos autos de {numero_cnj}"
            )
        return self._ultimo_processo_id

    # ========== DOWNLOAD NATIVO DOS AUTOS COMPLETOS ==========

    @_serializa
    async def baixar_processo_nativo(
        self,
        numero_cnj,
        caminho_destino,
        tipo_documento="",
        id_inicial="",
        id_final="",
        periodo_inicio="",
        periodo_fim="",
        cronologia="decrescente",
        incluir_expediente=False,
        incluir_movimentos=False,
        timeout_download_ms=120000,
    ):
        """
        VERSAO NOVA com seletores reais capturados via DevTools/captura passiva.

        Fluxo:
        1. Abre autos do processo
        2. Clica no <a class='btn-menu-abas dropdown-toggle' title='Download autos
           do processo'> que abre o dropdown
        3. (Opcional) altera selects dentro do dialog
        4. Clica em <input value='Download'> que dispara A4J.AJAX.Submit
        5. expect_download captura o ZIP gerado em S3 (~19s)
        6. Extrai o PDF interno do ZIP e salva no caminho_destino
        """
        return await self._baixar_processo_nativo_v2(
            numero_cnj=numero_cnj,
            caminho_destino=caminho_destino,
            tipo_documento=tipo_documento,
            id_inicial=id_inicial,
            id_final=id_final,
            periodo_inicio=periodo_inicio,
            periodo_fim=periodo_fim,
            cronologia=cronologia,
            incluir_expediente=incluir_expediente,
            incluir_movimentos=incluir_movimentos,
            timeout_download_ms=timeout_download_ms,
        )

    async def _baixar_processo_nativo_v2(
        self,
        numero_cnj,
        caminho_destino,
        tipo_documento="",
        id_inicial="",
        id_final="",
        periodo_inicio="",
        periodo_fim="",
        cronologia="decrescente",
        incluir_expediente=False,
        incluir_movimentos=False,
        timeout_download_ms=120000,
    ):
        """Implementacao real - separada pra manter compat antiga em scripts.

        Observacao: o PJe-TJPI nao serve o PDF como attachment - ele abre uma
        nova aba apontando pra uma URL pre-assinada do S3
        (storagepje.tjpi.jus.br/.../processo.pdf?X-Amz-...&X-Amz-Expires=120).
        Por isso 'expect_download' nao dispara. Capturamos a URL via listener
        de request e baixamos via APIRequestContext dentro da janela de 120s.
        """
        from pathlib import Path

        caminho_destino = Path(caminho_destino)
        caminho_destino.parent.mkdir(parents=True, exist_ok=True)

        aba = await self._abrir_autos_processo(numero_cnj)

        # 1. Clica no trigger do dropdown (o proprio click espera o elemento)
        _log("[NATIVO] Clicando no trigger do dropdown...")
        try:
            await aba.click(
                "a.btn-menu-abas.dropdown-toggle[title='Download autos do processo']",
                timeout=5000,
            )
        except Exception:
            # Fallback
            await aba.click("li.filtros-download a.dropdown-toggle", timeout=3000)

        # 2. Espera o dropdown abrir (aria-expanded=true / class 'open')
        await aba.wait_for_selector("li.filtros-download.open", timeout=5000)
        _log("[NATIVO] Dropdown aberto")
        await asyncio.sleep(0.5)  # JS renderizar campos

        # 3. Cronologia (Select2 do PJe usa IDs especificos). Default do PJe = DESC.
        #    Os campos sao Select2: precisa setar .value E disparar 'change' via jQuery.
        if cronologia.lower().startswith("cres"):
            valor_cronologia = "ASC"
        else:
            valor_cronologia = "DESC"

        try:
            ok = await aba.evaluate(f"""
                () => {{
                    const sel = document.getElementById('navbar:cbCronologia');
                    if (!sel) return 'select-nao-encontrado';
                    sel.value = '{valor_cronologia}';
                    if (window.jQuery) {{
                        window.jQuery(sel).trigger('change');
                        return 'jquery-change';
                    }}
                    sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return 'native-change';
                }}
            """)
            _log(f"[NATIVO] Cronologia={valor_cronologia} via {ok}")
        except Exception as e:
            _log(f"[NATIVO] Aviso ao setar cronologia: {e}")

        # 4. Tipo de documento (best effort - precisa saber o valor exato)
        if tipo_documento:
            try:
                await aba.evaluate(f"""
                    () => {{
                        const sel = document.getElementById('navbar:cbTipoDocumento');
                        if (!sel) return false;
                        for (const opt of sel.options) {{
                            if (opt.text.toLowerCase().includes('{tipo_documento.lower()}')) {{
                                sel.value = opt.value;
                                if (window.jQuery) window.jQuery(sel).trigger('change');
                                else sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                _log(f"[NATIVO] Tipo documento={tipo_documento} setado")
            except Exception as e:
                _log(f"[NATIVO] Aviso tipo documento: {e}")

        # 5. Incluir expediente / movimentos (Select2 com 'Sim'/'Não')
        if incluir_expediente:
            try:
                await aba.evaluate("""
                    () => {
                        const sel = document.getElementById('navbar:cbExpediente');
                        if (!sel) return false;
                        for (const opt of sel.options) {
                            if (opt.text.toLowerCase().trim() === 'sim') {
                                sel.value = opt.value;
                                if (window.jQuery) window.jQuery(sel).trigger('change');
                                return true;
                            }
                        }
                        return false;
                    }
                """)
            except Exception:
                pass

        if incluir_movimentos:
            try:
                await aba.evaluate("""
                    () => {
                        const sel = document.getElementById('navbar:cbMovimentos');
                        if (!sel) return false;
                        for (const opt of sel.options) {
                            if (opt.text.toLowerCase().trim() === 'sim') {
                                sel.value = opt.value;
                                if (window.jQuery) window.jQuery(sel).trigger('change');
                                return true;
                            }
                        }
                        return false;
                    }
                """)
            except Exception:
                pass

        # 6. Range de IDs e periodo (inputs simples, nao Select2)
        if id_inicial:
            try:
                await aba.fill("input[name*='idInicial'], input[id*='idInicial']", str(id_inicial), timeout=2000)
            except Exception:
                pass
        if id_final:
            try:
                await aba.fill("input[name*='idFinal'], input[id*='idFinal']", str(id_final), timeout=2000)
            except Exception:
                pass
        if periodo_inicio:
            try:
                await aba.fill("input[name*='periodoInicio']", periodo_inicio, timeout=2000)
            except Exception:
                pass
        if periodo_fim:
            try:
                await aba.fill("input[name*='periodoFim']", periodo_fim, timeout=2000)
            except Exception:
                pass

        # 7. Listener pra capturar a URL S3 assim que o PJe a gerar.
        #    O PJe abre o PDF numa nova aba apontando pra storagepje.tjpi.jus.br
        #    com URL pre-assinada (X-Amz-Expires=120s). Capturamos no on('request').
        loop = asyncio.get_event_loop()
        url_future = loop.create_future()
        aba_pdf_future = loop.create_future()

        def _on_request(request):
            url = request.url
            if (
                not url_future.done()
                and "storagepje.tjpi.jus.br" in url
                and "processo.pdf" in url
                and "X-Amz-Signature" in url
            ):
                url_future.set_result(url)

        def _on_page(page):
            if not aba_pdf_future.done():
                aba_pdf_future.set_result(page)

        self._context.on("request", _on_request)
        self._context.on("page", _on_page)

        try:
            # 8. Clica DOWNLOAD - dispara AJAX, servidor gera PDF (~10-30s) e
            #    abre nova aba apontando pra URL S3.
            _log("[NATIVO] Clicando DOWNLOAD - servidor vai gerar PDF...")
            for sel in [
                "#navbar\\:botoesDownload input.btn-primary[value='Download']",
                "li.filtros-download .dropdown-menu input.btn-primary[value='Download']",
                "input.btn-primary[value='Download']",
                "input[value='Download'][type='button']",
            ]:
                try:
                    await aba.click(sel, timeout=3000)
                    _log(f"[NATIVO] DOWNLOAD clicado via {sel}")
                    break
                except Exception:
                    continue

            # 9. Aguarda a URL S3 aparecer (timeout = janela de validade do PJe)
            try:
                pdf_url = await asyncio.wait_for(
                    url_future, timeout=timeout_download_ms / 1000
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "Timeout aguardando URL S3 do PJe. O servidor nao gerou o PDF "
                    f"em {timeout_download_ms/1000:.0f}s ou os seletores do modal "
                    "mudaram (verificar btn-primary[value='Download'])."
                )

            _log(f"[NATIVO] URL S3 capturada: {pdf_url[:120]}...")

            # 10. Baixa via APIRequestContext (mesmos cookies/proxy da sessao).
            #     A URL pre-assinada e' valida por ~120s, entao baixamos imediatamente.
            response = await self._context.request.get(pdf_url, timeout=60000)
            if response.status != 200:
                raise RuntimeError(
                    f"Download S3 falhou: HTTP {response.status} - {await response.text()}"
                )
            corpo = await response.body()
            caminho_destino.write_bytes(corpo)
            _log(
                f"[NATIVO] PDF salvo: {caminho_destino.name} "
                f"({len(corpo)/1024/1024:.2f} MB)"
            )

            # 11. Fecha aba do PDF (se abriu) pra nao acumular.
            if aba_pdf_future.done():
                try:
                    await aba_pdf_future.result().close()
                except Exception:
                    pass
        finally:
            try:
                self._context.remove_listener("request", _on_request)
                self._context.remove_listener("page", _on_page)
            except Exception:
                pass
            await self._fechar_aba_autos(aba)

        tamanho_pdf = caminho_destino.stat().st_size
        return {
            "caminho": str(caminho_destino),
            "tamanho_bytes": tamanho_pdf,
            "tamanho_mb": round(tamanho_pdf / 1024 / 1024, 2),
            "metodo": "nativo",
            "filtros_aplicados": {
                "tipo_documento": tipo_documento or None,
                "cronologia": cronologia,
                "incluir_expediente": incluir_expediente,
                "incluir_movimentos": incluir_movimentos,
            },
        }
