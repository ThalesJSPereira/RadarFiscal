"""
Orquestrador do monitor de NFe / CTe / MDFe.

Fluxo:
  1. Baixa cada fonte oficial (config.SOURCES) e aplica o parser correspondente.
  2. Compara com o estado salvo em data/state.json para descobrir:
       - itens novos (id nunca visto antes)
       - itens cuja Nota Técnica ganhou uma NOVA VERSÃO (mesmo código,
         versão diferente) -> é aqui que fica claro "o que mudou na estrutura"
  3. Atualiza data/state.json (fonte de verdade, com histórico completo).
  4. Gera docs/data.json, consumido pelo painel web (GitHub Pages).
  5. Dispara e-mail de alerta quando há novidade.

Uso:
    python -m scraper.main
"""

import sys
from datetime import datetime, timedelta, timezone

from . import config, store, notifier
from .fetcher import fetch, FetchError
from .parsers import PARSERS


def collect_all_items():
    all_items = []
    errors = []
    for source in config.SOURCES:
        parser = PARSERS[source["parser"]]
        try:
            html = fetch(source["url"])
        except FetchError as exc:
            print(f"[main] AVISO: {exc}")
            errors.append(str(exc))
            continue
        try:
            items = parser(html, source, source["base_url"])
        except Exception as exc:  # noqa: BLE001 - queremos seguir mesmo se um parser falhar
            print(f"[main] AVISO: parser '{source['parser']}' falhou para {source['url']}: {exc}")
            errors.append(str(exc))
            continue
        print(f"[main] {source['label']}: {len(items)} item(ns) extraído(s).")
        all_items.extend(items)
    return all_items, errors


def _version_key(version):
    """Normaliza a versão para comparação segura.

    Alguns itens extraídos dos portais não possuem uma versão explícita
    (campo None) - por exemplo, quando a Nota Técnica não traz "v.X.XX" no
    título. Sem essa normalização, comparar None > "1.00" quebra o Python
    com TypeError. Tratamos None (e valores vazios) como string vazia, que
    sempre perde na comparação contra uma versão real.
    """
    return version or ""


def diff_against_state(all_items, state):
    """Retorna (new_items, updated_items, merged_state)."""
    existing = state.get("items", {})
    new_items = []
    updated_items = []
    today = datetime.now(timezone.utc).date().isoformat()

    # Índice por (document, code) para detectar mudança de versão dentro do
    # mesmo ato normativo (ex.: NT 2025.002 v1.50 -> v1.51).
    latest_by_code = {}
    for existing_id, existing_item in existing.items():
        key = (existing_item.get("document"), existing_item.get("code"))
        if key[1] is None:
            continue
        current_best = latest_by_code.get(key)
        if current_best is None or _version_key(existing_item.get("version")) > _version_key(
            current_best.get("version")
        ):
            latest_by_code[key] = existing_item

    for item in all_items:
        item_id = item["id"]
        if item_id in existing:
            # já conhecido, apenas garante que o registro mais recente é mantido
            item["first_seen"] = existing[item_id].get("first_seen", today)
            existing[item_id] = item
            continue

        # item novo (id nunca visto)
        item["first_seen"] = today
        key = (item.get("document"), item.get("code"))
        prior = latest_by_code.get(key) if key[1] else None

        if prior and item.get("version") and item["version"] != prior.get("version"):
            item["previous_version"] = prior.get("version")
            item["previous_summary"] = prior.get("summary")
            updated_items.append(item)
        else:
            new_items.append(item)

        existing[item_id] = item
        if key[1]:
            latest_by_code[key] = item

    state["items"] = existing
    state["last_run"] = store.now_iso()
    return new_items, updated_items, state


def build_dashboard_payload(state):
    items = list(state.get("items", {}).values())
    items.sort(key=lambda x: x.get("date", ""), reverse=True)

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=config.NEW_BADGE_DAYS)).isoformat()
    for it in items:
        it["is_new"] = it.get("first_seen", "") >= cutoff

    counts = {}
    for it in items:
        counts[it["document"]] = counts.get(it["document"], 0) + 1

    payload = {
        "generated_at": store.now_iso(),
        "total_items": len(items),
        "counts_by_document": counts,
        "new_last_run": sum(1 for it in items if it.get("first_seen") == datetime.now(timezone.utc).date().isoformat()),
        "items": items,
    }
    return payload


def main():
    state = store.load_state()
    all_items, errors = collect_all_items()

    if not all_items and not state.get("items"):
        print("[main] Nenhum item coletado e nenhum estado anterior. Abortando sem gravar.")
        sys.exit(1 if errors else 0)

    new_items, updated_items, merged_state = diff_against_state(all_items, state)
    store.save_state(merged_state)

    dashboard_payload = build_dashboard_payload(merged_state)
    import json
    import os
    os.makedirs(os.path.dirname(config.DASHBOARD_DATA_FILE), exist_ok=True)
    with open(config.DASHBOARD_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)

    print(f"[main] Itens novos: {len(new_items)} | Novas versões: {len(updated_items)}")
    notifier.send_alert(new_items, updated_items)

    # Sinaliza para o workflow do GitHub Actions se algo mudou (usado para
    # decidir a mensagem do commit automático).
    github_output = os.environ.get("GITHUB_OUTPUT")
    changed = bool(new_items or updated_items)
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            f.write(f"new_count={len(new_items)}\n")
            f.write(f"updated_count={len(updated_items)}\n")

    if errors and not all_items:
        sys.exit(1)


if __name__ == "__main__":
    main()
