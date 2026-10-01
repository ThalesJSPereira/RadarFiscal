"""
Parsers específicos de cada portal oficial.

IMPORTANTE (leia antes de rodar em produção):
Os portais de NF-e, CT-e e MDF-e não oferecem uma API pública para consulta
de Notas Técnicas, então o monitor precisa interpretar o HTML/texto renderizado
das páginas. Os padrões de regex abaixo foram construídos a partir do texto
real publicado nessas páginas (ex.: "Nota Técnica 2026.009 v.1.00 - Publicada
em 09/09/2026 Divulga correção em regra de validação"). Como os três portais
têm diagramação diferente, cada um tem sua própria função de parsing.

Se o layout de um site mudar, normalmente basta ajustar o regex da função
correspondente — o restante do pipeline (diff, e-mail, painel) não precisa
mudar, pois todos os parsers devolvem itens no mesmo formato normalizado:

{
    "document": "NFe" | "CTe" | "MDFe",
    "category": "Notas Técnicas" | "Informes" | "Documentos",
    "type": "Nota Técnica" | "Informe Técnico" | "Ato Conjunto" | ...,
    "code": "2026.009",           # código/número do ato, quando existir
    "version": "1.00",            # versão, quando existir
    "date": "2026-09-09",         # ISO yyyy-mm-dd
    "title": "Nota Técnica 2026.009 v.1.00",
    "summary": "Divulga correção em regra de validação",
    "link": "https://...",
    "source_key": "nfe_notas_tecnicas",
}
"""

import re
import hashlib
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup


MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def _to_iso_date(date_str):
    """Converte datas no formato dd/mm/aaaa para yyyy-mm-dd. Retorna None se falhar."""
    date_str = date_str.strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str)
    if m:
        d, mo, y = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def make_id(document, code, version, title):
    """Gera um identificador estável para deduplicação entre execuções."""
    base = f"{document}|{code or ''}|{version or ''}|{title}".lower().strip()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _classify_type(title):
    title_low = title.lower()
    if "informe técnico" in title_low or "informe tecnico" in title_low:
        return "Informe Técnico"
    if "ato conjunto" in title_low:
        return "Ato Conjunto"
    if "nota técnica conjunta" in title_low or "nota tecnica conjunta" in title_low:
        return "Nota Técnica Conjunta"
    if "manual" in title_low or "moc" in title_low:
        return "Manual"
    if "schema" in title_low:
        return "Schema"
    if "nota técnica" in title_low or "nota tecnica" in title_low or re.match(r"^nt\b", title_low):
        return "Nota Técnica"
    return "Publicação"


def _extract_code_version(title):
    """Extrai código (ex.: 2026.009) e versão (ex.: 1.00 / 1.10a) do título."""
    code = None
    version = None
    m_code = re.search(r"(\d{4}\.\d{3})", title)
    if m_code:
        code = m_code.group(1)
    m_ver = re.search(r"v\.?\s*(\d+\.\d+[a-z]?)", title, re.IGNORECASE)
    if m_ver:
        version = m_ver.group(1)
    return code, version


def _find_link(el, base_url):
    a = el.find("a", href=True) if hasattr(el, "find") else None
    if a:
        return urljoin(base_url, a["href"])
    return base_url


