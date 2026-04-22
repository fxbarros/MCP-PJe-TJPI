"""Servidor MCP para consultas no PJe-TJPI - 1o GRAU.

Este MCP acessa EXCLUSIVAMENTE o 1o grau (varas) do Tribunal de Justica
do Piaui. Para 2o grau (cameras, acordaos, recursos), use outro MCP.

Tools disponiveis (12):
- expedientes_pendentes, verificar_prazos_urgentes
- consultar_processo, ultimas_movimentacoes, relatorio_processo
- buscar_por_nome_parte, buscar_por_nome_advogado
- buscar_por_cpf, buscar_por_cnpj, buscar_por_oab
- listar_documentos, ler_documento

Todas suportam parametro 'persona': 'advogado' (default) ou 'procurador'.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import keyring
from mcp.server.fastmcp import FastMCP

from pje_client import PJeClient

mcp = FastMCP("pje-tjpi-1g")

TRIBUNAL = "TJPI"
GRAU = "1º grau"
KEYRING_SERVICE = "mcp-pje-tjpi"  # chave usada pra salvar as credenciais


def _get_creds():
    """Le credenciais do Keychain do macOS."""
    cpf = keyring.get_password(KEYRING_SERVICE, "cpf")
    senha = keyring.get_password(KEYRING_SERVICE, "senha")
    seed = keyring.get_password(KEYRING_SERVICE, "totp_seed")
    if not all([cpf, senha, seed]):
        raise RuntimeError(
            "Credenciais nao encontradas no Keychain. "
            "Execute setup_credenciais.py primeiro."
        )
    return cpf, senha, seed


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
    """
    [TJPI - 1o GRAU] Lista expedientes pendentes de ciencia/resposta.

    persona: 'advogado' (padrao) ou 'procurador' para Procurador do Municipio.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.expedientes_pendentes(), p)


@mcp.tool()
async def verificar_prazos_urgentes(persona: str = "advogado") -> dict:
    """
    [TJPI - 1o GRAU] Retorna expedientes com data limite em ate 3 dias.
    """
    from datetime import datetime, timedelta
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        r = await pje.expedientes_pendentes()

    hoje = datetime.now()
    limite = hoje + timedelta(days=3)
    urgentes = []
    for exp in r.get("expedientes", []):
        try:
            dl = datetime.strptime(exp["data_limite"], "%d/%m/%Y %H:%M")
            dias = (dl - hoje).days
            if dl <= limite:
                urgentes.append({**exp, "dias_restantes": dias})
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
    """
    [TJPI - 1o GRAU] Consulta dados basicos de um processo pelo numero CNJ.
    Formato: 0000000-00.0000.0.00.0000.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.buscar_processo(numero_cnj), p)


@mcp.tool()
async def ultimas_movimentacoes(
    numero_cnj: str, limite: int = 5, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Lista as ultimas N movimentacoes (padrao 5).
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.ultimas_movimentacoes(numero_cnj, limite), p)


@mcp.tool()
async def relatorio_processo(numero_cnj: str, persona: str = "advogado") -> dict:
    """
    [TJPI - 1o GRAU] Relatorio completo: dados, partes, movimentacoes, documentos.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.relatorio_processo(numero_cnj), p)


# =========================================================================
# BUSCAS POR DIFERENTES CRITERIOS
# =========================================================================

@mcp.tool()
async def buscar_por_nome_parte(
    nome: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Busca processos pelo NOME de uma parte (autor ou reu).

    Util para listar todos os processos envolvendo uma pessoa.
    Ex: 'Maria Francisca Ramos' retorna todos processos em que ela eh parte.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.buscar_por_nome_parte(nome, limite), p)


@mcp.tool()
async def buscar_por_nome_advogado(
    nome: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Busca processos pelo NOME do advogado/representante.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.buscar_por_nome_advogado(nome, limite), p)


@mcp.tool()
async def buscar_por_cpf(
    cpf_busca: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Busca processos pelo CPF de uma das partes.
    Aceita CPF com ou sem pontuacao.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.buscar_por_cpf(cpf_busca, limite), p)


@mcp.tool()
async def buscar_por_cnpj(
    cnpj: str, limite: int = 20, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Busca processos pelo CNPJ de uma das partes.
    Aceita CNPJ com ou sem pontuacao.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.buscar_por_cnpj(cnpj, limite), p)


@mcp.tool()
async def buscar_por_oab(
    numero_oab: str, uf: str = "PI", limite: int = 20, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Busca processos pelo numero OAB do advogado.

    - numero_oab: so os numeros, sem a UF. Ex: '123456'
    - uf: sigla do estado da OAB. Padrao 'PI'.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(
            await pje.buscar_por_oab(numero_oab, uf.upper(), limite), p
        )


# =========================================================================
# DOCUMENTOS
# =========================================================================

@mcp.tool()
async def listar_documentos(numero_cnj: str, persona: str = "advogado") -> dict:
    """
    [TJPI - 1o GRAU] Lista todos os documentos do processo (id + tipo).
    Use antes de ler um documento especifico.
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(await pje.listar_documentos(numero_cnj), p)


@mcp.tool()
async def ler_documento(
    numero_cnj: str, id_documento: str, persona: str = "advogado"
) -> dict:
    """
    [TJPI - 1o GRAU] Le o teor completo de um documento (HTML ou PDF).

    - numero_cnj: numero do processo
    - id_documento: ID do documento (obtido via listar_documentos)
    """
    cpf, senha, seed = _get_creds()
    p = _normaliza_persona(persona)
    async with PJeClient(cpf, senha, seed, persona=p, headless=True) as pje:
        return _marcar_grau(
            await pje.ler_documento(numero_cnj, str(id_documento)), p
        )


if __name__ == "__main__":
    mcp.run()
