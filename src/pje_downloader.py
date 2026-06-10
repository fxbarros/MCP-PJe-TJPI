"""Download de documentos e processos do PJe-TJPI 1o Grau.

Primitivas:
- baixar_documento_bytes: baixa 1 doc como bytes (PDF ou HTML)
- salvar_documento: salva 1 doc na pasta do processo

Constantes:
- PASTA_PROCESSOS: ~/Library/Mobile Documents/.../Processos TJPI 1 Grau/
- LIMITE_PDF_DIRETO_MB: 18 MB (acima vai pro NotebookLM)
"""
import re
import sys
from pathlib import Path

URL_BASE = "https://pje.tjpi.jus.br/1g"
LIMITE_PDF_DIRETO_MB = 18

PASTA_PROCESSOS = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/Processos TJPI 1 Grau"
)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cnj_safe(numero_cnj: str) -> str:
    """Normaliza CNJ pra usar como nome de pasta/arquivo (sem caracteres problematicos)."""
    return re.sub(r"[^\w.-]", "_", numero_cnj.strip())


def pasta_processo(numero_cnj: str) -> Path:
    """Retorna a pasta do processo no iCloud, criando se nao existir."""
    p = PASTA_PROCESSOS / cnj_safe(numero_cnj)
    p.mkdir(parents=True, exist_ok=True)
    return p


def url_documento(processo_id: str, documento_id: str) -> str:
    """Monta a URL de download direto de um documento."""
    return (
        f"{URL_BASE}/seam/resource/rest/pje-legacy/documento/download/"
        f"TJPI/1g/{processo_id}/{documento_id}"
    )


async def baixar_documento_bytes(client, numero_cnj: str, documento_id: str) -> dict:
    """Baixa 1 documento como bytes usando a sessao autenticada do PJeClient.

    Garante que o processo_id em cache pertence a ESTE processo (com o
    singleton, o id pode ter sobrado de outro processo consultado antes -
    usar o id errado baixaria documento de outro processo ou daria 404).
    Retorna dict com bytes, content_type, tamanho, formato.
    """
    processo_id = await client.garantir_processo_id(numero_cnj)

    url = url_documento(processo_id, str(documento_id))
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

    pasta = pasta_processo(numero_cnj) / "documentos"
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
            destino = pasta_processo(numero_cnj) / f"{cnj_safe(numero_cnj)}{sufixo}.pdf"
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

    pasta = pasta_processo(numero_cnj)
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
