from docling.document_converter import DocumentConverter
import sys

def converter(caminho_pdf):
    print(f"Convertendo: {caminho_pdf}")
    conv = DocumentConverter()
    result = conv.convert(caminho_pdf)
    markdown = result.document.export_to_markdown()
    
    saida = caminho_pdf.replace(".pdf", ".md")
    with open(saida, "w", encoding="utf-8") as f:
        f.write(markdown)
    
    print(f"Arquivo salvo: {saida}")

converter(sys.argv[1])
