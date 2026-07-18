"""Download de documentos e processos do PJe-TJPI (1o e 2o graus).

Primitivas:
- baixar_documento_bytes: baixa 1 doc como bytes (PDF ou HTML)
- salvar_documento: salva 1 doc na pasta do processo

Constantes:
- _PASTAS: ~/Library/Mobile Documents/.../Processos TJPI {1,2} Grau/ (por grau)
- LIMITE_PDF_DIRETO_MB: 18 MB (acima vai pro NotebookLM)
"""
import asyncio
import re
import sys
import time
from pathlib import Path

LIMITE_PDF_DIRETO_MB = 18

# Pastas separadas por grau: o MESMO numero CNJ existe no 1g e no 2g
# (a apelacao herda o numero da origem) - misturar causaria cache-hit errado.
_PASTAS = {
    "1g": Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/Processos TJPI 1 Grau",
    "2g": Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/Processos TJPI 2 Grau",
}
PASTA_PROCESSOS = _PASTAS["1g"]  # retrocompatibilidade


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cnj_safe(numero_cnj: str) -> str:
    """Normaliza CNJ pra usar como nome de pasta/arquivo (sem caracteres problematicos)."""
    return re.sub(r"[^\w.-]", "_", numero_cnj.strip())


def pasta_processo(numero_cnj: str, grau: str = "1g") -> Path:
    """Retorna a pasta do processo no iCloud (por grau), criando se nao existir."""
    p = _PASTAS[grau] / cnj_safe(numero_cnj)
    p.mkdir(parents=True, exist_ok=True)
    return p


def url_documento(processo_id: str, documento_id: str, grau: str = "1g") -> str:
    """Monta a URL de download direto de um documento."""
    return (
        f"https://pje.tjpi.jus.br/{grau}/seam/resource/rest/pje-legacy/"
        f"documento/download/TJPI/{grau}/{processo_id}/{documento_id}"
    )


async def baixar_documento_bytes(client, numero_cnj: str, documento_id: str) -> dict:
    """Baixa 1 documento como bytes usando a sessao autenticada do PJeClient.

    Garante que o processo_id em cache pertence a ESTE processo (com o
    singleton, o id pode ter sobrado de outro processo consultado antes -
    usar o id errado baixaria documento de outro processo ou daria 404).
    Retorna dict com bytes, content_type, tamanho, formato.
    """
    processo_id = await client.garantir_processo_id(numero_cnj)

    url = url_documento(processo_id, str(documento_id), client.grau)
    _log(f"[BAIXA] GET {url}")

    resp = await client._context.request.get(url)
    if not resp.ok:
        raise RuntimeError(
            f"Download falhou: HTTP {resp.status} pro doc {documento_id}"
        )

    body = await resp.body()
    content_type = resp.headers.get("content-type", "")
    is_pdf = "pdf" in content_type.lower()

    return {
        "bytes": body,
        "content_type": content_type,
        "tamanho_bytes": len(body),
        "formato": "pdf" if is_pdf else "html",
    }


async def salvar_documento(
    client, numero_cnj: str, documento_id: str, tipo_descritivo: str = ""
) -> dict:
    """Baixa e salva 1 documento na pasta do processo.

    Salva em: Processos TJPI 1 Grau/{cnj}/documentos/{id}{-tipo}.{ext}
    Retorna dict com caminho, tamanho, formato.
    """
    info = await baixar_documento_bytes(client, numero_cnj, documento_id)

    pasta = pasta_processo(numero_cnj, client.grau) / "documentos"
    pasta.mkdir(parents=True, exist_ok=True)

    ext = "pdf" if info["formato"] == "pdf" else "html"
    nome_seguro = re.sub(r"[^\w-]+", "_", tipo_descritivo)[:60].strip("_")
    nome = f"{documento_id}-{nome_seguro}.{ext}" if nome_seguro else f"{documento_id}.{ext}"
    caminho = pasta / nome

    caminho.write_bytes(info["bytes"])
    _log(f"[BAIXA] Salvo {caminho.name} ({info['tamanho_bytes']/1024:.1f} KB)")

    return {
        "numero_cnj": numero_cnj,
        "documento_id": str(documento_id),
        "caminho": str(caminho),
        "tamanho_bytes": info["tamanho_bytes"],
        "tamanho_kb": round(info["tamanho_bytes"] / 1024, 1),
        "formato": info["formato"],
    }


