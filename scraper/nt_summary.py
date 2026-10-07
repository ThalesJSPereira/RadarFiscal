"""
Resumo automático das alterações propostas por uma Nota Técnica (NT).

Para cada publicação NOVA (ou nova versão) detectada, este módulo localiza o
documento da NT no portal (normalmente um PDF), baixa, extrai o texto e
identifica, por regras de texto (sem IA externa): objetivo, inclusões,
alterações, exclusões, campos/grupos citados, regras/rejeições, e as DATAS DE
HOMOLOGAÇÃO E PRODUÇÃO.

Datas de implantação
--------------------
As NTs da SEFAZ trazem o cronograma em uma tabela "Histórico de Alterações /
Cronograma" com uma linha por versão e as colunas:

    Versão | Histórico de atualizações | Implantação Teste | Implantação Produção

  - "Implantação Teste"    = ambiente de HOMOLOGAÇÃO
  - "Implantação Produção" = ambiente de PRODUÇÃO

Vale a linha da versão da própria NT (a versão do título no portal; se não for
informada, a versão mais alta da tabela). Textos como "Até 05/10/2026" guardam o
qualificador ("até") para ser exibido junto da data.
Quando o documento não tem essa tabela, as datas são buscadas nas frases
("ambiente de homologação ... 15/10/2026").

Links
-----
O link do documento que vai para o e-mail e para o painel passa por
clean_document_url(): o portal (ASP.NET) acrescenta sozinho, nos redirecionamentos,
o marcador "AspxAutoDetectCookieSupport=1" (e, em alguns casos, um ID de sessão no
caminho). Esses trechos não identificam o documento e, abertos direto no navegador,
causam "ERR_TOO_MANY_REDIRECTS". O link guardado é o mesmo que o portal usa na
própria listagem.

O resultado fica em item["change_summary"] e é usado pelo painel e pelo e-mail:
  homologation / production            -> "dd/mm/aaaa" ou None
  homologation_note / production_note  -> "até" | "a partir de" | None
  schedule_version                     -> versão da linha do cronograma usada

O texto do documento NÃO é enviado a nenhum serviço externo. O resumo é um apoio
de triagem: sempre confira o PDF original antes de implementar.
"""

import io
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from . import config

# Limites de segurança (evitam baixar dezenas de PDFs caso o estado seja zerado)
MAX_SUMMARIES_PER_RUN = 10
MAX_AGE_DAYS = 60
MAX_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 120
DOWNLOAD_TIMEOUT = 40

ITEMS_PER_SECTION = 5
BULLET_MAX_CHARS = 260

_SKIP_HREF = re.compile(r"^(#|javascript:|mailto:)", re.IGNORECASE)


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


# ---------------------------------------------------------------------------
# Limpeza de links
# ---------------------------------------------------------------------------
# Parâmetros que o servidor ASP.NET do portal acrescenta SOZINHO durante o teste de cookies.
_VOLATILE_PARAMS = {"aspxautodetectcookiesupport"}
# Sessão "sem cookie" do ASP.NET embutida no caminho: /(S(abc123))/pagina.aspx
_RE_COOKIELESS_SESSION = re.compile(r"/\((?:[SAF]\([^)]*\))+\)(?=/)", re.IGNORECASE)


def clean_document_url(url):
    """Remove da URL os trechos temporários que o portal acrescenta nos redirecionamentos
    (AspxAutoDetectCookieSupport e ID de sessão no caminho), preservando o restante
    exatamente como está (inclusive o "=" no fim do valor de "conteudo")."""
    if not url:
        return url
    parts = urlsplit(url)
    path = _RE_COOKIELESS_SESSION.sub("", parts.path)
    kept = [p for p in parts.query.split("&")
            if p and p.split("=", 1)[0].lower() not in _VOLATILE_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(kept), parts.fragment))


