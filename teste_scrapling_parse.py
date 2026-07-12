"""Prototipo etapa 2 (OFFLINE): extrator de movimentacoes com Scrapling.

Usa a fixture salva por teste_scrapling_movs.py - nao faz login.
Compara o regex atual (sobre inner_text) com o Selector (sobre HTML).

Estrutura real da timeline (mapeada na fixture em 12/07/2026):
    form#divTimeLine
      div.media.data > span.data-interna     -> cabecalho "23 mar 2026"
                                                (vem ANTES do grupo do dia)
      div.media.interno.tipo-M|tipo-D > div.media-body
        span.text-upper.texto-movimento      -> tipo do movimento
        div.anexos a span                    -> "92241811 - Despacho"
        small.text-muted (pull-right)        -> hora "20:22"
      (div.media so de documento nao tem texto-movimento - ignorado,
       mesmo comportamento do extrator atual)

Uso:
    venv/bin/python teste_scrapling_parse.py [cnj]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from scrapling.parser import Selector

from pje_client import PJeClient
from pje_downloader import cnj_safe

PASTA_FIXTURES = Path(__file__).parent / "fixtures"


def extrair_movimentacoes_scrapling(html: str) -> list[dict]:
    """Extrai movimentacoes da timeline dos autos parseando o DOM.

    Corrige dois defeitos do regex sobre inner_text:
    - so capturava o ULTIMO movimento de cada dia (o unico com a data
      na linha seguinte);
    - atribuia a esse movimento a data do grupo SEGUINTE (mais antigo),
      porque o cabecalho de data vem antes do grupo, nao depois.
    """
    def primeiro(el, sel):
        r = el.css(sel)
        return r[0] if r else None

    page = Selector(html)
    timeline = primeiro(page, "form#divTimeLine") or primeiro(page, "#divTimeLine")
    if timeline is None:
        return []

    movimentacoes = []
    data_atual = None
    for media in timeline.css("div.media"):
        classes = (media.attrib.get("class") or "").split()

        if "data" in classes:
            cabecalho = primeiro(media, "span.data-interna")
            if cabecalho:
                data_atual = cabecalho.text.strip()
            continue

        tipo_el = primeiro(media, "span.texto-movimento")
        if tipo_el is None:
            continue  # item so de documento (sem movimento) - fora do escopo

        mov = {"tipo": tipo_el.text.strip(), "data": data_atual}

        hora_el = primeiro(media, "small.text-muted")
        if hora_el:
            mov["hora"] = hora_el.text.strip()

        anexo = primeiro(media, "div.anexos a span")
        if anexo:
            m = re.match(r"(\d{6,9})\s*-\s*(.+)", anexo.text.strip())
            if m:
                mov["id"] = m.group(1)
                mov["descricao"] = m.group(2).strip()

        movimentacoes.append(mov)

    return movimentacoes


def main(numero_cnj: str) -> None:
    base = PASTA_FIXTURES / f"autos_{cnj_safe(numero_cnj)}"
    # nao usar with_suffix: o CNJ tem pontos e ele cortaria o ".0037" final
    html = Path(f"{base}.html").read_text(encoding="utf-8")
    texto = Path(f"{base}.txt").read_text(encoding="utf-8")

    antigas = PJeClient._extrair_movimentacoes(texto)
    # Usa o metodo INTEGRADO no pje_client (nao a copia local) - assim este
    # script serve de teste de regressao offline do codigo de producao
    novas = PJeClient._extrair_movimentacoes_dom(html)

    print(f"=== REGEX ATUAL: {len(antigas)} movimentacoes ===")
    for m in antigas:
        print(f"  {m.get('data', '?'):>12}  {m.get('hora', '?')}  {m['tipo'][:60]}"
              + (f"  [doc {m['id']}]" if m.get("id") else ""))

    print(f"\n=== SCRAPLING: {len(novas)} movimentacoes ===")
    for m in novas:
        print(f"  {m.get('data') or '?':>12}  {m.get('hora', '?')}  {m['tipo'][:60]}"
              + (f"  [doc {m['id']}]" if m.get("id") else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "0801447-61.2024.8.18.0037")