async def baixar_processo_doc_a_doc(
    client,
    numero_cnj: str,
    ordem: str = "decrescente",
    limite: int = 0,
) -> dict:
    """Baixa o processo iterando documentos da arvore.

    Estrategia simples e confiavel: lista docs + faz HTTP request direto
    em cada um (sem precisar reabrir autos). Mais lento que o nativo,
    mas robusto e ja validado.

    ordem: 'decrescente' (mais recente primeiro) ou 'crescente'
    limite: 0 = todos; N = so os primeiros N apos ordenar
    """
    # listar_documentos JA abre os autos internamente e captura processo_id.
    # Nao chamar _abrir_autos_processo explicitamente aqui (evita reabertura redundante).
    _log(f"[BAIXA-DOC] Listando documentos do processo...")
    listagem = await client.listar_documentos(numero_cnj)
    docs = listagem.get("documentos", [])

    # Ordena por ID (numerico) - PJe usa IDs sequenciais
    docs = sorted(docs, key=lambda d: int(d["id"]),
                  reverse=ordem.lower().startswith("decres"))

    if limite and limite > 0:
        docs = docs[:limite]

    _log(f"[BAIXA-DOC] Iniciando download de {len(docs)} docs (ordem={ordem})")

    salvos = []
    erros = []
    for i, d in enumerate(docs):
        try:
            r = await salvar_documento(client, numero_cnj, d["id"], d["tipo"])
            salvos.append(r)
            _log(f"[BAIXA-DOC] {i+1}/{len(docs)} OK: {d['id']} ({r['tamanho_kb']} KB, {r['formato']})")
        except Exception as e:
            erros.append({"id": d["id"], "tipo": d["tipo"], "erro": str(e)})
            _log(f"[BAIXA-DOC] {i+1}/{len(docs)} FAIL: {d['id']} - {e}")

    # Consolida PDFs em um unico arquivo.
    # IMPORTANTE: NUNCA escrever no caminho de cache {cnj}.pdf - esse nome e'
    # reservado pro download nativo (sempre completo). O doc_a_doc pode ser
    # parcial (limite=N ou teto do lazy-load), e um {cnj}.pdf parcial seria
    # servido como "autos completos" em cache-hits futuros do preparar_processo.
    pdfs = [s for s in salvos if s.get("formato") == "pdf"]
    consolidado = None
    if len(pdfs) >= 1:
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for p in pdfs:
                writer.append(p["caminho"])
            sufixo = f" (parcial {len(pdfs)} docs)" if limite else " (doc_a_doc)"
            destino = pasta_processo(numero_cnj, client.grau) / f"{cnj_safe(numero_cnj)}{sufixo}.pdf"
            with open(destino, "wb") as f:
                writer.write(f)
            tamanho = destino.stat().st_size
            consolidado = {
                "caminho": str(destino),
                "tamanho_bytes": tamanho,
                "tamanho_mb": round(tamanho / 1024 / 1024, 2),
                "num_pdfs_concatenados": len(pdfs),
            }
            _log(
                f"[BAIXA-DOC] Consolidado: {destino.name} "
                f"({consolidado['tamanho_mb']} MB, {len(pdfs)} PDFs)"
            )
        except Exception as e:
            _log(f"[BAIXA-DOC] Falha ao consolidar: {e}")

    return {
        "numero_cnj": numero_cnj,
        "metodo": "doc_a_doc",
        "total_documentos": len(docs),
        "baixados": len(salvos),
        "erros": len(erros),
        "pdf_consolidado": consolidado,
        "documentos_salvos": [
            {
                "id": s["documento_id"],
                "caminho": s["caminho"],
                "formato": s["formato"],
                "tamanho_kb": s["tamanho_kb"],
            }
            for s in salvos
        ],
        "erros_detalhados": erros if erros else None,
    }