# ---------------------------------------------------------------------------
# 1) Localizar o link do documento no HTML do portal
# ---------------------------------------------------------------------------
def find_document_url(page_html, page_url, item):
    """Procura, no HTML da página de listagem, o link da NT descrita em `item`.

    Acha o texto que contém o código (ex.: 2026.009), prefere o que também
    contém a versão, e usa o link que envolve o texto (ou o primeiro link logo
    depois dele, caso do padrão "... (Leia mais)").
    """
    code = item.get("code")
    version = (item.get("version") or "").lower()
    if not code or not page_html:
        return None

    soup = BeautifulSoup(page_html, "lxml")
    candidates = []
    for idx, node in enumerate(soup.find_all(string=re.compile(re.escape(code)))):
        own_text = _norm(node)
        score = 0
        if version and version in own_text:
            score += 3
        parent = node.parent
        if parent is not None:
            parent_text = _norm(parent.get_text(" "))
            if version and len(parent_text) < 400 and version in parent_text:
                score += 1
        candidates.append((-score, idx, node))
    candidates.sort(key=lambda c: (c[0], c[1]))

    for _, _, node in candidates:
        anchor = node.find_parent("a", href=True)
        if anchor is not None and not _SKIP_HREF.match(anchor["href"]):
            return clean_document_url(urljoin(page_url, anchor["href"]))
        for nxt in node.find_all_next("a", href=True, limit=3):
            if not _SKIP_HREF.match(nxt["href"]):
                return clean_document_url(urljoin(page_url, nxt["href"]))
    return None


# ---------------------------------------------------------------------------
# 2) Baixar e extrair o texto
# ---------------------------------------------------------------------------
def _pdf_to_text(data):
    from pypdf import PdfReader  # import tardio: só é necessário aqui

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - uma página ruim não deve derrubar o resumo
            continue
    return "\n".join(parts)


def get_document_text(url, depth=0):
    """Baixa `url` e devolve (texto, url_final_limpa).

    - Se for PDF: extrai o texto.
    - Se for uma página HTML com link para PDF (ex.: exibirArquivo.aspx): segue
      o link uma vez.
    - Se for uma página HTML sem PDF (caso dos Informes): usa o texto da página.

    A URL devolvida é a final (depois dos redirecionamentos), SEM os marcadores
    temporários do portal (ver clean_document_url).
    """
    resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    content = resp.content[:MAX_BYTES]
    ctype = resp.headers.get("Content-Type", "").lower()
    final_url = clean_document_url(resp.url)

    if content[:5] == b"%PDF-" or "application/pdf" in ctype:
        return _pdf_to_text(content), final_url

    if "charset" not in ctype:  # sem charset declarado: requests assumiria ISO-8859-1
        resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    if depth == 0:
        # 1ª passada: links que apontam claramente para PDF
        # 2ª passada: links cujo texto fala em PDF/baixar/download (exceto zip)
        for strict in (True, False):
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if _SKIP_HREF.match(href) or re.search(r"\.(zip|rar|xsd)(\?|$)", href, re.I):
                    continue
                text = a.get_text(" ", strip=True)
                if strict:
                    ok = re.search(r"\.pdf(\?|$)|exibirArquivo", href, re.I)
                else:
                    ok = re.search(r"\bpdf\b|baixar|download", text, re.I)
                if ok:
                    try:
                        return get_document_text(urljoin(resp.url, href), depth=1)
                    except Exception:  # noqa: BLE001
                        continue

    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    blocks = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"]):
        block = tag.get_text(" ", strip=True)
        if block:
            blocks.append(block if block[-1] in ".;:!?" else block + ".")
    text = "\n".join(blocks)
    if len(text) < 200:  # página sem tags de bloco: usa o texto inteiro
        text = soup.get_text("\n", strip=True)
    return text, final_url


# ---------------------------------------------------------------------------
# 3) Analisar o texto e montar o resumo
# ---------------------------------------------------------------------------
_RE_REMOVE = re.compile(
    r"\b(exclu[ií]d\w*|exclus[ãa]o|exclu[ií]r|remo[çc]\w*|remov\w*|elimin\w*|"
    r"descontinu\w*|suprim\w*|retirad\w*)",
    re.IGNORECASE,
)
_RE_NEW = re.compile(
    r"\b(inclu[ií]d\w*|inclus[ãa]o|inclu[ií]r|inclui\b|incluem|criad[oa]s?|cria[çc][ãa]o|criar|"
    r"acrescent\w*|adi[çc][ãa]o|adicion\w*|"
    r"nov[oa]s?\s+(?:campo|grupo|evento|servi[cç]o|c[óo]digo|leiaute|layout|schema|tag|regra|"
    r"valida\w*|tabela|situa\w*|tipo|vers[ãa]o))",
    re.IGNORECASE,
)
_RE_CHANGE = re.compile(
    r"\b(altera\w*|modific\w*|ajust\w*|atualiz\w*|substitu\w*|passa(?:m|r[áa])?\s+a\b|"
    r"tornad\w*|torna-se|obrigatori\w*|facultativ\w*|redu[çc]\w*|ampli\w*|corre[çc]\w*|corrig\w*)",
    re.IGNORECASE,
)
_RE_RULE_CODE = re.compile(r"\b\d?[A-Z]{1,3}\d{2,3}[a-z]?-\d{1,3}\b")
_RE_REJEICAO = re.compile(r"Rejei[çc][ãa]o\s*[:\-]?\s*(?:n[ºo°.]*\s*)?(\d{3})\b", re.IGNORECASE)
_RE_FIELD_ID = re.compile(r"\b[A-Z]{1,3}\d{2,3}[a-z]?\b(?!-\d)")
_RE_ELEMENT_WORD = re.compile(r"\b(campo|grupo|tag|elemento|evento|leiaute|schema)\b", re.IGNORECASE)
_RE_RULE_SENT = re.compile(r"(rejei[çc][ãa]o|regras?\s+de\s+valida[çc][ãa]o)", re.IGNORECASE)
_DATE = r"(\d{2}/\d{2}/\d{4})"

