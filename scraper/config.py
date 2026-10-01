"""
Configuração central do monitor de NFe / CTe / MDFe.

- SOURCES: lista das fontes oficiais monitoradas (Portal Nacional da NF-e,
  Portal do CT-e e Portal do MDF-e/SVRS). Cada fonte tem uma URL "principal"
  (ex.: página de Notas Técnicas) e um parser específico (ver parsers.py),
  já que cada portal publica o conteúdo em um formato de texto diferente.
- As credenciais de e-mail (SMTP) e destinatários são lidas de variáveis de
  ambiente para nunca ficarem hardcoded no código (serão configuradas como
  "Secrets" no GitHub Actions).
"""

import os

# ---------------------------------------------------------------------------
# Fontes oficiais monitoradas
# ---------------------------------------------------------------------------
# type: identifica qual função de parser (em parsers.py) deve processar a página.
SOURCES = [
    {
        "key": "nfe_notas_tecnicas",
        "label": "NF-e / NFC-e - Notas Técnicas",
        "document": "NFe",
        "category": "Notas Técnicas",
        "url": "https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=04BIflQt1aY=",
        "base_url": "https://www.nfe.fazenda.gov.br/portal/",
        "parser": "nfe_lista",
    },
    {
        "key": "nfe_informes",
        "label": "NF-e / NFC-e - Informes (página inicial)",
        "document": "NFe",
        "category": "Informes",
        "url": "https://www.nfe.fazenda.gov.br/portal/principal.aspx",
        "base_url": "https://www.nfe.fazenda.gov.br/portal/",
        "parser": "nfe_informes",
    },
    {
        "key": "cte_notas_tecnicas",
        "label": "CT-e / BP-e - Notas Técnicas",
        "document": "CTe",
        "category": "Notas Técnicas",
        "url": "https://www.cte.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=Y0nErnoZpsg=",
        "base_url": "https://www.cte.fazenda.gov.br/portal/",
        "parser": "cte_lista",
    },
    {
        "key": "mdfe_documentos",
        "label": "MDF-e - Notas Técnicas, Manuais e Schemas",
        "document": "MDFe",
        "category": "Documentos",
        "url": "https://dfe-portal.svrs.rs.gov.br/Mdfe/Documentos",
        "base_url": "https://dfe-portal.svrs.rs.gov.br/",
        "parser": "mdfe_lista",
    },
]

# ---------------------------------------------------------------------------
# Caminhos de arquivo
# ---------------------------------------------------------------------------
STATE_FILE = os.path.join("data", "state.json")
DASHBOARD_DATA_FILE = os.path.join("docs", "data.json")

# Quantos dias um item fica marcado como "NOVO" no painel
NEW_BADGE_DAYS = 3

# Cabeçalhos HTTP usados nas requisições (alguns portais de governo bloqueiam
# requisições sem um User-Agent de navegador "normal").
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.6,en;q=0.4",
}
HTTP_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Configuração de e-mail (lida de variáveis de ambiente / GitHub Secrets)
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_FROM = os.environ.get("ALERT_FROM", SMTP_USER)
# Vários destinatários separados por vírgula: "a@x.com,b@x.com"
ALERT_TO = [e.strip() for e in os.environ.get("ALERT_TO", "").split(",") if e.strip()]