# ---------------------------------------------------------------------------
# Função auxiliar genérica: localiza todos os "cabeçalhos" de publicação
# (título + data, no formato "TÍTULO - Publicada em DD/MM/AAAA") dentro do
# texto e usa o espaço ENTRE dois cabeçalhos consecutivos como o resumo da
# publicação anterior. Essa abordagem é mais robusta do que tentar usar
# "Nota Técnica" como fronteira de parada, porque o próprio texto do resumo
# frequentemente começa com as palavras "Nota técnica de ..." (minúsculo),
# o que geraria falsos positivos de corte.
# ---------------------------------------------------------------------------
_HEADER_PATTERN_DATE_AFTER = re.compile(
    r"(?P<title>(?:Nota Técnica|Nota Técnica Conjunta|Informe Técnico|Ato Conjunto)"
    r"(?:(?!\s*-\s*Publicad[ao]\s+em)[^\n\r])*?)"
    r"\s*-\s*Publicad[ao]\s+em\s+(?P<date>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)


def _split_by_headers(text, header_pattern):
    """Retorna lista de dicts {title, date, summary} usando os cabeçalhos
    encontrados por `header_pattern` (que deve ter grupos nomeados 'title' e
    'date') como fronteiras entre publicações."""
    matches = list(header_pattern.finditer(text))
    results = []
    for idx, m in enumerate(matches):
        title = re.sub(r"\s+", " ", m.group("title")).strip(" -")
        date_raw = m.group("date")
        start_summary = m.end()
        end_summary = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        summary_raw = text[start_summary:end_summary]
        summary = re.sub(r"\s+", " ", summary_raw).strip(" -\n")
        results.append({"title": title, "date": date_raw, "summary": summary})
    return results


# ---------------------------------------------------------------------------
# Portal Nacional da NF-e - página "Documentos > Notas Técnicas"
# Padrão observado:
#   "Nota Técnica 2026.009 v.1.00 - Publicada em 09/09/2026"
#   "Divulga correção em regra de validação"
# ---------------------------------------------------------------------------
def parse_nfe_lista(html, source, base_url):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    items = []
    for entry in _split_by_headers(text, _HEADER_PATTERN_DATE_AFTER):
        title = entry["title"]
        code, version = _extract_code_version(title)
        iso_date = _to_iso_date(entry["date"])
        if not iso_date:
            continue
        summary = entry["summary"]
        item = {
            "document": source["document"],
            "category": source["category"],
            "type": _classify_type(title),
            "code": code,
            "version": version,
            "date": iso_date,
            "title": title,
            "summary": summary[:500] if summary else "(sem resumo divulgado pelo portal)",
            "link": source["url"],
            "source_key": source["key"],
        }
        item["id"] = make_id(item["document"], item["code"], item["version"], item["title"])
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Portal Nacional da NF-e - página inicial "Informes"
# Padrão observado:
#   "04/09/2026 Publicado Informe Técnico 2023.002 v.2.10 que divulga
#    atualização na tabela de CFOP ... (Leia mais)"
# ---------------------------------------------------------------------------
_NFE_INFORME_PATTERN = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s*(Publicad[ao]?\s.*?)(?=(?:\d{2}/\d{2}/\d{4})|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def parse_nfe_informes(html, source, base_url):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    # Restringe a busca à seção de "Informes" para não capturar o menu inteiro
    informes_idx = text.lower().find("informes")
    if informes_idx != -1:
        text = text[informes_idx:]
    items = []
    for m in _NFE_INFORME_PATTERN.finditer(text):
        date_raw, body = m.groups()
        body = re.sub(r"\s+", " ", body).strip()
        body = re.sub(r"\(Leia mais\)$", "", body).strip()
        iso_date = _to_iso_date(date_raw)
        if not iso_date:
            continue
        title = body[:140]
        code, version = _extract_code_version(body)
        item = {
            "document": source["document"],
            "category": source["category"],
            "type": _classify_type(body),
            "code": code,
            "version": version,
            "date": iso_date,
            "title": title,
            "summary": body[:500],
            "link": source["url"],
            "source_key": source["key"],
        }
        item["id"] = make_id(item["document"], item["code"], item["version"], item["title"])
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Portal do CT-e - página "Documentos > Notas Técnicas"
# Padrão observado (mesma estrutura "TÍTULO - Publicada em DATA" da NF-e):
#   "Nota Técnica CT-e 2026.002 v.1.01 - Publicada em 04/08/2026"
#   "Nota técnica de adequação dos leiautes do CT-e, do CT-eOS e da GTV-e."
# Reaproveita o mesmo separador de cabeçalhos usado no parser da NF-e.
# ---------------------------------------------------------------------------
_CTE_HEADER_PATTERN = re.compile(
    r"(?P<title>(?:Nota[_ ]Técnica)(?:(?!\s*-\s*Publicad[ao]\s+em)[^\n\r])*?)"
    r"\s*-\s*Publicad[ao]\s+em\s+(?P<date>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)


def parse_cte_lista(html, source, base_url):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    items = []
    for entry in _split_by_headers(text, _CTE_HEADER_PATTERN):
        title = entry["title"]
        code, version = _extract_code_version(title)
        iso_date = _to_iso_date(entry["date"])
        if not iso_date:
            continue
        summary = entry["summary"]
        # Detecta o documento real (CT-e, BP-e, BP-e TA, GTV-e) a partir do título
        doc = source["document"]
        title_low = title.lower()
        if "bp-e ta" in title_low or "bpe ta" in title_low:
            doc = "BP-e TA"
        elif "bp-e" in title_low or "bpe" in title_low:
            doc = "BP-e"
        elif "ct-e" in title_low or "cte" in title_low:
            doc = "CTe"
        item = {
            "document": doc,
            "category": source["category"],
            "type": _classify_type(title),
            "code": code,
            "version": version,
            "date": iso_date,
            "title": title,
            "summary": summary[:500] if summary else "(sem resumo divulgado pelo portal)",
            "link": source["url"],
            "source_key": source["key"],
        }
        item["id"] = make_id(item["document"], item["code"], item["version"], item["title"])
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Portal do MDF-e (SVRS) - página "Documentos"
# Padrão observado (data ANTES do título, ao contrário dos outros portais):
#   "29/05/2026 Nota Técnica 2026.001 Esta NT dispõe sobre regra de
#    validação do MDFe obrigando o CIOT ..."
#   "07/05/2025 Nota Técnica DFe Conjunta - CNPJ Alfanumérico v1.00
#    Publica-se a nota técnica conjunta ..."
# Usa o mesmo separador genérico de cabeçalhos (_split_by_headers); como os
# grupos nomeados 'title'/'date' funcionam independente da ordem em que
# aparecem no texto, o mesmo helper serve para os três portais.
# ---------------------------------------------------------------------------
_MDFE_HEADER_PATTERN = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<title>(?:Nota Técnica|Nota Tecnica|Schemas?|Manuais?|MOC)[^\n\r]*)",
    re.IGNORECASE,
)


def parse_mdfe_lista(html, source, base_url):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    # Restringe à seção "Notas Técnicas" quando existir, para reduzir ruído
    idx = text.lower().find("notas técnicas")
    section_text = text[idx:] if idx != -1 else text
    items = []
    for entry in _split_by_headers(section_text, _MDFE_HEADER_PATTERN):
        title = entry["title"]
        code, version = _extract_code_version(title)
        iso_date = _to_iso_date(entry["date"])
        if not iso_date:
            continue
        summary = entry["summary"]
        item = {
            "document": "MDFe",
            "category": source["category"],
            "type": _classify_type(title),
            "code": code,
            "version": version,
            "date": iso_date,
            "title": title,
            "summary": summary[:500] if summary else "(sem resumo divulgado pelo portal)",
            "link": source["url"],
            "source_key": source["key"],
        }
        item["id"] = make_id(item["document"], item["code"], item["version"], item["title"])
        items.append(item)
    return items


PARSERS = {
    "nfe_lista": parse_nfe_lista,
    "nfe_informes": parse_nfe_informes,
    "cte_lista": parse_cte_lista,
    "mdfe_lista": parse_mdfe_lista,
}