_SECTIONS = (
    ("removed", "Exclusões / remoções", _RE_REMOVE),
    ("new", "Inclusões", _RE_NEW),
    ("changed", "Alterações", _RE_CHANGE),
)

_RE_HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^\n]{2,90}$")
_RE_HEADING_WORD = re.compile(
    r"^(Objetivo|Resumo|Introdu[çc][ãa]o|Apresenta[çc][ãa]o|Justificativa|Cronograma|Prazos?)$", re.IGNORECASE
)
_RE_NEXT_HEADING = re.compile(r"\s\d{1,2}(?:\.\d{1,2})*\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]")


def _prepare_flat(text):
    """Junta as linhas do PDF em texto corrido, mas isola os títulos de seção
    (ex.: "2. Alterações no leiaute") para que não grudem na frase seguinte."""
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
        if not line:
            continue
        is_heading = _RE_HEADING.match(line) or _RE_HEADING_WORD.match(line)
        if is_heading and line[-1] not in ".;:,!?)":
            line += "."
        lines.append(line)
    return " ".join(lines)


def _clip(text, limit=BULLET_MAX_CHARS):
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"


def _split_sentences(flat):
    parts = re.split(r"(?<=[.;!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ•\-–\d])", flat)
    return [p.strip() for p in parts if p.strip()]


def _is_noise(sentence):
    if len(sentence) < 35 or len(sentence) > 900:
        return True
    if "...." in sentence or ". . . ." in sentence:  # sumário com linha pontilhada
        return True
    letters = sum(ch.isalpha() for ch in sentence)
    if letters / max(len(sentence), 1) < 0.55:  # linha de tabela / números
        return True
    if re.match(r"^(página|pág\.|nota técnica\s+\d{4}\.\d{3}\s*$)", sentence, re.IGNORECASE):
        return True
    return False


def _score(sentence):
    score = len(_RE_FIELD_ID.findall(sentence)) + 2 * len(_RE_RULE_CODE.findall(sentence))
    if _RE_ELEMENT_WORD.search(sentence):
        score += 1
    if _RE_RULE_SENT.search(sentence):
        score += 1
    return score


def _extract_purpose(flat, fallback):
    # (?=[A-Z...]) cobre PDFs que "colam" o título na frase seguinte: "ObjetivoEsta Nota Técnica..."
    for m in re.finditer(
        r"\b(?:Objetivo|Resumo|Introdu[çc][ãa]o|Apresenta[çc][ãa]o)(?:\b|(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ]))[:.\s]*", flat
    ):
        snippet = flat[m.end(): m.end() + 900]
        cut = _RE_NEXT_HEADING.search(snippet)  # o objetivo termina onde começa a próxima seção
        if cut:
            snippet = snippet[:cut.start()]
        if "...." in snippet[:150] or ". . ." in snippet[:150]:
            continue  # é o sumário, não o corpo do texto
        picked, total = [], 0
        for sentence in _split_sentences(snippet):
            if len(sentence) < 30:
                continue
            picked.append(sentence)
            total += len(sentence)
            if len(picked) >= 2 or total >= 350:
                break
        if picked:
            return _clip(" ".join(picked), 480)
    return fallback


# ---------------------------------------------------------------------------
# Datas de HOMOLOGAÇÃO e PRODUÇÃO
# ---------------------------------------------------------------------------
_MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
# 15/10/2026   |   15 / 10 / 2026 (o PDF às vezes separa "01" de "/09/2026")   |   15 de outubro de 2026
_RE_ANY_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\s?/\s?(\d{1,2})\s?/\s?(\d{4})(?!\d)"
    r"|(?<!\d)(\d{1,2})\s+de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|"
    r"outubro|novembro|dezembro)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
