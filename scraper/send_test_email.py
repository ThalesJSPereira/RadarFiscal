"""
Script de TESTE para validar o envio de e-mail de alerta, isoladamente do
restante do pipeline (não mexe em data/state.json nem em docs/data.json).

Uso:
    python -m scraper.send_test_email

Monta 2 itens fictícios (um "novo" e um de "nova versão"), já COM o bloco
"Resumo das alterações", e chama a mesma função de envio usada em produção.
Assim você valida credenciais SMTP e o layout final do e-mail.
"""

from . import notifier

_FAKE_NEW_ITEM = {
    "document": "NFe",
    "date": "2026-10-02",
    "title": "[TESTE] Nota Técnica 9999.001 v.1.00",
    "summary": "Item de TESTE para validar o envio de e-mail. Nenhuma publicação real foi detectada.",
    "link": "https://www.nfe.fazenda.gov.br/portal/principal.aspx",
    "doc_url": "https://www.nfe.fazenda.gov.br/portal/principal.aspx",
    "change_summary": {
        "status": "ok",
        "purpose": "Esta nota técnica (fictícia) divulga a inclusão de novos campos no leiaute da NF-e e ajusta regras de validação.",
        "sections": [
            {"key": "removed", "title": "Exclusões / remoções",
             "items": ["Exclusão do campo XX01 do grupo de informações adicionais."]},
            {"key": "new", "title": "Inclusões",
             "items": ["Inclusão do grupo YY10 (Informações de teste) com os campos YY11 e YY12.",
                       "Nova regra de validação Z10-20 com rejeição 999 para o grupo YY10."]},
            {"key": "changed", "title": "Alterações",
             "items": ["Alteração da obrigatoriedade do campo YY11 para NFC-e."]},
        ],
        "elements": ["XX01", "YY10", "YY11", "YY12"],
        "rules": ["Regra Z10-20", "Rejeição 999"],
        "deadlines": ["Homologação: 15/10/2026", "Produção: 01/11/2026"],
    },
}

_FAKE_UPDATED_ITEM = {
    "document": "CTe",
    "date": "2026-10-02",
    "title": "[TESTE] Nota Técnica CT-e 9999.002 v.2.00",
    "summary": "Item de TESTE simulando uma nova versão de uma Nota Técnica já existente.",
    "link": "https://www.cte.fazenda.gov.br/portal/listaConteudo.aspx",
    "previous_version": "1.00",
    "previous_summary": "Resumo fictício da versão anterior, apenas para teste.",
    "change_summary": {
        "status": "unavailable",
        "reason": "link do documento não localizado na página do portal",
    },
}


def main():
    print("[send_test_email] Disparando e-mail de teste (dados fictícios, marcados como [TESTE])...")
    notifier.send_alert([_FAKE_NEW_ITEM], [_FAKE_UPDATED_ITEM])
    print("[send_test_email] Concluído. Verifique a caixa de entrada (e o Spam) dos destinatários em ALERT_TO.")


if __name__ == "__main__":
    main()
