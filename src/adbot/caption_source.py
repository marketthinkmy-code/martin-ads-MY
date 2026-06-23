"""Load ad copy from the Notion content DB — the single source of truth for captions.

When ``notion.captions_source`` is true, the build reads each unit's Caption + Headline
(and, for carousels, the per-card texts from the page body) straight from Notion by
Content ID. The copy you reviewed/approved in Notion is therefore exactly what goes live —
there is never a second, LLM-generated version. A unit with no matching (and, if
``notion.require_status`` is set, correctly-statused) Notion row is a hard error: write and
approve the caption in Notion first.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .creative_groups import CAROUSEL, Unit
from .logging import get_logger


def _plain(prop: Dict[str, Any]) -> str:
    """Concatenate the plain text of a Notion title/rich_text property."""
    if not prop:
        return ""
    runs = prop.get("rich_text") or prop.get("title") or []
    return "".join(r.get("plain_text") or r.get("text", {}).get("content", "") for r in runs)


def _status(props: Dict[str, Any]) -> str:
    return ((props.get("Status") or {}).get("status") or {}).get("name", "")


_CARD_SEPS = ("—", "–", " - ")  # em dash / en dash / hyphen — author format is "name — desc"


def _carousel_cards(notion, page_id: str) -> List[Dict[str, str]]:
    """Parse '<name> — <description>' numbered-list items from the page body (best-effort)."""
    cards: List[Dict[str, str]] = []
    try:
        blocks = notion.get_block_children(page_id)
    except Exception:  # noqa: BLE001 - card texts are optional polish; never break a build
        return cards
    for b in blocks:
        if b.get("type") != "numbered_list_item":
            continue
        text = "".join(r.get("plain_text", "")
                       for r in b.get("numbered_list_item", {}).get("rich_text", [])).strip()
        for sep in _CARD_SEPS:
            if sep in text:
                name, desc = text.split(sep, 1)
                cards.append({"name": name.strip(), "description": desc.strip()})
                break
    return cards


def load_from_notion(notion, settings, units: List[Unit], *,
                     strict: bool = True) -> Dict[str, Dict[str, Any]]:
    """Map each unit's content_id to its reviewed Notion copy.

    ``strict`` (real build): a unit with no usable Notion row is a hard error.
    ``strict=False`` (dry-run preview): missing units are stubbed and reported, never raised.
    """
    log = get_logger()
    db = settings.notion.database_id
    require = (settings.notion.require_status or "").strip()
    out: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []

    for u in units:
        rows = notion.query_by_content_id(db, u.content_id)
        if require:
            rows = [p for p in rows if _status(p.get("properties", {})) == require]
        if not rows:
            missing.append(u.content_id)
            continue
        chosen = sorted(rows, key=lambda p: p.get("last_edited_time", ""), reverse=True)[0]
        props = chosen.get("properties", {})
        caption = _plain(props.get("Caption", {}))
        headline = _plain(props.get("Headline", {}))
        if not caption or not headline:
            missing.append(u.content_id)
            continue
        entry: Dict[str, Any] = {"content_id": u.content_id, "caption": caption,
                                 "headline": headline}
        if u.kind == CAROUSEL:
            entry["carousel_card_texts"] = _carousel_cards(notion, chosen.get("id", ""))
        out[u.content_id] = entry
        log.info("  [notion-caption] %s <- %s", u.content_id, headline[:50])

    if missing:
        hint = f" with Status={require!r}" if require else ""
        msg = ("No usable Notion caption" + hint + " for: " + ", ".join(missing)
               + " — write and approve the caption in Notion first (single source of truth).")
        if strict:
            raise RuntimeError(msg)
        log.warning("%s (dry-run: stubbing these)", msg)
        for cid in missing:
            out[cid] = {"content_id": cid, "caption": "<no approved Notion caption yet>",
                        "headline": "<no approved Notion caption yet>"}
    return out
