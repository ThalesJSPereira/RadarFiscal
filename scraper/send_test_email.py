"""
Script de TESTE para validar o envio de e-mail de alerta, isoladamente do
restante do pipeline (não mexe em data/state.json nem em docs/data.json).

Uso:
    python -m scraper.send_test_email

Ele monta 2 itens fictícios (um "novo" e um de "nova versão") e chama a
mesma função de envio usada em produção (scraper.notifier.send_alert),
para você confirmar que:
  - as credenciais SMTP (Secrets do GitHub) estão corretas;
  - o e-mail chega na caixa de entrada dos destinatários configurados.

Se SMTP_HOST / SMTP_USER / SMTP_PASS / ALERT_TO não estiverem configurados,
o script avisa no log e não tenta enviar (mesmo comportamento do notifier
em produção).
"""

from . import notifier

_FAKE_NEW_ITEM = {
    "document": "NFe",
    "date": "2026-10-02",
    "title": "[TESTE] Nota Técnica 9999.001 v.1.00",
    "summary": "Este é um item de TESTE gerado manualmente para validar o envio de e-mail. Nenhuma publicação real foi detectada.",
    "link": "https://www.nfe.fazenda.gov.br/portal/principal.aspx",
}

_FAKE_UPDATED_ITEM = {
    "document": "CTe",
    "date": "2026-10-02",
    "title": "[TESTE] Nota Técnica CT-e 9999.002 v.2.00",
    "summary": "Este é um item de TESTE simulando uma nova versão de uma Nota Técnica já existente.",
    "link": "https://www.cte.fazenda.gov.br/portal/listaConteudo.aspx",
    "previous_version": "1.00",
    "previous_summary": "Resumo fictício da versão anterior, apenas para teste.",
}


def main():
    print("[send_test_email] Disparando e-mail de teste (dados fictícios, marcados como [TESTE])...")
    notifier.send_alert([_FAKE_NEW_ITEM], [_FAKE_UPDATED_ITEM])
    print("[send_test_email] Concluído. Verifique a caixa de entrada (e a pasta de Lixo Eletrônico/Spam) dos destinatários em ALERT_TO.")


if __name__ == "__main__":
    main()
