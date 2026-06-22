"""build: create (and optionally activate) the 1-1-10 structure + write the caption log."""

from __future__ import annotations

from typing import Any, Dict

from . import docs_client, drive_client, graph_client, llm_client, notion_client
from .. import build_1_1_10, docwriter, media
from ..captions import generate_for_units
from ..drive_sync import download_assets, load_units
from ..logging import get_logger


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    graph = graph_client(settings)
    drive = drive_client(settings)
    _, units = load_units(drive, settings)
    log.info("Loaded %d creative unit(s) from Drive.", len(units))

    if dry_run:
        # Preview structure without uploading media or calling the LLM (no spend/cost).
        stub = {u.content_id: {"caption": "<generated on live run>",
                              "headline": "<generated>"} for u in units}
        return build_1_1_10.build(graph, settings, units, stub, dry_run=True)

    download_assets(drive, units)
    media.sync_media(graph, settings, units, dry_run=False)

    llm = llm_client(settings)
    captions = generate_for_units(llm, settings, units)

    entities = build_1_1_10.build(graph, settings, units, captions, dry_run=False)

    docs = docs_client(settings)
    docwriter.write_caption_log(docs, settings, units, captions)

    if settings.notion.enabled and settings.secrets.notion_api_key and settings.notion.database_id:
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
    for sf in subfolders:
        s = settings.model_copy(deep=True)
        s.drive.creatives_folder_id = sf["id"]
        s.naming.prefix = f"{settings.naming.prefix} | {sf['name']}"
        log.info("──────── campaign from '%s' ────────", sf["name"])
        results.append({"folder": sf["name"], "result": run(s, dry_run=dry_run)})
    log.info("build_all: processed %d campaign folder(s)%s.", len(subfolders),
             " (dry-run)" if dry_run else "")
    return {"campaigns": len(subfolders), "results": results, "dry_run": dry_run}
