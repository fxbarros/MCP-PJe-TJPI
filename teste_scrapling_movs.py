"""Prototipo: extrator de movimentacoes com Scrapling Selector vs regex atual.

Etapa 1 (captura): abre os autos de um processo real, salva o HTML em
fixtures/ e mostra o que o regex atual extrai. Com a fixture salva, o
extrator novo pode ser iterado OFFLINE (sem novo login) na etapa 2.

Uso:
    venv/bin/python teste_scrapling_movs.py 0801447-61.2024.8.18.0037
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import keyring

from pje_client import PJeClient
from pje_downloader import cnj_safe

KEYRING_SERVICE = "mcp-pje-tjpi"
PASTA_FIXTURES = Path(__file__).parent / "fixtures"


async def capturar(numero_cnj: str) -> Path:
    cpf = keyring.get_password(KEYRING_SERVICE, "cpf")
    senha = keyring.get_password(KEYRING_SERVICE, "senha")
    seed = keyring.get_password(KEYRING_SERVICE, "totp_seed")
    if not all([cpf, senha, seed]):
        raise RuntimeError("Credenciais nao encontradas no Keychain.")

    PASTA_FIXTURES.mkdir(exist_ok=True)
    destino = PASTA_FIXTURES / f"autos_{cnj_safe(numero_cnj)}.html"

    async with PJeClient(cpf, senha, seed, persona="advogado", headless=True) as pje:
        aba = await pje._abrir_autos_processo(numero_cnj)
        try:
            # Mesmo insumo que ultimas_movimentacoes usa hoje
            texto = await aba.inner_text("body")
            html = await aba.content()
        finally:
            await pje._fechar_aba_autos(aba)

    destino.write_text(html, encoding="utf-8")
    (destino.with_suffix(".txt")).write_text(texto, encoding="utf-8")
    print(f"[FIXTURE] HTML salvo: {destino} ({len(html)/1024:.0f} KB)")
    print(f"[FIXTURE] inner_text salvo: {destino.with_suffix('.txt')}")

    # Baseline: o que o regex ATUAL extrai desse texto
    movs = PJeClient._extrair_movimentacoes(texto)
    print(f"\n[REGEX ATUAL] {len(movs)} movimentacoes:")
    for m in movs[:10]:
        print(f"  - {m}")
    return destino


if __name__ == "__main__":
    cnj = sys.argv[1] if len(sys.argv) > 1 else "0801447-61.2024.8.18.0037"
    if not re.sub(r"\D", "", cnj):
        sys.exit("CNJ invalido")
    asyncio.run(capturar(cnj))
