"""
Resumo automático das alterações propostas por uma Nota Técnica (NT).

Para cada publicação NOVA (ou nova versão) detectada, este módulo localiza o
documento da NT no portal (normalmente um PDF), baixa, extrai o texto e
identifica, por regras de texto (sem IA externa): objetivo, inclusões,
alterações, exclusões, campos/grupos citados, regras/rejeições, e as DATAS DE
HOMOLOGAÇÃO E PRODUÇÃO.

O resultado fica em item["change_summary"] e é usado pelo painel e pelo e-mail.
As datas ficam em change_summary["homologation"] e change_summary["production"]
(formato dd/mm/aaaa, ou None quando o documento não traz a data).

O texto do documento NÃO é enviado a nenhum serviço externo. O resumo é um apoio
de triagem: sempre confira o PDF original antes de implementar.
"""

import io
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

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
            return urljoin(page_url, anchor["href"])
        for nxt in node.find_all_next("a", href=True, limit=3):
            if not _SKIP_HREF.match(nxt["href"]):
                return urljoin(page_url, nxt["href"])
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
    """Baixa `url` e devolve (texto, url_final).

    - Se for PDF: extrai o texto.
    - Se for uma página HTML com link para PDF (ex.: exibirArquivo.aspx): segue
      o link uma vez.
    - Se for uma página HTML sem PDF (caso dos Informes): usa o texto da página.
    """
    resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    content = resp.content[:MAX_BYTES]
    ctype = resp.headers.get("Content-Type", "").lower()

    if content[:5] == b"%PDF-" or "application/pdf" in ctype:
        return _pdf_to_text(content), resp.url

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
    return text, resp.url


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
_RE_RULE_CODE = re.compile(r"\b[A-Z]{1,3}\d{2,3}[a-z]?-\d{1,3}\b")
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
    for m in re.finditer(r"\b(?:Objetivo|Resumo|Introdu[çc][ãa]o|Apresenta[çc][ãa]o)\b[:.\s]+", flat):
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
# 15/10/2026   ou   15 de outubro de 2026
_RE_ANY_DATE = re.compile(
    r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"
    r"|(?<!\d)(\d{1,2})\s+de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|"
    r"outubro|novembro|dezembro)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_RE_ROLLOUT_KW = re.compile(r"(?P<h>homolog\w*|homol\.)|(?P<p>produ[çc][ãa]o|\bprod\.)", re.IGNORECASE)
_RE_BOUNDARY = re.compile(r"[.;|]\s")
_RE_PUBLISHED_CTX = re.compile(r"publica\w*\s*(?:em|de|:)?\s*$", re.IGNORECASE)
_RE_OTHER_DEADLINE = re.compile(
    r"(vig[êe]ncia|entra(?:r[áa])?\s+em\s+vigor|implanta[çc][ãa]o|a partir de|prazo|obrigatoriedade)", re.IGNORECASE
)
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


def _rollout_keywords(flat, start, end, consumed):
    found = []
    for m in _RE_ROLLOUT_KW.finditer(flat[start:end]):
        pos = start + m.start()
        if pos in consumed:
            continue
        found.append((pos, start + m.end(), "homologation" if m.group("h") else "production"))
    return found


def extract_rollout_dates(flat):
    """Acha as datas em que a NT entra em HOMOLOGAÇÃO e em PRODUÇÃO.

    Para cada data do texto, procura a palavra "homologação"/"produção" na
    mesma oração (antes da data; se não houver, logo depois). Cobre formatos como:
      - "Homologação: 15/10/2026 | Produção: 01/12/2026"
      - "disponível em homologação a partir de 15/10/2026 e em produção em 01/12/2026"
      - "a partir de 15/10/2026 em homologação e 01/12/2026 em produção"
      - "ambientes de homologação e produção a partir de 01/12/2026" (data vale para os dois)
    Retorna {"homologation": "dd/mm/aaaa"|None, "production": "dd/mm/aaaa"|None}.
    Nunca inventa data: se o documento não traz, devolve None.
    """
    # Datas inválidas (ex.: 31/02/2026) entram com label None: não valem como prazo,
    # mas continuam delimitando a oração e "consumindo" a palavra que as acompanha.
    dates = [(m.start(), m.end(), _match_to_date(m)) for m in _RE_ANY_DATE.finditer(flat)]

    result = {"homologation": None, "production": None}
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
    return result


def _extract_deadlines(rollout, sentences):
    """Lista de prazos em texto: homologação, produção e (se faltar alguma das
    duas) até 2 frases sobre vigência/implantação para o leitor conferir."""
    deadlines = []
    if rollout["homologation"]:
        deadlines.append(f"Homologação: {rollout['homologation']}")
    if rollout["production"]:
        deadlines.append(f"Produção: {rollout['production']}")
    if not (rollout["homologation"] and rollout["production"]):
        extra = 0
        for sentence in sentences:
            if _RE_OTHER_DEADLINE.search(sentence) and re.search(_DATE, sentence) and not _is_noise(sentence):
                deadlines.append(_clip(sentence))
                extra += 1
            if extra >= 2:
                break
    return deadlines


def rollout_dates(cs):
    """(homologação, produção) de um change_summary, em 'dd/mm/aaaa' ou None.

    Aceita também resumos antigos (gravados antes desta versão), que só tinham a
    lista de texto 'deadlines' ("Homologação: 15/10/2026").
    """
    if not cs or cs.get("status") != "ok":
        return None, None
    homologation, production = cs.get("homologation"), cs.get("production")
    if homologation is None and production is None:
        for line in cs.get("deadlines") or []:
            m = re.match(r"\s*Homologa[çc][ãa]o:\s*" + _DATE, line, re.IGNORECASE)
            if m and homologation is None:
                homologation = m.group(1)
            m = re.match(r"\s*Produ[çc][ãa]o:\s*" + _DATE, line, re.IGNORECASE)
            if m and production is None:
                production = m.group(1)
    return homologation, production


def summarize_text(text, item=None):
    """Gera o resumo estruturado a partir do texto bruto do documento."""
    item = item or {}
    flat = _prepare_flat(text)
    sentences = _split_sentences(flat)
    purpose = _extract_purpose(flat, item.get("summary"))
    purpose_norm = _norm(purpose)

    # Classifica cada frase na primeira categoria que casar (exclusão > inclusão > alteração)
    buckets = {key: [] for key, _, _ in _SECTIONS}
    seen = set()
    for idx, sentence in enumerate(sentences):
        if _is_noise(sentence):
            continue
        norm = _norm(sentence)
        if norm in seen or (purpose_norm and norm[:80] in purpose_norm):
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

    rollout = extract_rollout_dates(flat)
    return {
        "status": "ok",
        "purpose": purpose,
        "sections": sections,
        "elements": elements,
        "rules": rules,
        "homologation": rollout["homologation"],
        "production": rollout["production"],
        "deadlines": _extract_deadlines(rollout, sentences),
    }


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
        "deadlines": [],
        "doc_url": doc_url,
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
    result["doc_url"] = final_url
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
