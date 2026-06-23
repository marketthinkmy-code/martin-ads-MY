"""sync: download Drive creatives, upload to Meta, group into 10 units."""

from __future__ import annotations

from typing import Any, Dict

from . import drive_client, graph_client
from .. import media
from ..drive_sync import download_assets, load_units
from ..logging import final_summary, get_logger


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    drive = drive_client(settings)
    graph = graph_client(settings)

    _, units = load_units(drive, settings)
    kinds = {}
    for u in units:
        kinds[u.kind] = kinds.get(u.kind, 0) + 1
    log.info("Grouped %d unit(s): %s", len(units), kinds)
    if len(units) < settings.meta.build.creatives_per_adset:
        log.info("NOTE: found %d units but build expects %d — add more assets to the Drive folder.",
                 len(units), settings.meta.build.creatives_per_adset)

    if not dry_run:
        download_assets(drive, units)
    stats = media.sync_media(graph, settings, units, dry_run=dry_run)

    final_summary(log, f"sync: {len(units)} units; uploaded {stats['uploaded']}, "
                       f"reused {stats['reused']} cached asset(s)"
                       + (" (dry-run)" if dry_run else ""))
    return {"units": len(units), **stats, "dry_run": dry_run}
