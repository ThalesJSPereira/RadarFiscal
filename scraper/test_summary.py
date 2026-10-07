"""
Testa o "Resumo das alterações" SEM enviar e-mail e SEM alterar nenhum dado.

Uso:
    python -m scraper.test_summary                 # pega a NT mais recente do portal da NF-e
    python -m scraper.test_summary <URL do PDF>    # resume um PDF/URL específico
    python -m scraper.test_summary arquivo.pdf     # resume um PDF local

O resultado é impresso no log.
"""

import os
import sys

from . import config, nt_summary
from .fetcher import fetch
from .parsers import PARSERS


def _summarize_local_or_url(target):
    if os.path.exists(target):
        with open(target, "rb") as f:
            text = nt_summary._pdf_to_text(f.read())
    else:
        text, _ = nt_summary.get_document_text(target)
    print(f"[test_summary] {len(text)} caracteres extraídos do documento.")
    return nt_summary.summarize_text(text, {})


def _summarize_latest_nfe():
    source = next(s for s in config.SOURCES if s["key"] == "nfe_notas_tecnicas")
    html = fetch(source["url"])
    items = PARSERS[source["parser"]](html, source, source["base_url"])
    items.sort(key=lambda i: i.get("date", ""), reverse=True)
    item = items[0]
    print(f"[test_summary] NT mais recente do portal: {item['title']} ({item['date']})")
    return nt_summary.build_summary(item, html, source["url"])


def main():
    if len(sys.argv) > 1:
        summary = _summarize_local_or_url(sys.argv[1])
    else:
        summary = _summarize_latest_nfe()
    print("\n" + "=" * 70)
    print(nt_summary.summary_to_text(summary))
    print("=" * 70)
    if summary.get("doc_url"):
        print(f"Documento: {summary['doc_url']}")


if __name__ == "__main__":
    main()
