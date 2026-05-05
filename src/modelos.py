"""Leitura de modelos de peticao/relatorio em iCloud.

Pasta: ~/Library/Mobile Documents/com~apple~CloudDocs/Modelos TJPI 1 Grau/
Convencao: nome descritivo + .docx ou .md, ex:
  - Modelo peticao contestacao.docx
  - Modelo relatorio despacho geral.docx
  - Modelo manifestacao geral.docx (fallback)
"""
import sys
from pathlib import Path

PASTA_MODELOS = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/Modelos TJPI 1 Grau"
)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _tipo_inferido(nome_stem: str) -> str:
    """Heuristica simples pra inferir tipo pelo nome."""
    s = nome_stem.lower()
    if "peticao" in s or "petição" in s or "contestacao" in s or "contestação" in s:
        return "peticao"
    if "relatorio" in s or "relatório" in s:
        return "relatorio"
    if "manifestacao" in s or "manifestação" in s:
        return "manifestacao"
    if "embargo" in s:
        return "embargos"
    if "recurso" in s or "apelacao" in s or "apelação" in s:
        return "recurso"
    return "outro"


def listar_modelos() -> dict:
    """Lista metadados dos modelos da pasta no iCloud."""
    if not PASTA_MODELOS.exists():
        return {
            "pasta": str(PASTA_MODELOS),
            "total": 0,
            "modelos": [],
            "aviso": (
                "Pasta nao existe. Crie manualmente em "
                f"{PASTA_MODELOS} e coloque os modelos .docx/.md la."
            ),
        }

    modelos = []
    for arquivo in sorted(PASTA_MODELOS.iterdir()):
        if arquivo.name.startswith("."):
            continue
        if arquivo.suffix.lower() not in {".docx", ".md", ".txt"}:
            continue
        modelos.append({
            "arquivo": arquivo.name,
            "stem": arquivo.stem,
            "extensao": arquivo.suffix,
            "tipo_inferido": _tipo_inferido(arquivo.stem),
            "tamanho_bytes": arquivo.stat().st_size,
        })

    return {
        "pasta": str(PASTA_MODELOS),
        "total": len(modelos),
        "modelos": modelos,
    }


def ler_modelo(arquivo: str) -> dict:
    """Le o conteudo textual de um modelo.

    arquivo: nome com ou sem extensao. Match case-insensitive.
    """
    if not PASTA_MODELOS.exists():
        raise FileNotFoundError(f"Pasta de modelos nao existe: {PASTA_MODELOS}")

    # Match case-insensitive, com ou sem extensao
    candidatos = []
    arquivo_norm = arquivo.lower().strip()
    for f in PASTA_MODELOS.iterdir():
        if f.name.startswith("."):
            continue
        if f.name.lower() == arquivo_norm:
            candidatos.append(f)
            break
        if f.stem.lower() == arquivo_norm:
            candidatos.append(f)
            break
        # Match parcial
        if arquivo_norm in f.stem.lower():
            candidatos.append(f)

    if not candidatos:
        disponiveis = [
            f.name for f in PASTA_MODELOS.iterdir()
            if f.suffix.lower() in {".docx", ".md", ".txt"} and not f.name.startswith(".")
        ]
        raise FileNotFoundError(
            f"Modelo '{arquivo}' nao encontrado. Disponiveis: {disponiveis}"
        )

    path = candidatos[0]
    _log(f"[MODELO] Lendo {path.name}")

    if path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(str(path))
        texto = "\n".join(p.text for p in doc.paragraphs)
    else:
        texto = path.read_text(encoding="utf-8")

    return {
        "arquivo": path.name,
        "extensao": path.suffix,
        "tipo_inferido": _tipo_inferido(path.stem),
        "texto": texto,
        "num_caracteres": len(texto),
    }