# "Implantação Teste" e "ambiente de teste" são o ambiente de HOMOLOGAÇÃO
_RE_ROLLOUT_KW = re.compile(
    r"(?P<h>homolog\w*|homol\.|ambientes?\s+de\s+teste\b|implanta[çc][ãa]o\s+(?:em\s+|no\s+)?teste\b)"
    r"|(?P<p>produ[çc][ãa]o|\bprod\.)",
    re.IGNORECASE,
)
_RE_BOUNDARY = re.compile(r"[.;|]\s")
_RE_PUBLISHED_CTX = re.compile(r"publica\w*\s*(?:em|de|:)?\s*$", re.IGNORECASE)
_RE_OTHER_DEADLINE = re.compile(
    r"(vig[êe]ncia|entra(?:r[áa])?\s+em\s+vigor|implanta[çc][ãa]o|a partir de|prazo|obrigatoriedade)", re.IGNORECASE
)
_RE_QUALIFIER = re.compile(r"(?P<q>at[ée]|a\s+partir\s+(?:de|do)|desde)\s*[:\-]?\s*$", re.IGNORECASE)
_CLAUSE_WINDOW = 120   # quantos caracteres antes da data são lidos em busca do "homologação"/"produção"
_AFTER_WINDOW = 60     # idem, depois da data (formato "15/10/2026 em homologação")
_PAIR_GAP = 50         # "homologação e produção: 01/12" -> a data vale para os dois


def _match_to_date(m):
    """Converte o match de _RE_ANY_DATE em 'dd/mm/aaaa' (None se a data for inválida)."""
    try:
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, y = int(m.group(4)), int(m.group(6))
            mo = _MONTHS_PT[m.group(5).lower().replace("ç", "c")]
        datetime(y, mo, d)
        return f"{d:02d}/{mo:02d}/{y}"
    except (ValueError, KeyError):
        return None


def _qualifier_before(text, start):
    """'até' | 'a partir de' | None, conforme a palavra que vem logo antes da data."""
    m = _RE_QUALIFIER.search(text[max(0, start - 20):start])
    if not m:
        return None
    return "até" if m.group("q").lower().startswith("at") else "a partir de"


def _with_note(note, date):
    return f"{note} {date}" if note else date


def _rollout_keywords(flat, start, end, consumed):
    found = []
    for m in _RE_ROLLOUT_KW.finditer(flat[start:end]):
        pos = start + m.start()
        if pos in consumed:
            continue
        found.append((pos, start + m.end(), "homologation" if m.group("h") else "production"))
    return found


# ---- (a) Tabela "Histórico de Alterações / Cronograma" --------------------
_KIND = r"(?:Teste|Homologa[çc][ãa]o|Produ[çc][ãa]o)"
# Cobre "Implantação Teste Implantação Produção" e a leitura intercalada do PDF
# "Implantação Implantação Teste Produção".
_RE_SCHEDULE_HEADER = re.compile(
    r"Implanta[çc][ãa]o\s+(?:Implanta[çc][ãa]o\s+)?(?:(?:em|no|nos)\s+)?(?P<a>" + _KIND + r")"
    r"\s+(?:Implanta[çc][ãa]o\s+)?(?:(?:em|no|nos)\s+)?(?P<b>" + _KIND + r")\b",
    re.IGNORECASE,
)
# palavras do cabeçalho que o PDF pode colocar DEPOIS das colunas de implantação
_RE_HEADER_TAIL = re.compile(
    r"(?:\s|Vers[ãa]o|Hist[óo]rico\s+de\s+atualiza[çc][õo]es|Descri[çc][ãa]o)*", re.IGNORECASE
)
_RE_VERSION_TOKEN = re.compile(r"(?<![\w./,\-])(\d{1,2}\.\d{1,2}[a-z]?)(?=\s)", re.IGNORECASE)
# o que pode existir ENTRE duas datas da mesma linha da tabela: "03/11/2026 - Até 05/10/2026"
_GAP_BETWEEN_DATES = re.compile(
    r"[\s\-–—:|()]*(?:(?:at[ée]|a\s+partir\s+(?:de|do)|desde|em)\s*)?[\s\-–—:|()]*", re.IGNORECASE
)
_RE_ROW_BOUNDARY = re.compile(r"(?:\d{4}|[-–—]|n/?a)\s*$", re.IGNORECASE)
_ROW_LOOKAHEAD = 600   # tamanho máximo lido para a última linha da tabela
_TABLE_SCAN = 5000     # quanto texto depois do cabeçalho é examinado


