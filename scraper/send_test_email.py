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
  4. envia o e-mail marcado como [TESTE] para TODOS os destinatários de ALERT_TO
     (um ou vários e-mails; o log lista quem vai receber, com os endereços parcialmente ocultos).

Se o envio falhar, o log explica o motivo, mostra a configuração de e-mail em uso (sem
senha nem valores dos Secrets) e quais portas do servidor respondem, e o workflow termina
com erro.

Não altera data/state.json nem docs/data.json e não depende de haver NT nova.
Se os Secrets de SMTP não estiverem configurados, só mostra a prévia no log.
"""

import sys

from . import notifier
from .latest_nt import build_latest_item, requested_document


def main():
    # descarrega o log linha a linha (sem isso, os prints podem aparecer DEPOIS do erro no GitHub Actions)
    sys.stdout.reconfigure(line_buffering=True)

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

    print("Destinatários do teste:")
    print("\n".join(notifier.describe_recipients()) + "\n")
    try:
        sent = notifier.send_alert([item], [], test_mode=True)
    except notifier.EmailDeliveryError as exc:
        print("[send_test_email] ERRO: o e-mail NÃO foi enviado.\n" + str(exc))
        sys.exit(1)

    if sent:
        print("[send_test_email] Concluído. Verifique a caixa de entrada (e o Spam) de cada destinatário.")
    else:
        print("[send_test_email] E-mail NÃO enviado: configure os Secrets de SMTP/ALERT_TO. "
              "A prévia acima mostra o conteúdo que seria enviado.\n")
        print("\n".join(notifier.describe_settings()))


if __name__ == "__main__":
    main()
