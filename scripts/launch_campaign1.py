"""One-off launcher for Campaign #1 (Video 1-10): upload videos + build the 1-1-10 PAUSED.

Run from the repo root with the venv active. Videos must already be in downloads/.
Captions come verbatim from config/captions_june2026.json (snapshot of the Notion copy).
Auto-activation is force-disabled here — everything is created PAUSED for preview; activate
only after reviewing. Idempotent: cached uploads + guards against a duplicate campaign.
"""

from __future__ import annotations

import json
from pathlib import Path

from adbot import build_1_1_10, media
from adbot.clients.graph import GraphClient
from adbot.creative_groups import VIDEO, Asset, Unit
from adbot.logging import get_logger
from adbot.settings import REPO_ROOT, load_settings

# content_id -> Drive file id (for the media-upload cache key)
DRIVE_IDS = {
    "video_1": "11RqwHJqqzmUo52fEMCvRmBwhT_u3MmAj",
    "video_2": "1JweZZrHCf8So_7dXxcTw2ZktOkaZ_404",
    "video_3": "1IE22yyTyzpl8eHShjqoSDZBh6M8C8Ptw",
    "video_4": "17snXLY-kPMivoM6d4sc4D60QemP_YPKJ",
    "video_5": "1TOnXPSKWvDAvF7FmosNWJTp6xv8reDlK",
    "video_6": "1x3mCw1v_5mGSzj2OOHVtvwa41df1dC6w",
    "video_7": "1KgtxWHZ5GRzpVMCk7dZcAx21gTAOL1aT",
    "video_8": "1lckVuXIFEPRoDJYZELmMfy9dbJSGIfXE",
    "video_9": "1nRCho0kV6Hsf2QdB640ghD2b_Drkv23B",
    "video_10": "1k95BMFRhphKvQoFtKQmZr54kn2_SoaTV",
}
ORDER = [f"video_{i}" for i in range(1, 11)]


def main() -> None:
    log = get_logger()
    settings = load_settings()
    settings.meta.build.activate_after_build = False  # PAUSED build; activate after preview

    manifest = json.loads(
        (Path(REPO_ROOT) / "config" / "captions_june2026.json").read_text(encoding="utf-8"))

    units = []
    for cid in ORDER:
        path = Path(REPO_ROOT) / "downloads" / f"{cid}.mp4"
        if not path.exists():
            raise SystemExit(f"missing video file: {path}")
        units.append(Unit(cid, VIDEO, [Asset(file_id=DRIVE_IDS[cid], name=f"{cid}.mp4",
                                             mime="video/mp4", local_path=str(path))]))

    # Longer timeout for large multipart video uploads.
    graph = GraphClient(settings.secrets.meta_token, settings.secrets.meta_app_secret, timeout=600)

    log.info("Uploading %d videos to Meta (cached if already uploaded)...", len(units))
    stats = media.sync_media(graph, settings, units, dry_run=False)
    log.info("media upload: %s", stats)

    captions = {cid: manifest[cid] for cid in ORDER}
    entities = build_1_1_10.build(graph, settings, units, captions, dry_run=False)
    log.info("ENTITIES: %s", json.dumps(entities, ensure_ascii=False))


if __name__ == "__main__":
    main()
