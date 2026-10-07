"""
TESTE de envio de e-mail, SEMPRE com a Nota Técnica mais recente publicada
pelos portais oficiais (NF-e, CT-e/BP-e e MDF-e), já com o "Resumo das alterações".

Uso:
    python -m scraper.send_test_email

Opcional: TEST_DOCUMENT=NFe | CTe | MDFe | BP-e  (restringe a escolha a um documento)

O que faz:
  1. consulta os portais e escolhe a NT mais recente (ver scraper/latest_nt.py);
  2. baixa o documento dela e gera o resumo das alterações;
  3. imprime no log uma prévia do e-mail;
  4. envia o e-mail marcado como [TESTE] para os destinatários de ALERT_TO.

Não altera data/state.json nem docs/data.json e não depende de haver NT nova.
Se os Secrets de SMTP não estiverem configurados, só mostra a prévia no log.
"""

import sys

from . import notifier
from .latest_nt import build_latest_item, requested_document


def main():
    document = requested_document()
    print(f"[send_test_email] Buscando a Nota Técnica mais recente"
          + (f" (documento: {document})" if document else " (todos os documentos)") + "...")
    try:
        item = build_latest_item(document)
    except LookupError as exc:
        print(f"[send_test_email] ERRO: {exc}")
        sys.exit(1)

    print("\n" + "=" * 70 + "\nPRÉVIA DO E-MAIL (texto)\n" + "=" * 70)
    print(notifier._build_text([item], [], test_mode=True))
    print("=" * 70 + "\n")

    if notifier.send_alert([item], [], test_mode=True):
        print("[send_test_email] Concluído. Verifique a caixa de entrada (e o Spam) dos destinatários em ALERT_TO.")
    else:
        print("[send_test_email] E-mail NÃO enviado: configure os Secrets de SMTP/ALERT_TO. A prévia acima mostra o conteúdo que seria enviado.")


if __name__ == "__main__":
    main()
