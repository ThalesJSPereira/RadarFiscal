"""
Localiza a Nota Técnica MAIS RECENTE publicada nos portais oficiais e gera o
"Resumo das alterações" dela. É a base dos testes (teste de e-mail e teste de
resumo): o teste sempre usa a última NT realmente publicada, nunca dados fictícios.

Regras de escolha:
  - Só entram Notas Técnicas (Informes Técnicos, Atos Conjuntos, manuais e
    schemas são ignorados).
  - Vale a maior data de publicação, entre todos os portais monitorados
    (NF-e/NFC-e, CT-e/BP-e e MDF-e).
  - Em caso de empate de data, vale a ordem em que a NT aparece na página do
    portal (o portal lista a mais nova primeiro); entre portais, a ordem de
    scraper/config.py (NF-e, CT-e, MDF-e).
  - Se a variável de ambiente TEST_DOCUMENT estiver preenchida (NFe, CTe, MDFe,
    BP-e), considera apenas NTs desse documento. Vazio ou "todos" = qualquer um.

Nada aqui altera data/state.json nem docs/data.json.
"""

import os
import re

from . import config, nt_summary
from .fetcher import fetch, FetchError
from .parsers import PARSERS

_RE_NT_TITLE = re.compile(r"nota[\s_]*t[ée]cnica", re.IGNORECASE)
_EXCLUDED_TYPES = {"Informe Técnico", "Ato Conjunto"}
_ALL_VALUES = {"", "todos", "todas", "all", "qualquer"}
TOP_N_LOG = 5


def _norm_doc(value):
    """'NF-e' / 'NFe' / 'nfe' -> 'nfe' (compara sem hífen, espaço ou maiúscula)."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def requested_document():
    """Documento pedido via TEST_DOCUMENT (None = qualquer documento)."""
    value = os.environ.get("TEST_DOCUMENT", "").strip()
    return None if _norm_doc(value) in _ALL_VALUES else value


def is_nota_tecnica(item):
    if item.get("category") == "Informes":
        return False
    if item.get("type") in _EXCLUDED_TYPES:
        return False
    return bool(_RE_NT_TITLE.search(item.get("title", "")))


def collect_candidates(document=None):
    """Baixa cada portal e devolve (candidatas, erros).

    Cada candidata guarda o item e o HTML da página de origem (necessário para
    o resumo localizar o link do documento). Um portal fora do ar não impede o
    teste: ele só é registrado em `erros`.
    """
    wanted = _norm_doc(document) if document else ""
    if wanted in _ALL_VALUES:
        wanted = ""

    candidates, errors = [], []
    for source in config.SOURCES:
        try:
            html = fetch(source["url"])
        except FetchError as exc:
            print(f"[latest_nt] AVISO: {exc}")
            errors.append(str(exc))
            continue
        try:
            items = PARSERS[source["parser"]](html, source, source["base_url"])
        except Exception as exc:  # noqa: BLE001
            print(f"[latest_nt] AVISO: parser '{source['parser']}' falhou: {exc}")
            errors.append(str(exc))
            continue
        for item in items:
            if not is_nota_tecnica(item):
                continue
            if wanted and _norm_doc(item.get("document")) != wanted:
                continue
            candidates.append({"item": item, "html": html, "url": source["url"]})
    return candidates, errors


def rank_candidates(candidates):
    """Mais recente primeiro. O sort é estável: no empate de data, mantém a
    ordem de coleta (ordem da página do portal)."""
    return sorted(candidates, key=lambda c: c["item"].get("date", ""), reverse=True)


def _fmt_date(iso):
    try:
        y, m, d = iso.split("-")
        return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        return iso or "-"


def build_latest_item(document=None):
    """Retorna o item da NT mais recente, já com `change_summary` e `doc_url`.

    Levanta LookupError se nenhum portal respondeu ou nenhuma NT foi encontrada.
    """
    candidates, errors = collect_candidates(document)
    if not candidates:
        filtro = f" do documento '{document}'" if document else ""
        detalhe = f" Erros: {' | '.join(errors)}" if errors else ""
        raise LookupError(f"Nenhuma Nota Técnica{filtro} encontrada nos portais.{detalhe}")

    ranked = rank_candidates(candidates)
    print("[latest_nt] Notas Técnicas mais recentes encontradas:")
    for pos, cand in enumerate(ranked[:TOP_N_LOG], start=1):
        it = cand["item"]
        mark = "  <== usada no teste" if pos == 1 else ""
        print(f"  {pos}. [{it['document']}] {it['title']} ({_fmt_date(it.get('date'))}){mark}")

    best = ranked[0]
    item = dict(best["item"])
    summary = nt_summary.build_summary(item, best["html"], best["url"])
    item["change_summary"] = summary
    if summary.get("doc_url"):
        item["doc_url"] = summary["doc_url"]
    print(f"[latest_nt] Resumo: {summary['status']}"
          + (f" ({summary.get('reason')})" if summary["status"] != "ok" else ""))
    return item
