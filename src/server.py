"""Servidor MCP para consultas no PJe-TJPI - 1o GRAU.

Este MCP acessa EXCLUSIVAMENTE o 1o grau (varas) do Tribunal de Justica
do Piaui. Para 2o grau (cameras, acordaos, recursos), use outro MCP.

Tools disponiveis (22):
  CONSULTA:
    - expedientes_pendentes, verificar_prazos_urgentes
    - consultar_processo, ultimas_movimentacoes, relatorio_processo
    - buscar_por_nome_parte, buscar_por_nome_advogado
    - buscar_por_cpf, buscar_por_cnpj, buscar_por_oab
    - listar_documentos, ler_documento

  CONSULTA PONTUAL (sem download):
    - ultima_decisao, ultimo_despacho, pendencias_processo

  DOWNLOAD:
    - baixar_documento (1 doc especifico)
    - baixar_processo (autos completos via download nativo do PJe)
    - preparar_processo (baixa + decide pdf_direto vs notebooklm)

  MODELOS E MINUTAS:
    - listar_modelos_peticao, ler_modelo_peticao
    - salvar_peticao_processo, salvar_relatorio_processo

Todas suportam parametro 'persona': 'advogado' (default) ou 'procurador'.
Sessao do Chrome eh reusada por ate 5min entre tool calls (singleton).
"""
import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

import cliente_singleton
import minutas
import modelos
import pje_downloader


async def _watchdog_inatividade():
    """Fecha a sessao do PJe (Chromium) DE FATO apos o timeout de inatividade.

    Sem isso o browser ficava vivo indefinidamente entre chamadas - o
    timeout do singleton so era avaliado lazy, na tool call seguinte.
    """
    while True:
        await asyncio.sleep(60)
        try:
            await cliente_singleton.fechar_se_ocioso()
        except Exception as e:
            print(f"[WATCHDOG] erro ignorado: {e}", file=sys.stderr, flush=True)


@asynccontextmanager
async def _lifespan(_server):
    """Startup: warm-up do login em background (mata o -32001 da 1a chamada
    a frio) + watchdog de inatividade. Shutdown: fecha a sessao no MESMO
    event loop (o atexit do cliente_singleton vira apenas backstop).

    Warm-up pode ser desligado com PJE_WARMUP=0 (ex.: pra nao logar no PJe
    toda vez que o Claude Desktop inicia, se voce raramente usa este MCP).
    """
    tarefas = []
    if os.environ.get("PJE_WARMUP", "1") == "1":
        tarefas.append(asyncio.create_task(cliente_singleton.warmup()))
    tarefas.append(asyncio.create_task(_watchdog_inatividade()))
    try:
        yield {}
    finally:
        for t in tarefas:
            t.cancel()
        try:
            await cliente_singleton.fechar_cliente()
        except Exception:
            pass


mcp = FastMCP("pje-tjpi-1g", lifespan=_lifespan)

TRIBUNAL = "TJPI"
GRAU = "1º grau"


def _normaliza_persona(persona: str) -> str:
    """Normaliza variacoes de texto para 'advogado' ou 'procurador'."""
    if not persona:
        return "advogado"
    p = persona.strip().lower()
    if any(k in p for k in ["procurador", "municipio", "município", "pgm", "teresina"]):
        return "procurador"
    return "advogado"


def _marcar_grau(r: dict, persona: str) -> dict:
    """Adiciona metadados de tribunal/grau/persona ao retorno."""
    r["tribunal"] = TRIBUNAL
    r["grau"] = GRAU
    r["persona_utilizada"] = persona
    return r


# =========================================================================
# EXPEDIENTES E PRAZOS
# =========================================================================

