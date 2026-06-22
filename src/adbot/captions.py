"""Generate a caption + headline for each content from the audience framework.

The framework (config/audience.md) and any per-asset script/brief are the INPUTS — the
model encodes the framework's precise-audience signals into compliant ad copy so Meta's
broad/Advantage+ (Andromeda) retrieval reaches the right people.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .creative_groups import CAROUSEL, Unit
from .logging import get_logger
from .settings import REPO_ROOT, Settings

AUDIENCE_PATH = Path(REPO_ROOT) / "config" / "audience.md"
SYSTEM_PROMPT_PATH = Path(REPO_ROOT) / "prompts" / "caption_system.md"


class AudienceNotReady(RuntimeError):
    """Raised when config/audience.md still holds template placeholders."""


def load_audience(settings: Settings) -> str:
    text = AUDIENCE_PATH.read_text(encoding="utf-8") if AUDIENCE_PATH.exists() else ""
    if not text.strip() or "TODO:" in text:
        raise AudienceNotReady(
            "config/audience.md still has TODO placeholders — paste Soo Cheng 老师's "
            "precise-audience framework before generating captions."
        )
    return text


def _brief(unit: Unit) -> Dict[str, Any]:
    return {
        "content_id": unit.content_id,
        "kind": unit.kind,
        "asset_names": [a.name for a in unit.assets],
        "num_cards": len(unit.assets) if unit.kind == CAROUSEL else None,
        "script_or_brief": unit.script_text or "",
    }


def generate_for_units(llm, settings: Settings, units: List[Unit]) -> Dict[str, Dict[str, Any]]:
    log = get_logger()
    audience = load_audience(settings)
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    out: Dict[str, Dict[str, Any]] = {}
    for unit in units:
        result = llm.generate_caption(system, audience, _brief(unit))
        result.setdefault("content_id", unit.content_id)
        out[unit.content_id] = result
        log.info("  [caption] %s -> %s", unit.content_id, result.get("headline", "")[:60])
    return out