def _kind_of(word):
    return "production" if word.lower().startswith("produ") else "homologation"


def _version_key(version):
    m = re.match(r"\s*v?\.?\s*(\d+)\.(\d+)([a-z]?)", version or "", re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2)), m.group(3).lower()) if m else (-1, -1, "")


def _same_version(a, b):
    ka, kb = _version_key(a), _version_key(b)
    return ka[:2] == kb[:2] and ka[0] >= 0


def _date_clusters(text):
    """Agrupa datas consecutivas (colunas da mesma linha da tabela)."""
    clusters, current = [], []
    for m in _RE_ANY_DATE.finditer(text):
        if current and _GAP_BETWEEN_DATES.fullmatch(text[current[-1].end():m.start()]):
            current.append(m)
        else:
            if current:
                clusters.append(current)
            current = [m]
    if current:
        clusters.append(current)
    return clusters


def _pick_cluster(clusters):
    """Primeiro grupo com 2 datas (teste + produção). Sem ele, nada é inferido."""
    for cluster in clusters:
        if len(cluster) >= 2:
            return cluster[:2]
    return None


def _schedule_rows(flat, body_start, body_end):
    """Divide o corpo da tabela em linhas, uma por versão."""
    body = flat[body_start:body_end]
    starts = []
    for m in _RE_VERSION_TOKEN.finditer(body):
        before = body[(starts[-1][0] if starts else 0):m.start()].rstrip()
        if not starts:
            # 1ª linha: o número da versão vem logo depois do cabeçalho
            ok = m.start() <= 60 and not _RE_ANY_DATE.search(body[:m.start()])
        else:
            # demais: a linha anterior terminou em data / traço / "n/a"
            ok = bool(_RE_ROW_BOUNDARY.search(before))
        if ok:
            starts.append((m.start(), m.group(1)))
    rows = []
    for i, (pos, version) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        text = body[pos:end]
        if i + 1 == len(starts):  # última linha: não ler texto que já é de outra seção
            text = text[:_ROW_LOOKAHEAD]
            cut = _RE_NEXT_HEADING.search(text, 8)
            if cut:
                text = text[:cut.start()]
        rows.append({"version": version, "offset": body_start + pos, "text": text})
    return rows


def _extract_schedule_table(flat, version=None):
    """Lê a tabela de cronograma da NT. Devolve None se o documento não a tem.

    Retorno: {"homologation", "production", "homologation_note", "production_note",
              "version", "start", "end"} - start/end delimitam a tabela no texto.
    """
    header = None
    for m in _RE_SCHEDULE_HEADER.finditer(flat):
        if _kind_of(m.group("a")) != _kind_of(m.group("b")):
            header = m
            break
    if header is None:
        return None
    order = (_kind_of(header.group("a")), _kind_of(header.group("b")))

    body_start = header.end() + _RE_HEADER_TAIL.match(flat, header.end()).end() - header.end()
    body_end = min(len(flat), body_start + _TABLE_SCAN)
    rows = _schedule_rows(flat, body_start, body_end)

    result = {"homologation": None, "production": None, "homologation_note": None,
              "production_note": None, "version": None, "start": header.start(), "end": body_start}
    chosen, cluster, row_text_offset = None, None, 0

    if rows:
        if version:
            chosen = next((r for r in rows if _same_version(r["version"], version)), None)
        if chosen is None:  # sem versão informada (ou não encontrada): vale a mais alta da tabela
            chosen = max(enumerate(rows), key=lambda ir: (_version_key(ir[1]["version"]), ir[0]))[1]
        cluster = _pick_cluster(_date_clusters(chosen["text"]))
        row_text_offset = chosen["offset"]
        result["version"] = chosen["version"]
        result["end"] = max(result["end"], chosen["offset"] + len(chosen["text"]))
        text_of_cluster = chosen["text"]
    else:
        # Não deu para separar as linhas: usa o último par de datas logo após o cabeçalho
        region = flat[body_start:body_start + 700]
        cut = _RE_NEXT_HEADING.search(region)
        if cut:
            region = region[:cut.start()]
        pairs = [c[:2] for c in _date_clusters(region) if len(c) >= 2]
        cluster = pairs[-1] if pairs else None
        row_text_offset, text_of_cluster = body_start, region
        result["end"] = body_start + len(region)

    if cluster:
        for kind, m in zip(order, cluster):
            label = _match_to_date(m)
            if label:
                result[kind] = label
                result[kind + "_note"] = _qualifier_before(text_of_cluster, m.start())
    return result