async def baixar_processo_completo(
    client,
    numero_cnj: str,
    tipo_documento: str = "",
    id_inicial: str = "",
    id_final: str = "",
    periodo_inicio: str = "",
    periodo_fim: str = "",
    cronologia: str = "decrescente",
    incluir_expediente: bool = True,
    incluir_movimentos: bool = True,
    forcar: bool = False,
    metodo: str = "nativo",
    limite: int = 0,
) -> dict:
    """Baixa o processo completo.

    metodo='nativo' (default, recomendado): consolida no servidor PJe,
        baixa em UMA requisicao do S3 pre-assinado. Inclui capa/indice e
        expediente/movimentos. Sempre completo.
    metodo='doc_a_doc' (alternativo): itera documentos da arvore e
        concatena. Util quando voce quer os arquivos individuais separados
        em Processos TJPI 1 Grau/{cnj}/documentos/. Limitacao: depende do
        listar_documentos, que tem bug de paginacao em processos com >30 docs.

    Salva docs individuais em Processos TJPI 1 Grau/{cnj}/documentos/
        (apenas no modo doc_a_doc).
    Salva PDF consolidado em Processos TJPI 1 Grau/{cnj}/{cnj}.pdf
        (apenas no modo nativo - doc_a_doc usa sufixo proprio).
    """
    if metodo not in ("nativo", "doc_a_doc"):
        # Sem validacao, qualquer typo caia silenciosamente no doc_a_doc
        raise ValueError(
            f"Metodo invalido: {metodo!r}. Use 'nativo' ou 'doc_a_doc'."
        )

    pasta = pasta_processo(numero_cnj, client.grau)
    consolidado_path = pasta / f"{cnj_safe(numero_cnj)}.pdf"

    # Cache hit - SO vale pro metodo nativo SEM filtros: o {cnj}.pdf em cache
    # e' sempre os autos completos. Pro doc_a_doc (que gera individuais) ou
    # pra pedidos com filtro/recorte, o cache nao representa o que foi pedido.
    sem_filtros = not any([tipo_documento, id_inicial, id_final,
                           periodo_inicio, periodo_fim, limite])
    if metodo == "nativo" and sem_filtros and consolidado_path.exists() and not forcar:
        tamanho = consolidado_path.stat().st_size
        _log(f"[BAIXA] Cache hit: {consolidado_path.name} ({tamanho/1024/1024:.2f} MB)")
        return {
            "numero_cnj": numero_cnj,
            "metodo": "cache",
            "caminho": str(consolidado_path),
            "tamanho_bytes": tamanho,
            "tamanho_mb": round(tamanho / 1024 / 1024, 2),
            "cache": True,
        }

    if metodo == "nativo":
        # Download com filtro/recorte NAO pode ocupar o caminho de cache
        destino = consolidado_path if sem_filtros else (
            pasta / f"{cnj_safe(numero_cnj)} (recorte).pdf"
        )
        r = await client.baixar_processo_nativo(
            numero_cnj=numero_cnj,
            caminho_destino=destino,
            tipo_documento=tipo_documento,
            id_inicial=id_inicial,
            id_final=id_final,
            periodo_inicio=periodo_inicio,
            periodo_fim=periodo_fim,
            cronologia=cronologia,
            incluir_expediente=incluir_expediente,
            incluir_movimentos=incluir_movimentos,
        )
        r["numero_cnj"] = numero_cnj
        r["metodo"] = "nativo"
        r["cache"] = False
        return r

    # Default: doc_a_doc
    return await baixar_processo_doc_a_doc(
        client, numero_cnj, ordem=cronologia, limite=limite
    )


