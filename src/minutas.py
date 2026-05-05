"""Salvamento de peticoes e relatorios na pasta do processo (iCloud).

Pasta: ~/Library/Mobile Documents/.../Processos TJPI 1 Grau/{cnj}/
Arquivos: '{tipo} {cnj}.docx' (ex: 'Peticao 0000000-00.0000.0.00.0000.docx')
"""
import sys
from pathlib import Path

from pje_downloader import pasta_processo


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
) -> dict:
    """Salva uma peca (peticao/relatorio/manifestacao/etc) na pasta do processo.

    tipo: 'Petição', 'Relatório', 'Manifestação', 'Despacho' (vai pro nome)
    formato: 'docx' (default) | 'md' | 'txt'
    """
    if formato not in {"docx", "md", "txt"}:
        raise ValueError(f"Formato invalido: {formato}. Use docx/md/txt.")

    pasta = pasta_processo(numero_cnj)
    nome_arquivo = f"{tipo} {numero_cnj}.{formato}"
    caminho = pasta / nome_arquivo

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
