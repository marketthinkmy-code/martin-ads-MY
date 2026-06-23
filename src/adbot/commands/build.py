"""build: create (and optionally activate) the 1-1-10 structure + write the caption log."""

from __future__ import annotations

from typing import Any, Dict

from . import docs_client, drive_client, graph_client, llm_client, notion_client
from .. import build_1_1_10, docwriter, media
from ..caption_source import load_from_notion
from ..captions import generate_for_units
from ..drive_sync import download_assets, load_units
from ..logging import get_logger


def _notion_is_source(settings) -> bool:
    """True when captions must be READ from Notion (single source of truth)."""
    return bool(settings.notion.captions_source and settings.notion.enabled
                and settings.secrets.notion_api_key and settings.notion.database_id)


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    graph = graph_client(settings)
    drive = drive_client(settings)
    _, units = load_units(drive, settings)
    log.info("Loaded %d creative unit(s) from Drive.", len(units))
    notion_sourced = _notion_is_source(settings)
    label, state_key = settings.meta.build.label, settings.meta.build.state_key

    if dry_run:
        # Preview structure without uploading media or calling the LLM (no spend/cost). When
        # Notion is the caption source, pull the REAL copy (read-only) so the dry-run doubles
        # as a check that every unit already has an approved Notion caption.
        if notion_sourced:
            captions = load_from_notion(notion_client(settings), settings, units, strict=False)
        else:
            captions = {u.content_id: {"caption": "<generated on live run>",
                                       "headline": "<generated>"} for u in units}
        return build_1_1_10.build(graph, settings, units, captions, dry_run=True,
                                  label=label, state_key=state_key)

    download_assets(drive, units)
    media.sync_media(graph, settings, units, dry_run=False)

    if notion_sourced:
        # Read the operator-reviewed copy straight from Notion — no LLM, no second version.
        captions = load_from_notion(notion_client(settings), settings, units)
    else:
        llm = llm_client(settings)
        captions = generate_for_units(llm, settings, units)

    entities = build_1_1_10.build(graph, settings, units, captions, dry_run=False,
                                  label=label, state_key=state_key)

    # The Google Doc caption-log is a nicety; never fail a completed build over it (a service
    # account has no Drive storage quota to create Docs, and Notion already holds the captions).
    try:
        docwriter.write_caption_log(docs_client(settings), settings, units, captions)
    except Exception as exc:  # noqa: BLE001
        log.warning("Caption-log Doc write skipped (%s) — captions are in Notion", exc)

    # Mirror to Notion only when the copy did NOT come from Notion (otherwise we'd duplicate
    # the very rows we just read).
    if (not notion_sourced and settings.notion.enabled and settings.secrets.notion_api_key
            and settings.notion.database_id):
        try:
            docwriter.write_notion_captions(notion_client(settings), settings, units, captions)
        except Exception as exc:  # noqa: BLE001 - Notion logging must never break a build
            log.warning("Notion logging failed (%s) — continuing", exc)
    return entities


def run_all(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    """Batch: build one PAUSED 1-1-N per immediate SUBFOLDER of the creatives folder.

    A parent folder of angle subfolders (e.g. C1…C5) becomes N campaigns, each named by its
    subfolder. Reuses run() unchanged per subfolder (so every guardrail + the caption→Notion
    write applies). Campaigns are created PAUSED when build.activate_after_build is false.
    """
    log = get_logger()
    drive = drive_client(settings)
    parent = settings.drive.creatives_folder_id
    subfolders = [c for c in drive.list_children(parent) if drive.is_folder(c)]
    if not subfolders:
        log.info("build_all: no subfolders under %s — nothing to batch-build.", parent)
        return {"campaigns": 0, "dry_run": dry_run}
    log.info("build_all: %d campaign folder(s) found.", len(subfolders))
    results = []
    base_state_key = settings.meta.build.state_key
    for sf in subfolders:
        s = settings.model_copy(deep=True)
        s.drive.creatives_folder_id = sf["id"]
        # Per-subfolder identity: the campaign is named for the angle ("PREFIX | <angle>") via the
        # build label, and gets a UNIQUE state_key (base + folder id). The unique key is essential —
        # build() reuses any campaign_id it finds in state, so a shared key would make every
        # subfolder after the first pile its ads into the first campaign instead of its own.
        s.meta.build.label = sf["name"]
        s.meta.build.state_key = f"{base_state_key}__{sf['id']}"
        log.info("──────── campaign from '%s' ────────", sf["name"])
        results.append({"folder": sf["name"], "result": run(s, dry_run=dry_run)})
    log.info("build_all: processed %d campaign folder(s)%s.", len(subfolders),
             " (dry-run)" if dry_run else "")
    return {"campaigns": len(subfolders), "results": results, "dry_run": dry_run}
