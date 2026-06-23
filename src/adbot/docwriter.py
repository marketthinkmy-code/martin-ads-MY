"""Render the caption log and idea backlog into Google Docs (append in place)."""

from __future__ import annotations

from typing import Any, Dict, List

from . import state
from .creative_groups import Unit
from .logging import get_logger

CAPTION_DOC_TITLE = "{prefix} — Caption & Headline Log"
IDEA_DOC_TITLE = "{prefix} — Content Idea Backlog"


def _caption_block(content_id: str, kind: str, cap: Dict[str, Any]) -> str:
    signals = ", ".join(cap.get("encoded_audience_signals", []) or [])
    lines = [
        f"\n=== {content_id} ({kind}) ===",
        f"Headline: {cap.get('headline', '')}",
        f"Caption:\n{cap.get('caption', '')}",
        f"Encoded audience signals: {signals}",
    ]
    for i, card in enumerate(cap.get("carousel_card_texts") or [], start=1):
        lines.append(f"  Card {i}: {card.get('name', '')} — {card.get('description', '')}")
    lines.append(f"(updated {state.now_iso()})\n")
    return "\n".join(lines)


def _remember_doc(key: str, doc_id: str) -> None:
    index = state.load("doc_index")
    index[key] = doc_id
    state.save("doc_index", index)


def write_caption_log(docs, settings, units: List[Unit],
                      captions: Dict[str, Dict[str, Any]]) -> str:
    log = get_logger()
    configured = settings.google_docs.caption_log_doc_id
    doc_id = docs.ensure_doc(configured, CAPTION_DOC_TITLE.format(prefix=settings.naming.prefix),
                             settings.drive.creatives_folder_id)
    if not configured:
        log.info("Created caption-log Doc %s — paste into config.yaml google_docs.caption_log_doc_id", doc_id)
        _remember_doc("caption_log_doc_id", doc_id)

    body = f"\n##### Caption log run {state.now_iso()} #####\n"
    body += "".join(_caption_block(u.content_id, u.kind, captions.get(u.content_id, {})) for u in units)
    docs.append_text(doc_id, body)
    log.info("Wrote %d captions to the caption-log Doc.", len(units))
    return doc_id


def _idea_block(idea: Dict[str, Any]) -> str:
    return (f"\n--- {idea.get('title', '')} [{idea.get('format', '')}] ---\n"
            f"Angle: {idea.get('angle', '')}\n"
            f"Hook: {idea.get('hook', '')}\n"
            f"Target audience signal: {idea.get('target_signal', '')}\n"
            f"Generation prompt: {idea.get('generation_prompt', '')}\n")


def append_ideas(docs, settings, ideas: List[Dict[str, Any]]) -> int:
    log = get_logger()
    configured = settings.google_docs.idea_backlog_doc_id
    doc_id = docs.ensure_doc(configured, IDEA_DOC_TITLE.format(prefix=settings.naming.prefix),
                             settings.drive.creatives_folder_id)
    if not configured:
        log.info("Created idea-backlog Doc %s — paste into config.yaml google_docs.idea_backlog_doc_id", doc_id)
        _remember_doc("idea_backlog_doc_id", doc_id)

    existing = docs.read_text(doc_id)
    blocks = [f"\n##### Idea drop {state.now_iso()} #####\n"]
    added = 0
    for idea in ideas:
        title = idea.get("title", "")
        if title and title in existing:
            continue
        blocks.append(_idea_block(idea))
        added += 1
    if added:
        docs.append_text(doc_id, "".join(blocks))
    log.info("Appended %d new ideas (skipped %d duplicates).", added, len(ideas) - added)
    return added


_NOTION_TYPE = {"video": "Video", "carousel": "Carousel", "single_image": "Post"}


def write_notion_captions(notion, settings, units: List[Unit],
                          captions: Dict[str, Dict[str, Any]]) -> int:
    """Mirror each caption+headline into the configured Notion database (one row per unit)."""
    from .clients.notion import rich_text
    log = get_logger()
    db = settings.notion.database_id
    brand = settings.notion.brand or settings.naming.prefix
    today = state.now_iso()[:10]
    written = 0
    for u in units:
        cap = captions.get(u.content_id, {})
        signals = ", ".join(cap.get("encoded_audience_signals", []) or [])
        props = {
            "Title": {"title": rich_text(f"{settings.naming.prefix} | {u.content_id}")},
            "Type": {"select": {"name": _NOTION_TYPE.get(u.kind, settings.notion.default_type)}},
            "Caption": {"rich_text": rich_text(cap.get("caption", ""))},
            "Headline": {"rich_text": rich_text(cap.get("headline", ""))},
            "Hook": {"rich_text": rich_text(cap.get("headline", ""))},
            "Content ID": {"rich_text": rich_text(u.content_id)},
            "Audience Signals": {"rich_text": rich_text(signals)},
            "Status": {"status": {"name": settings.notion.default_status}},
            "Brand": {"rich_text": rich_text(brand)},
            "Date": {"date": {"start": today}},
        }
        try:
            notion.create_row(db, props)
            written += 1
        except Exception as exc:  # noqa: BLE001 - one bad row shouldn't stop the rest
            log.warning("  [notion] %s failed: %s", u.content_id, exc)
    log.info("Wrote %d/%d captions to Notion.", written, len(units))
    return written