@mcp.tool()
async def expedientes_pendentes(persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Lista expedientes pendentes de ciencia/resposta."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.expedientes_pendentes(), p)


@mcp.tool()
async def verificar_prazos_urgentes(persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Retorna expedientes com data limite em ate 3 dias."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje.expedientes_pendentes()

    hoje = datetime.now()
    limite = hoje + timedelta(days=3)
    urgentes = []
    for exp in r.get("expedientes", []):
        try:
            dl = datetime.strptime(exp["data_limite"], "%d/%m/%Y %H:%M")
            dias = (dl - hoje).days
            if dl <= limite:
                # vencido=True deixa explicito que o prazo JA passou
                # (dias_restantes negativo era facil de passar batido)
                urgentes.append({**exp, "dias_restantes": dias, "vencido": dl < hoje})
        except (ValueError, KeyError):
            continue
    urgentes.sort(key=lambda x: x.get("dias_restantes", 999))
    return _marcar_grau({
        "total_urgentes": len(urgentes),
        "total_expedientes": len(r.get("expedientes", [])),
        "urgentes": urgentes,
    }, p)


# =========================================================================
# CONSULTA DE PROCESSO POR NUMERO CNJ
# =========================================================================

@mcp.tool()
async def consultar_processo(numero_cnj: str, persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Consulta dados basicos de um processo pelo numero CNJ."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_processo(numero_cnj), p)


@mcp.tool()
async def ultimas_movimentacoes(
    numero_cnj: str, limite: int = 5, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Lista as ultimas N movimentacoes (padrao 5)."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.ultimas_movimentacoes(numero_cnj, limite), p)


@mcp.tool()
async def relatorio_processo(numero_cnj: str, persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Relatorio completo: dados, partes, movimentacoes, documentos."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.relatorio_processo(numero_cnj), p)


# =========================================================================
# BUSCAS POR DIFERENTES CRITERIOS
# =========================================================================

@mcp.tool()
async def buscar_por_nome_parte(
    nome: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Busca processos pelo NOME de uma parte (autor ou reu)."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_por_nome_parte(nome, limite), p)


@mcp.tool()
async def buscar_por_nome_advogado(
    nome: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Busca processos pelo NOME do advogado/representante."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_por_nome_advogado(nome, limite), p)


@mcp.tool()
async def buscar_por_cpf(
    cpf_busca: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Busca processos pelo CPF de uma das partes."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_por_cpf(cpf_busca, limite), p)


@mcp.tool()
async def buscar_por_cnpj(
    cnpj: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Busca processos pelo CNPJ de uma das partes."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_por_cnpj(cnpj, limite), p)


@mcp.tool()
async def buscar_por_oab(
    numero_oab: str, uf: str = "PI", limite: int = 20, persona: str = "advogado"
) -> dict:
    """[TJPI - 1o GRAU] Busca processos pelo numero OAB do advogado."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.buscar_por_oab(numero_oab, uf.upper(), limite), p)


# =========================================================================
# DOCUMENTOS
# =========================================================================

@mcp.tool()
async def listar_documentos(numero_cnj: str, persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Lista todos os documentos do processo (id + tipo)."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(await pje.listar_documentos(numero_cnj), p)


@mcp.tool()
async def ler_documento(
    numero_cnj: str,
    id_documento: str,
    max_paginas: int = 30,
    persona: str = "advogado",
) -> dict:
    """[TJPI - 1o GRAU] Le o teor completo de um documento (HTML ou PDF).

    PDFs com mais de max_paginas paginas voltam truncados, com truncado=True
    e aviso explicito no retorno.
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    return _marcar_grau(
        await pje.ler_documento(numero_cnj, str(id_documento), max_paginas), p
    )


# =========================================================================
# CONSULTA PONTUAL (sem download)
# =========================================================================

@mcp.tool()
async def ultima_decisao(numero_cnj: str, persona: str = "advogado") -> dict:
    """
    [TJPI - 1o GRAU] Le o teor da ultima decisao/sentenca/despacho/ato ordinatorio.

    Abre os autos UMA vez, filtra os decisorios, escolhe o de maior ID
    (mais recente) e devolve o teor completo. NAO baixa o processo todo.
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje.ler_documento_filtrado(
        numero_cnj,
        r"(decis[ãa]o|senten[çc]a|despacho|ato\s+ordinat[óo]rio)",
    )
    if not r.get("encontrado"):
        r.setdefault(
            "aviso",
            "Nenhuma decisao/sentenca/despacho encontrado nos documentos listados.",
        )
        r["numero_cnj"] = numero_cnj
    return _marcar_grau(r, p)


@mcp.tool()
async def ultimo_despacho(numero_cnj: str, persona: str = "advogado") -> dict:
    """[TJPI - 1o GRAU] Le o teor do ultimo despacho (so despacho, nao decisao)."""
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje.ler_documento_filtrado(numero_cnj, r"despacho")
    if not r.get("encontrado"):
        r.setdefault("aviso", "Nenhum despacho encontrado.")
        r["numero_cnj"] = numero_cnj
    return _marcar_grau(r, p)


@mcp.tool()
async def pendencias_processo(numero_cnj: str, persona: str = "advogado") -> dict:
    """
    [TJPI - 1o GRAU] Retorna pendencias (expedientes + prazos) de UM processo.

    Filtra os expedientes pendentes pelo numero do processo.
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje.expedientes_pendentes()

    cnj_normalizado = re.sub(r"\D", "", numero_cnj)
    pendencias = []
    hoje = datetime.now()
    for exp in r.get("expedientes", []):
        n = re.sub(r"\D", "", exp.get("numero_processo", ""))
        if n == cnj_normalizado:
            try:
                dl = datetime.strptime(exp["data_limite"], "%d/%m/%Y %H:%M")
                exp = {**exp, "dias_restantes": (dl - hoje).days}
            except (ValueError, KeyError):
                pass
            pendencias.append(exp)

    return _marcar_grau({
        "numero_cnj": numero_cnj,
        "total_pendencias": len(pendencias),
        "pendencias": pendencias,
    }, p)


# =========================================================================
# DOWNLOAD
# =========================================================================

@mcp.tool()
async def baixar_documento(
    numero_cnj: str,
    id_documento: str,
    tipo_descritivo: str = "",
    persona: str = "advogado",
) -> dict:
    """
    [TJPI - 1o GRAU] Baixa UM documento especifico e salva no iCloud.

    Pasta: ~/Library/Mobile Documents/.../Processos TJPI 1 Grau/{cnj}/documentos/

    - tipo_descritivo: opcional, vai pro nome do arquivo (ex: 'Despacho')
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    # garantir_processo_id (chamado dentro do downloader) abre os autos so
    # se necessario e FECHA a aba - antes a aba ficava vazando no singleton
    r = await pje_downloader.salvar_documento(
        pje, numero_cnj, str(id_documento), tipo_descritivo
    )
    return _marcar_grau(r, p)


@mcp.tool()
async def baixar_processo(
    numero_cnj: str,
    metodo: str = "nativo",
    limite: int = 0,
    cronologia: str = "decrescente",
    forcar: bool = False,
    persona: str = "advogado",
) -> dict:
    """
    [TJPI - 1o GRAU] Baixa os autos COMPLETOS do processo.

    Salva PDF consolidado em: Processos TJPI 1 Grau/{cnj}/{cnj}.pdf
    (so no metodo nativo; doc_a_doc consolida com sufixo proprio e tambem
    salva individuais em .../documentos/)

    - metodo: 'nativo' (default, completo - servidor PJe consolida com
              capa/indice + expediente + movimentos)
            | 'doc_a_doc' (alternativo - itera arvore de docs e concatena;
              util pra ter arquivos individuais separados; pode nao pegar
              todos os docs em processos muito grandes - lazy-load da arvore)
    - limite: 0=todos | N=baixa so os primeiros N documentos (por cronologia)
              [aplicavel apenas em metodo='doc_a_doc']
    - cronologia: 'decrescente' (default, mais recente primeiro) | 'crescente'
    - forcar: True pra re-baixar mesmo se ja existir cache
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje_downloader.baixar_processo_completo(
        pje,
        numero_cnj=numero_cnj,
        cronologia=cronologia,
        forcar=forcar,
        metodo=metodo,
        limite=limite,
    )
    return _marcar_grau(r, p)


@mcp.tool()
async def preparar_processo(
    numero_cnj: str, forcar: bool = False, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Baixa o processo + decide estrategia de analise.

    Retorna:
    - estrategia='pdf_direto' (≤18 MB): Claude le o PDF direto
    - estrategia='notebooklm' (>18 MB): instrucoes pro Claude usar NotebookLM
    """
    p = _normaliza_persona(persona)
    pje = await cliente_singleton.get_cliente(p)
    r = await pje_downloader.preparar_processo_orquestrador(pje, numero_cnj, forcar=forcar)
    return _marcar_grau(r, p)


# =========================================================================
# MODELOS E MINUTAS
# =========================================================================

@mcp.tool()
async def listar_modelos_peticao() -> dict:
    """
    [TJPI - 1o GRAU] Lista modelos de peticao/relatorio em iCloud.

    Pasta: ~/Library/Mobile Documents/com~apple~CloudDocs/Modelos TJPI 1 Grau/
    """
    return modelos.listar_modelos()


@mcp.tool()
async def ler_modelo_peticao(arquivo: str) -> dict:
    """[TJPI - 1o GRAU] Le o conteudo textual de um modelo (.docx/.md/.txt)."""
    return modelos.ler_modelo(arquivo)


@mcp.tool()
async def salvar_peticao_processo(
    numero_cnj: str,
    conteudo: str,
    tipo: str = "Petição",
    formato: str = "docx",
) -> dict:
    """
    [TJPI - 1o GRAU] Salva uma peticao na pasta do processo.

    - tipo: 'Petição', 'Manifestação', 'Embargos', 'Recurso', 'Contestação'
    - formato: 'docx' (default) | 'md' | 'txt'
    """
    return minutas.salvar_peca(numero_cnj, conteudo, tipo=tipo, formato=formato)


@mcp.tool()
async def salvar_relatorio_processo(
    numero_cnj: str,
    conteudo: str,
    formato: str = "docx",
) -> dict:
    """[TJPI - 1o GRAU] Salva um relatorio de analise na pasta do processo."""
    return minutas.salvar_peca(numero_cnj, conteudo, tipo="Relatório", formato=formato)


if __name__ == "__main__":
    mcp.run()