# ---- (b) Frases do texto --------------------------------------------------
def _extract_rollout_generic(flat):
    """Procura, em cada data do texto, a palavra "homologação"/"produção" na
    mesma oração (antes da data; se não houver, logo depois). Cobre formatos como:
      - "Homologação: 15/10/2026 | Produção: 01/12/2026"
      - "disponível em homologação a partir de 15/10/2026 e em produção em 01/12/2026"
      - "a partir de 15/10/2026 em homologação e 01/12/2026 em produção"
      - "ambientes de homologação e produção a partir de 01/12/2026" (vale para os dois)
    """
    # Datas inválidas (ex.: 31/02/2026) entram com label None: não valem como prazo,
    # mas continuam delimitando a oração e "consumindo" a palavra que as acompanha.
    dates = [(m.start(), m.end(), _match_to_date(m)) for m in _RE_ANY_DATE.finditer(flat)]

    result = {"homologation": None, "production": None, "homologation_note": None, "production_note": None}
    consumed = set()
    prev_end = 0
    for i, (start, end, label) in enumerate(dates):
        window_start = max(prev_end, start - _CLAUSE_WINDOW)
        chunk = flat[window_start:start]
        cut = max((m.end() for m in _RE_BOUNDARY.finditer(chunk)), default=0)
        clause_start = window_start + cut
        prev_end = end

        if _RE_PUBLISHED_CTX.search(flat[clause_start:start]):
            continue  # "publicada em 05/10/2026" é data de publicação, não de implantação

        found = _rollout_keywords(flat, clause_start, start, consumed)
        if found:
            last = found[-1]
            chosen = [last]
            for other in reversed(found[:-1]):
                if other[2] != last[2] and last[0] - other[1] <= _PAIR_GAP:
                    chosen.append(other)
                    break
        else:
            next_start = dates[i + 1][0] if i + 1 < len(dates) else len(flat)
            after_end = min(next_start, end + _AFTER_WINDOW)
            stop = _RE_BOUNDARY.search(flat[end:after_end])
            if stop:
                after_end = end + stop.start()
            chosen = _rollout_keywords(flat, end, after_end, consumed)[:1]

        for pos, _, kind in chosen:
            consumed.add(pos)
            if label and result[kind] is None:
                result[kind] = label
                result[kind + "_note"] = _qualifier_before(flat, start)
    return result


def extract_rollout_dates(flat, version=None):
    """Datas em que a NT entra em HOMOLOGAÇÃO (Implantação Teste) e em PRODUÇÃO.

    Primeiro lê a tabela "Histórico de Alterações / Cronograma" (linha da versão
    `version`, ou a mais alta); o que faltar é buscado nas frases do texto.
    Nunca inventa data: se o documento não traz, devolve None.
    """
    result = {"homologation": None, "production": None, "homologation_note": None,
              "production_note": None, "schedule_version": None}
    text = flat
    table = _extract_schedule_table(flat, version)
    if table:
        for key in ("homologation", "production", "homologation_note", "production_note"):
            result[key] = table[key]
        result["schedule_version"] = table["version"]
        if result["homologation"] and result["production"]:
            return result
        # tabela incompleta: complementa com as frases, ignorando o texto da própria tabela
        text = flat[:table["start"]] + " | " + flat[table["end"]:]

    generic = _extract_rollout_generic(text)
    for kind in ("homologation", "production"):
        if result[kind] is None and generic[kind]:
            result[kind] = generic[kind]
            result[kind + "_note"] = generic[kind + "_note"]
    return result


def _schedule_excerpt(flat):
    """Trecho bruto do cronograma, como o PDF foi lido (usado só para diagnóstico)."""
    for m in _RE_SCHEDULE_HEADER.finditer(flat):
        return flat[max(0, m.start() - 40): m.start() + 420].strip()
    return None


_RE_VERSION_CONTROL = re.compile(r"Controle\s+de\s+Vers[õo]es", re.IGNORECASE)
_RE_SCHEDULE_HEADING = re.compile(r"Hist[óo]rico\s+de\s+Altera[çc][õo]es(?:\s*/\s*Cronograma)?", re.IGNORECASE)


