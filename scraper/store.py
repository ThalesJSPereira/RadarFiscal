"""Persistência do estado do monitor (data/state.json)."""

import json
import os
from datetime import datetime, timezone

from . import config


def load_state():
    if not os.path.exists(config.STATE_FILE):
        return {"items": {}, "last_run": None}
    with open(config.STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"items": {}, "last_run": None}


def save_state(state):
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