# =========================================================================
# DOWNLOAD EM BACKGROUND (imune ao timeout do protocolo MCP)
# =========================================================================
#
# Problema: o metodo nativo gera o PDF no servidor do PJe (~30-90s pra autos
# grandes) e so entao baixa dezenas de MB do S3. A tool call sincrona estoura
# o timeout curto do protocolo MCP (~30-40s); quando o cliente cancela a
# request, a corrotina do download e' cancelada junto e NADA e' salvo.
#
# Solucao: disparar o download como asyncio.create_task (rodando no event loop
# do server, nao amarrado a request). A task e' guardada em _JOBS (ref forte,
# nao coletada pelo GC), entao sobrevive ao fim/cancelamento da tool call.
# A 1a chamada ainda espera ~GRACA_SINCRONA_S: se o processo for pequeno,
# resolve em UMA chamada; se for grande, retorna "em_andamento" e o usuario
# consulta status_download depois.
#
# Seguranca com o singleton: baixar_processo_nativo e' @_serializa (segura o
# _op_lock durante todo o download), e o watchdog de inatividade so fecha a
# sessao APOS adquirir esse mesmo lock. Logo o Chromium nunca e' fechado no
# meio de um download em andamento.

_JOBS: dict = {}  # cnj_safe -> {status, task, iniciado_em, concluido_em, resultado, erro}
GRACA_SINCRONA_S = 20  # quanto a 1a chamada espera antes de devolver "em_andamento"


async def _executar_download(client, numero_cnj: str, cronologia: str, forcar: bool) -> None:
    """Corpo da task de background: baixa e registra o resultado em _JOBS.

    Nunca propaga excecao (roda solta no loop); erros viram status='erro'.
    """
    chave = f"{client.grau}:{cnj_safe(numero_cnj)}"
    try:
        r = await baixar_processo_completo(
            client,
            numero_cnj=numero_cnj,
            cronologia=cronologia,
            forcar=forcar,
            metodo="nativo",
        )
        _JOBS[chave].update(
            status="concluido", resultado=r, erro=None, concluido_em=time.time()
        )
        _log(f"[BG] Download concluido: {chave} ({r.get('tamanho_mb')} MB)")
    except Exception as e:
        _JOBS[chave].update(
            status="erro",
            resultado=None,
            erro=f"{type(e).__name__}: {e}",
            concluido_em=time.time(),
        )
        _log(f"[BG] Download FALHOU: {chave} - {e}")


def status_download(numero_cnj: str, grau: str = "1g") -> dict:
    """Consulta o estado de um download (em_andamento/concluido/erro/inexistente).

    Nao toca no browser - so le o registro _JOBS e o disco. Seguro chamar a
    qualquer momento, inclusive enquanto o download corre em background.
    """
    chave = f"{grau}:{cnj_safe(numero_cnj)}"
    job = _JOBS.get(chave)
    consolidado = pasta_processo(numero_cnj, grau) / f"{cnj_safe(numero_cnj)}.pdf"

    if job is None:
        if consolidado.exists():
            tam = consolidado.stat().st_size
            return {
                "numero_cnj": numero_cnj,
                "status": "concluido",
                "caminho": str(consolidado),
                "tamanho_mb": round(tam / 1024 / 1024, 2),
                "observacao": "arquivo ja existia (sem job na sessao atual)",
            }
        return {
            "numero_cnj": numero_cnj,
            "status": "inexistente",
            "observacao": "nenhum download iniciado pra este processo nesta sessao",
        }

    base = {
        "numero_cnj": numero_cnj,
        "status": job["status"],
        "decorrido_s": round(time.time() - job["iniciado_em"], 1),
    }
    if job["status"] == "em_andamento":
        base["observacao"] = (
            "Servidor do PJe ainda gerando/baixando o PDF. "
            "Chame status_download de novo em ~30-60s."
        )
    elif job["status"] == "concluido":
        base.update(job.get("resultado") or {})
    elif job["status"] == "erro":
        base["erro"] = job.get("erro")
    return base


