"""build: create (and optionally activate) the 1-1-10 structure + write the caption log."""

from __future__ import annotations

from typing import Any, Dict

from . import docs_client, drive_client, graph_client, llm_client
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
    return entities
