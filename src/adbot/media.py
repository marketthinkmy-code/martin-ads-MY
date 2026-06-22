"""Upload grouped assets to the Meta ad account (video_id / image_hash), with caching.

This is the step the Meta MCP cannot do. Uploaded ids are cached in state/media_cache
keyed by Drive file id, so re-running ``sync`` never re-uploads the same asset.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import state
from .creative_groups import Unit
from .logging import get_logger


def sync_media(graph, settings, units: List[Unit], *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    cache: Dict[str, Any] = state.load("media_cache")
    uploaded = reused = 0

    for unit in units:
        for asset in unit.assets:
            cached = cache.get(asset.file_id)
            if cached:
                asset.meta_id = cached["meta_id"]
                reused += 1
                continue
            if dry_run:
                log.info("  [WOULD UPLOAD] %s (%s)", asset.name, unit.kind)
                continue
            if asset.mime.startswith("video/"):
                asset.meta_id = graph.upload_video(settings.meta.account_path,
                                                   asset.local_path, name=unit.content_id)
                kind = "video"
            else:
                asset.meta_id = graph.upload_image(settings.meta.account_path, asset.local_path)
                kind = "image"
            cache[asset.file_id] = {"meta_id": asset.meta_id, "kind": kind,
                                    "name": asset.name, "uploaded_at": state.now_iso()}
            uploaded += 1
            state.save("media_cache", cache)  # persist incrementally so a mid-run failure is resumable
            log.info("  [UPLOADED %s] %s -> %s", kind, asset.name, asset.meta_id)

    if not dry_run:
        state.save("media_cache", cache)
    return {"uploaded": uploaded, "reused": reused}