async def baixar_processo_background(
    client, numero_cnj: str, cronologia: str = "decrescente", forcar: bool = False
) -> dict:
    """Dispara o download nativo em background e espera ate GRACA_SINCRONA_S.

    - cache hit: retorna na hora.
    - processo pequeno: termina dentro da graca e retorna 'concluido'.
    - processo grande: retorna 'em_andamento' e segue baixando; consulte
      status_download pra acompanhar.
    """
    chave = f"{client.grau}:{cnj_safe(numero_cnj)}"
    consolidado = pasta_processo(numero_cnj, client.grau) / f"{cnj_safe(numero_cnj)}.pdf"

    # Cache hit imediato (autos completos ja em disco).
    if consolidado.exists() and not forcar:
        tam = consolidado.stat().st_size
        _log(f"[BG] Cache hit: {consolidado.name} ({tam/1024/1024:.2f} MB)")
        return {
            "numero_cnj": numero_cnj,
            "status": "concluido",
            "metodo": "cache",
            "cache": True,
            "caminho": str(consolidado),
            "tamanho_bytes": tam,
            "tamanho_mb": round(tam / 1024 / 1024, 2),
        }

    # Ja existe um download rodando pra este processo? Nao duplica.
    job = _JOBS.get(chave)
    if job and job["status"] == "em_andamento":
        resp = status_download(numero_cnj, client.grau)
        resp["ja_estava_em_andamento"] = True
        return resp

    # Dispara a task e guarda a ref forte em _JOBS (sobrevive ao fim da request).
    task = asyncio.create_task(
        _executar_download(client, numero_cnj, cronologia, forcar)
    )
    _JOBS[chave] = {
        "status": "em_andamento",
        "task": task,
        "iniciado_em": time.time(),
        "concluido_em": None,
        "resultado": None,
        "erro": None,
    }
    _log(f"[BG] Download iniciado em background: {chave}")

    # Espera a graca sincrona. shield garante que o timeout cancele SO a espera,
    # nunca a task de download (que segue no loop).
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=GRACA_SINCRONA_S)
    except asyncio.TimeoutError:
        pass

    return status_download(numero_cnj, client.grau)


async def preparar_processo_orquestrador(
    client, numero_cnj: str, forcar: bool = False
) -> dict:
    """Baixa o processo + decide estrategia de analise.

    Retorna:
      {
        "estrategia": "pdf_direto" | "notebooklm",
        "caminho_pdf": ...,
        "tamanho_mb": ...,
        "limite_mb": 18,
        "instrucao_claude": "..."
      }
    """
    info = await baixar_processo_completo(
        client, numero_cnj, cronologia="decrescente", forcar=forcar
    )

    tamanho_mb = info.get("tamanho_mb")
    if tamanho_mb is None:
        caminho = info.get("caminho")
        tamanho_mb = (
            round(Path(caminho).stat().st_size / 1024 / 1024, 2) if caminho else 0
        )
        info["tamanho_mb"] = tamanho_mb

    if tamanho_mb <= LIMITE_PDF_DIRETO_MB:
        estrategia = "pdf_direto"
        instrucao = (
            f"PDF do processo cabe na janela de contexto ({tamanho_mb:.2f} MB ≤ "
            f"{LIMITE_PDF_DIRETO_MB} MB). Leia diretamente o arquivo em "
            f"{info['caminho']} pra analisar."
        )
    else:
        estrategia = "notebooklm"
        instrucao = (
            f"PDF muito grande ({tamanho_mb:.2f} MB > {LIMITE_PDF_DIRETO_MB} MB). "
            f"Suba pro NotebookLM:\n"
            f"  1. notebook_create(name='Processo {numero_cnj}')\n"
            f"  2. source_add(source_type='file', file_path='{info['caminho']}')\n"
            f"  3. Use notebook_query pra fazer perguntas sobre o processo."
        )

    return {
        **info,
        "estrategia": estrategia,
        "limite_mb": LIMITE_PDF_DIRETO_MB,
        "instrucao_claude": instrucao,
    }