def _strip_version_tables(flat, table):
    """Remove do texto as tabelas "Controle de Versões" e "Histórico de Alterações /
    Cronograma". Elas descrevem o histórico do documento, não as mudanças que a NT
    propõe, e não podem entrar no "Resumo das alterações"."""
    cuts = []
    if table:
        start = table["start"]
        back = flat[max(0, start - 200):start]
        headings = list(_RE_SCHEDULE_HEADING.finditer(back))
        if headings:
            start = max(0, start - 200) + headings[-1].start()
        cuts.append((start, table["end"]))
    for m in _RE_VERSION_CONTROL.finditer(flat):
        end = min(len(flat), m.end() + 600)
        nxt = _RE_NEXT_HEADING.search(flat, m.end())
        if nxt:
            end = min(end, nxt.start())
        if table:  # termina onde começa a tabela de cronograma, quando ela vem logo depois
            sched = max(0, table["start"] - 200)
            heading = _RE_SCHEDULE_HEADING.search(flat, m.end())
            if heading and heading.start() < end:
                end = heading.start()
            elif sched > m.end():
                end = min(end, sched)
        cuts.append((m.start(), end))
    for start, end in sorted(cuts, reverse=True):
        flat = flat[:start] + " . " + flat[end:]
    return flat


def _extract_deadlines(rollout, sentences):
    """Lista de prazos em texto: homologação, produção e (se faltar alguma das
    duas) até 2 frases sobre vigência/implantação para o leitor conferir."""
    deadlines = []
    if rollout["homologation"]:
        deadlines.append(f"Homologação: {_with_note(rollout['homologation_note'], rollout['homologation'])}")
    if rollout["production"]:
        deadlines.append(f"Produção: {_with_note(rollout['production_note'], rollout['production'])}")
    if not (rollout["homologation"] and rollout["production"]):
        extra = 0
        for sentence in sentences:
            if _RE_OTHER_DEADLINE.search(sentence) and re.search(_DATE, sentence) and not _is_noise(sentence):
                deadlines.append(_clip(sentence))
                extra += 1
            if extra >= 2:
                break
    return deadlines


def rollout_info(cs):
    """Datas de implantação de um change_summary:
    {"homologation", "production", "homologation_note", "production_note", "schedule_version"}.

    Aceita também resumos antigos (gravados antes desta versão), que só tinham a
    lista de texto 'deadlines' ("Homologação: até 15/10/2026").
    """
    info = {"homologation": None, "production": None, "homologation_note": None,
            "production_note": None, "schedule_version": None}
    if not cs or cs.get("status") != "ok":
        return info
    for key in info:
        info[key] = cs.get(key)
    if info["homologation"] is None and info["production"] is None:
        qual = r"\s*(?:(até|a partir de)\s+)?"
        for line in cs.get("deadlines") or []:
            m = re.match(r"\s*Homologa[çc][ãa]o:" + qual + _DATE, line, re.IGNORECASE)
            if m and info["homologation"] is None:
                info["homologation_note"], info["homologation"] = (m.group(1) or None), m.group(2)
            m = re.match(r"\s*Produ[çc][ãa]o:" + qual + _DATE, line, re.IGNORECASE)
            if m and info["production"] is None:
                info["production_note"], info["production"] = (m.group(1) or None), m.group(2)
    return info


def rollout_dates(cs):
    """(homologação, produção) em 'dd/mm/aaaa' ou None."""
    info = rollout_info(cs)
    return info["homologation"], info["production"]


