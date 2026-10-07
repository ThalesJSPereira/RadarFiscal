"""
Testa o "Resumo das alterações" SEM enviar e-mail e SEM alterar nenhum dado.

Uso:
    python -m scraper.test_summary                 # NT MAIS RECENTE publicada nos portais (padrão)
    python -m scraper.test_summary <URL do PDF>    # resume um PDF/URL específico
    python -m scraper.test_summary arquivo.pdf     # resume um PDF local

Sem argumento, usa sempre a última Nota Técnica publicada (NF-e, CT-e/BP-e ou
MDF-e - a de data mais recente). Para restringir a um documento, defina
TEST_DOCUMENT=NFe | CTe | MDFe | BP-e.
"""

import os
import sys

from . import nt_summary
from .latest_nt import build_latest_item, requested_document


def _summarize_local_or_url(target):
    if os.path.exists(target):
        with open(target, "rb") as f:
            text = nt_summary._pdf_to_text(f.read())
    else:
        text, _ = nt_summary.get_document_text(target)
    print(f"[test_summary] {len(text)} caracteres extraídos do documento.")
    return {"title": target}, nt_summary.summarize_text(text, {})


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        item, summary = _summarize_local_or_url(sys.argv[1].strip())
    else:
        try:
            item = build_latest_item(requested_document())
        except LookupError as exc:
            print(f"[test_summary] ERRO: {exc}")
            sys.exit(1)
        summary = item["change_summary"]

    print("\n" + "=" * 70)
    print(f"NT: {item.get('title')}")
    print("=" * 70)
    print(nt_summary.summary_to_text(summary))
    print("=" * 70)
    if summary.get("doc_url"):
        print(f"Documento: {summary['doc_url']}")


if __name__ == "__main__":
    main()
