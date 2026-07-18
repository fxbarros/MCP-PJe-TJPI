"""Salvamento de peticoes e relatorios na pasta do processo (iCloud).

Pasta: ~/Library/Mobile Documents/.../Processos TJPI {1,2} Grau/{cnj}/ (por grau)
Arquivos: '{tipo} {cnj}.docx' (ex: 'Peticao 0000000-00.0000.0.00.0000.docx')
Se ja existir arquivo com o mesmo nome, versiona: '{tipo} {cnj} (2).docx' etc.
"""
import re
import sys
from pathlib import Path

from pje_downloader import cnj_safe, pasta_processo


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _salvar_docx(caminho: Path, conteudo: str) -> None:
    """Salva texto como .docx, paragrafo por paragrafo."""
    from docx import Document
    doc = Document()
    for paragrafo in conteudo.split("\n"):
        doc.add_paragraph(paragrafo)
    doc.save(str(caminho))


def _salvar_texto(caminho: Path, conteudo: str) -> None:
    caminho.write_text(conteudo, encoding="utf-8")


def salvar_peca(
    numero_cnj: str,
    conteudo: str,
    tipo: str = "Petição",
    formato: str = "docx",
    grau: str = "1g",
) -> dict:
    """Salva uma peca (peticao/relatorio/manifestacao/etc) na pasta do processo.

    tipo: 'Petição', 'Relatório', 'Manifestação', 'Despacho' (vai pro nome)
    formato: 'docx' (default) | 'md' | 'txt'
    """
    if formato not in {"docx", "md", "txt"}:
        raise ValueError(f"Formato invalido: {formato}. Use docx/md/txt.")

    pasta = pasta_processo(numero_cnj, grau)
    # Sanitiza tipo e cnj pro nome do arquivo (mesma regra da pasta) -
    # evita separadores de caminho e afins vindos dos parametros
    tipo_seguro = re.sub(r"[^\w À-ÿ.-]", "_", tipo).strip() or "Documento"
    base = f"{tipo_seguro} {cnj_safe(numero_cnj)}"
    caminho = pasta / f"{base}.{formato}"

    # Nao sobrescreve versao anterior: acrescenta (2), (3), ...
    versao = 2
    while caminho.exists():
        caminho = pasta / f"{base} ({versao}).{formato}"
        versao += 1

    if formato == "docx":
        _salvar_docx(caminho, conteudo)
    else:
        _salvar_texto(caminho, conteudo)

    tamanho = caminho.stat().st_size
    _log(f"[MINUTA] Salvo {caminho.name} ({tamanho/1024:.1f} KB)")

    return {
        "numero_cnj": numero_cnj,
        "tipo": tipo,
        "formato": formato,
        "caminho": str(caminho),
        "tamanho_bytes": tamanho,
        "tamanho_kb": round(tamanho / 1024, 1),
        "num_caracteres": len(conteudo),
    }
