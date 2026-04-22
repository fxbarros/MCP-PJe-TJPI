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
import io
import re
import time

import pdfplumber
import pyotp
from playwright.async_api import async_playwright

URL_BASE = "https://pje.tjpi.jus.br/1g"


class PJeClient:
    """Cliente para automatizar consultas ao PJe-TJPI - 1o Grau."""

    def __init__(self, cpf, senha, totp_seed, persona="advogado", headless=True):
        """Inicializa o cliente.

        Args:
            cpf: CPF do usuario (so numeros)
            senha: Senha do PDPJ
            totp_seed: Seed TOTP em Base32 (gerada ao configurar 2FA)
            persona: "advogado" (default) ou "procurador"
            headless: True para rodar sem interface, False para visualizar
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
        self._ultimo_processo_foi_terceiro = False

    async def __aenter__(self):
        await self._iniciar()
        await self._login()
        await self._trocar_perfil()
        return self

    async def __aexit__(self, *args):
        await self._fechar()

    # ========== SETUP / TEARDOWN ==========

    async def _iniciar(self):
        """Inicia o browser Playwright."""
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
            print(f"[DIALOG] Aceitando: {msg[:100]}...")
            # Resolucao CNJ 121 = processo de terceiro
            if "Resolução" in msg or "121" in msg or "não faz parte" in msg:
                self._ultimo_processo_foi_terceiro = True
            await dialog.accept()
        page.on("dialog", handle_dialog)

    async def _fechar(self):
        """Fecha browser e Playwright."""
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ========== LOGIN E TROCA DE PERFIL ==========

    def _codigo_totp(self):
        """Gera codigo TOTP, esperando se estiver perto de virar ciclo."""
        restante = self.totp.interval - (int(time.time()) % self.totp.interval)
        if restante < 3:
            time.sleep(restante + 1)
        return self.totp.now()

    async def _login(self):
        """Faz login no SSO do CNJ com CPF + senha + TOTP."""
        print("[LOGIN] Iniciando...")
        await self._page.goto(f"{URL_BASE}/login.seam", wait_until="networkidle")

        # CPF + senha
        await self._page.wait_for_selector("input#username", timeout=15000)
        await self._page.fill("input#username", self.cpf)
        await self._page.fill("input#password", self.senha)
        try:
            await self._page.click("input#kc-login", timeout=3000)
        except Exception:
            await self._page.click("button[type='submit']")
        await self._page.wait_for_load_state("networkidle", timeout=15000)

        # 2FA
        codigo = self._codigo_totp()
        await self._page.wait_for_selector("input[type='text']", timeout=15000)
        await self._page.fill("input[type='text']", codigo)
        try:
            await self._page.click("input#kc-login", timeout=2000)
        except Exception:
            await self._page.click("button[type='submit']")
        await self._page.wait_for_load_state("networkidle", timeout=30000)
        print("[LOGIN] OK")

    async def _trocar_perfil(self):
        """Abre o dropdown do usuario e troca para a persona desejada."""
        print(f"[PERFIL] Trocando para perfil: {self.persona}")
        await asyncio.sleep(2)

        # Abre o dropdown do menu do usuario
        try:
            await self._page.click("li.menu-usuario a.dropdown-toggle", timeout=5000)
        except Exception:
            await self._page.click("a.dropdown-toggle", timeout=3000)
        await asyncio.sleep(1)

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
                await self._page.click(sel, timeout=5000)
                clicou = True
                break
            except Exception:
                continue

        if not clicou:
            raise Exception(f"Nao consegui trocar para perfil {self.persona}")

        await self._page.wait_for_load_state("networkidle", timeout=15000)
        print(f"[PERFIL] OK - persona: {self.persona}")

    # ========== EXPEDIENTES PENDENTES ==========

    async def expedientes_pendentes(self):
        """Lista expedientes pendentes de ciencia/resposta."""
        print("[EXP] Navegando pro painel...")
        await self._page.goto(
            f"{URL_BASE}/Painel/painel_usuario/advogado.seam",
            wait_until="networkidle"
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
        print(f"[PROC] Buscando {numero_cnj}...")
        await self._page.goto(
            f"{URL_BASE}/Processo/ConsultaProcesso/listView.seam",
            wait_until="networkidle"
        )
        await asyncio.sleep(2)

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
        print(f"[PROC] Partes do numero: {partes}")

        # PJe-TJPI tem 6 campos de input; os campos J e TR ja vem preenchidos
        inputs = await self._page.query_selector_all(
            "input[id*='numeroProcesso']"
        )
        print(f"[PROC] Campos encontrados: {len(inputs)}")

        if len(inputs) >= 6:
            await inputs[0].fill(partes[0])  # N: 0801447
            await inputs[1].fill(partes[1])  # DD: 61
            await inputs[2].fill(partes[2])  # AAAA: 2024
            # Pula indice 3 (J) e 4 (TR) - ja vem preenchidos
            await inputs[5].fill(partes[4])  # OOOO: 0037
            print(f"[PROC] Preencheu: N={partes[0]} DD={partes[1]} AAAA={partes[2]} OOOO={partes[4]}")
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

        await self._page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        # Clica no link do processo no resultado
        try:
            link = await self._page.wait_for_selector(
                f"a:has-text('{numero_cnj}'), a:has-text('{partes[0]}')",
                timeout=5000
            )
            await link.click()
        except Exception:
            links = await self._page.query_selector_all("table a")
            if links:
                await links[0].click()
            else:
                raise Exception(f"Processo {numero_cnj} nao encontrado")

        await asyncio.sleep(3)

        # Autos podem abrir em nova aba - pega a ultima
        paginas = self._context.pages
        aba_autos = paginas[-1]
        await aba_autos.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)
        print(f"[PROC] Autos abertos em: {aba_autos.url}")
        return aba_autos

    async def buscar_processo(self, numero_cnj):
        """Retorna dados basicos de um processo pelo CNJ."""
        aba = await self._abrir_autos_processo(numero_cnj)
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

        # Partes
        m = re.search(
            r"(?:[A-Z]{2,6}\s+\d{7}-\d{2}[\d.]+\s*)(.+?\s+X\s+.+?)(?=\s*\n|\s*Ícone)",
            texto, re.DOTALL
        )
        if m:
            resultado["partes"] = m.group(1).strip()

        if len(self._context.pages) > 1:
            try:
                await aba.close()
            except Exception:
                pass
        return resultado

    async def ultimas_movimentacoes(self, numero_cnj, limite=5):
        """Retorna as ultimas N movimentacoes do processo."""
        aba = await self._abrir_autos_processo(numero_cnj)
        texto = await aba.inner_text("body")

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

        if len(self._context.pages) > 1:
            try:
                await aba.close()
            except Exception:
                pass

        return {
            "numero_cnj": numero_cnj,
            "total_encontrado": len(movimentacoes),
            "movimentacoes": movimentacoes[:limite],
        }

    async def relatorio_processo(self, numero_cnj):
        """Relatorio completo: dados + movimentacoes + documentos."""
        aba = await self._abrir_autos_processo(numero_cnj)
        texto = await aba.inner_text("body")

        relatorio = {
            "numero_cnj": numero_cnj,
            "url_autos": aba.url,
            "processo_de_terceiro": self._ultimo_processo_foi_terceiro,
        }

        m = re.search(r"([A-Z]{2,6})\s+(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})", texto)
        if m:
            relatorio["classe_sigla"] = m.group(1)

        m = re.search(
            r"(?:[A-Z]{2,6}\s+\d{7}-\d{2}[\d.]+\s*)(.+?\s+X\s+.+?)(?=\s*\n|\s*Ícone)",
            texto, re.DOTALL
        )
        if m:
            relatorio["partes"] = m.group(1).strip()

        # Movimentacoes (10 ultimas)
        movs = []
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
            movs.append(mov)
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

        if len(self._context.pages) > 1:
            try:
                await aba.close()
            except Exception:
                pass
        return relatorio

    # ========== BUSCA POR DIFERENTES CRITERIOS ==========

    async def _buscar_por_campo(self, campo, valor, limite=20):
        """Busca processos preenchendo um campo especifico da consulta.

        campo: 'nome_parte', 'nome_advogado', 'cpf', 'cnpj', 'oab'
        valor: str (ou tupla (numero, uf) para OAB)
        """
        print(f"[BUSCA] Preenchendo {campo}={valor}")
        await self._page.goto(
            f"{URL_BASE}/Processo/ConsultaProcesso/listView.seam",
            wait_until="networkidle"
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
                            print(f"[BUSCA] Radio CNPJ marcado via {sel}")
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
                        print(f"[BUSCA] Preencheu {campo} em {sel}")
                        break
                    except Exception:
                        continue
            except Exception as e:
                print(f"[BUSCA] Erro CPF/CNPJ: {e}")
        elif campo in mapa_seletores:
            for sel in mapa_seletores[campo]:
                try:
                    el = await self._page.wait_for_selector(sel, timeout=3000)
                    await el.fill(valor)
                    preencheu = True
                    print(f"[BUSCA] Preencheu {campo} em {sel}")
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
                print(f"[BUSCA] Erro OAB: {e}")

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

        await self._page.wait_for_load_state("networkidle", timeout=20000)
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

    async def listar_documentos(self, numero_cnj):
        """Lista todos os documentos do processo (id + tipo)."""
        aba = await self._abrir_autos_processo(numero_cnj)
        await asyncio.sleep(2)

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
            print(f"[DOC] Erro no JS: {e}")
            docs = []

        if not docs:
            print("[DOC] JS vazio, tentando fallback com regex no HTML")
            html = await aba.content()
            vistos = set()
            for m in re.finditer(r"abrirLinkDocumento\(['\"](\d{6,9})['\"]\)", html):
                if m.group(1) not in vistos:
                    vistos.add(m.group(1))
                    docs.append({"id": m.group(1), "tipo": "(tipo nao extraido)"})

        if len(self._context.pages) > 1:
            try:
                await aba.close()
            except Exception:
                pass

        return {
            "numero_cnj": numero_cnj,
            "total": len(docs),
            "documentos": docs,
        }

    async def ler_documento(self, numero_cnj, id_documento):
        """Le o teor de um documento especifico (HTML ou PDF)."""
        aba = await self._abrir_autos_processo(numero_cnj)
        await asyncio.sleep(2)

        print(f"[DOC] Abrindo documento {id_documento}...")
        async with self._context.expect_page(timeout=30000) as nova_aba_info:
            await aba.evaluate(f"abrirLinkDocumento('{id_documento}')")

        nova_aba = await nova_aba_info.value
        await nova_aba.wait_for_load_state("domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        url_doc = nova_aba.url
        print(f"[DOC] URL: {url_doc}")

        resultado = {
            "id_documento": id_documento,
            "numero_cnj": numero_cnj,
            "url": url_doc,
        }

        # Detecta HTML vs PDF
        content_type = ""
        try:
            response = await nova_aba.goto(url_doc, wait_until="domcontentloaded")
            if response:
                content_type = response.headers.get("content-type", "")
        except Exception:
            pass

        is_pdf = "pdf" in content_type.lower() or url_doc.lower().endswith(".pdf")

        if is_pdf:
            try:
                resp = await self._context.request.get(url_doc)
                pdf_bytes = await resp.body()
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    paginas = []
                    for page in pdf.pages[:30]:  # Limita 30 paginas
                        txt = page.extract_text() or ""
                        paginas.append(txt)
                    texto = "\n\n".join(paginas)
                    resultado["num_paginas"] = len(pdf.pages)
                resultado["formato"] = "pdf"
                resultado["texto"] = texto
            except Exception as e:
                resultado["formato"] = "pdf"
                resultado["erro"] = f"Falhou ao extrair PDF: {e}"
        else:
            try:
                texto = await nova_aba.inner_text("body")
                resultado["formato"] = "html"
                resultado["texto"] = texto.strip()
            except Exception as e:
                resultado["erro"] = f"Falhou ao extrair HTML: {e}"

        try:
            await nova_aba.close()
        except Exception:
            pass

        return resultado