def summarize_text(text, item=None):
    """Gera o resumo estruturado a partir do texto bruto do documento."""
    item = item or {}
    flat = _prepare_flat(text)
    # frases e objetivo vêm do texto SEM as tabelas de versões/cronograma; as datas, do texto completo
    body = _strip_version_tables(flat, _extract_schedule_table(flat, item.get("version")))
    sentences = _split_sentences(body)
    purpose = _extract_purpose(body, item.get("summary"))
    purpose_norm = _norm(purpose)

    # Classifica cada frase na primeira categoria que casar (exclusão > inclusão > alteração)
    buckets = {key: [] for key, _, _ in _SECTIONS}
    seen = set()
    for idx, sentence in enumerate(sentences):
        if _is_noise(sentence):
            continue
        norm = _norm(sentence)
        if norm in seen or (purpose_norm and (norm[:80] in purpose_norm or purpose_norm[:60] in norm)):
            continue
        for key, _, regex in _SECTIONS:
            if regex.search(sentence):
                buckets[key].append((idx, sentence))
                seen.add(norm)
                break

    sections, chosen_sentences = [], []
    display_order = {"new": 0, "changed": 1, "removed": 2}
    for key, title, _ in sorted(_SECTIONS, key=lambda sec: display_order[sec[0]]):
        ranked = sorted(buckets[key], key=lambda x: (-_score(x[1]), x[0]))[:ITEMS_PER_SECTION]
        ranked.sort(key=lambda x: x[0])
        if ranked:
            sections.append({"key": key, "title": title, "items": [_clip(s) for _, s in ranked]})
            chosen_sentences.extend(s for _, s in ranked)

    def ordered_unique(matches):
        out = []
        for value in matches:
            if value not in out:
                out.append(value)
        return out

    elements = ordered_unique(_RE_FIELD_ID.findall(" ".join(chosen_sentences)))[:20]
    if not elements:
        elements = ordered_unique(_RE_FIELD_ID.findall(flat))[:12]

    rule_codes = ordered_unique(_RE_RULE_CODE.findall(flat))[:12]
    rejections = ordered_unique(_RE_REJEICAO.findall(flat))[:10]
    rules = [f"Regra {c}" for c in rule_codes] + [f"Rejeição {r}" for r in rejections]

    rollout = extract_rollout_dates(flat, item.get("version"))
    summary = {
        "status": "ok",
        "purpose": purpose,
        "sections": sections,
        "elements": elements,
        "rules": rules,
        "homologation": rollout["homologation"],
        "production": rollout["production"],
        "homologation_note": rollout["homologation_note"],
        "production_note": rollout["production_note"],
        "schedule_version": rollout["schedule_version"],
        "deadlines": _extract_deadlines(rollout, sentences),
    }
    excerpt = _schedule_excerpt(flat)
    if excerpt:
        summary["schedule_excerpt"] = excerpt
    return summary


# ---------------------------------------------------------------------------
# 4) Orquestração
# ---------------------------------------------------------------------------
def _unavailable(reason, item, doc_url=None):
    return {
        "status": "unavailable",
        "reason": reason,
        "purpose": item.get("summary"),
        "sections": [],
        "elements": [],
        "rules": [],
        "homologation": None,
        "production": None,
        "homologation_note": None,
        "production_note": None,
        "schedule_version": None,
        "deadlines": [],
        "doc_url": clean_document_url(doc_url),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_summary(item, page_html, page_url):
    doc_url = find_document_url(page_html, page_url, item)
    if not doc_url or doc_url.rstrip("/") == (page_url or "").rstrip("/"):
        return _unavailable("link do documento não localizado na página do portal", item)
    try:
        text, final_url = get_document_text(doc_url)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"falha ao baixar o documento ({type(exc).__name__})", item, doc_url)
    if len(re.sub(r"\s+", "", text)) < 200:
        return _unavailable("não foi possível extrair texto (documento escaneado ou vazio)", item, final_url)
    result = summarize_text(text, item)
    result["doc_url"] = clean_document_url(final_url)
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def enrich_items(items, pages):
    """Preenche item['change_summary'] (e item['doc_url']) para itens novos.

    `pages` = {source_key: (html, url)} com o HTML já baixado de cada portal.
    Respeita os limites MAX_SUMMARIES_PER_RUN e MAX_AGE_DAYS.
    """
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    done = 0
    for item in items:
        if done >= MAX_SUMMARIES_PER_RUN:
            print("[nt_summary] Limite de resumos por execução atingido.")
            break
        if item.get("date", "") < cutoff:
            continue
        page = pages.get(item.get("source_key"))
        if not page:
            continue
        summary = build_summary(item, page[0], page[1])
        item["change_summary"] = summary
        if summary.get("doc_url"):
            item["doc_url"] = summary["doc_url"]
        print(f"[nt_summary] {item.get('title')}: {summary['status']}"
              + (f" ({summary.get('reason')})" if summary["status"] != "ok" else ""))
        done += 1
    return done


def summary_to_text(cs):
    """Versão em texto puro (usada no e-mail texto, nos logs e nos testes)."""
    if not cs:
        return ""
    if cs.get("status") != "ok":
        return f"Resumo detalhado indisponível: {cs.get('reason', '')}"
    lines = []
    if cs.get("purpose"):
        lines.append(f"Objetivo: {cs['purpose']}")
    for sec in cs.get("sections", []):
        lines.append(f"{sec['title']}:")
        lines.extend(f"  - {it}" for it in sec["items"])
    if cs.get("elements"):
        lines.append("Campos/grupos citados: " + ", ".join(cs["elements"]))
    if cs.get("rules"):
        lines.append("Regras/rejeições citadas: " + ", ".join(cs["rules"]))
    if cs.get("deadlines"):
        lines.append("Prazos: " + " | ".join(cs["deadlines"]))
    return "\n".join(lines)
