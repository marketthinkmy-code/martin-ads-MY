"""Committed JSON ledgers for idempotency + audit (under ``state/``).

Used as a within-run cache and a human-readable audit trail. The cloud routines do
NOT rely on these for cross-run coordination — the weekly OFF/ON cycle uses a Meta
**ad label** (stateless, survives fresh clones), and build/sync reconcile from Meta by
name. These files still give a local record and speed up repeat runs in one checkout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .settings import REPO_ROOT

STATE_DIR = Path(REPO_ROOT) / "state"


def _path(name: str) -> Path:
    return STATE_DIR / f"{name}.json"


def load(name: str, default: Any = None) -> Any:
    p = _path(name)
    if not p.exists():
        return {} if default is None else default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {} if default is None else default


def save(name: str, data: Any) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _path(name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_pause_log(entity_id: str, entity_type: str, reason: str, detail: Dict[str, Any]) -> None:
    """Append one audit row describing a pause/resume action."""
    log = load("pause_log", default=[])
    if not isinstance(log, list):
        log = []
    log.append({
        "ts": now_iso(),
        "entity_id": entity_id,
        "entity_type": entity_type,
        "reason": reason,
        **detail,
    })
    save("pause_log", log)
