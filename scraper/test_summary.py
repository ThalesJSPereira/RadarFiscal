"""
Testa o "Resumo das alterações" SEM enviar e-mail e SEM alterar nenhum dado.

Uso:
    python -m scraper.test_summary                 # NT MAIS RECENTE publicada nos portais (padrão)
    python -m scraper.test_summary <URL do PDF>    # resume um PDF/URL específico
    python -m scraper.test_summary arquivo.pdf     # resume um PDF local

Sem argumento, usa sempre a última Nota Técnica publicada (NF-e, CT-e/BP-e ou
MDF-e - a de data mais recente). Para restringir a um documento, defina
TEST_DOCUMENT=NFe | CTe | MDFe | BP-e.

O log mostra as datas de HOMOLOGAÇÃO ("Implantação Teste") e PRODUÇÃO
("Implantação Produção") e o trecho do cronograma exatamente como foi lido do
PDF - útil para conferir/ajustar se alguma NT trouxer um formato diferente.
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


def _print_rollout(summary):
    info = nt_summary.rollout_info(summary)

    def show(date, note):
        return nt_summary._with_note(note, date) if date else "NÃO ENCONTRADA"

    print("DATAS DE IMPLANTAÇÃO")
    print(f"  Homologação (Implantação Teste):  {show(info['homologation'], info['homologation_note'])}")
    print(f"  Produção (Implantação Produção):  {show(info['production'], info['production_note'])}")
    if info["schedule_version"]:
        print(f"  (linha do cronograma usada: v{info['schedule_version']})")
    if summary.get("schedule_excerpt"):
        print("\nCronograma lido do PDF (trecho bruto):")
        print("  " + summary["schedule_excerpt"])


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
    _print_rollout(summary)
    print("-" * 70)
    print(nt_summary.summary_to_text(summary))
    print("=" * 70)
    if summary.get("doc_url"):
        print(f"Documento: {summary['doc_url']}")


if __name__ == "__main__":
    main()
