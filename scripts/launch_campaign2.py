"""Campaign #2: 5 new videos + 2 single images + 1 six-card carousel, mixed in one ad set.

Separate campaign (state key entities_c2), scheduled to begin delivery at START_TIME
(20 Jun 2026 00:00 GMT+8). Built then activated — the future start_time gates actual spend.
Idempotent: cached uploads + resumable build.
"""

from __future__ import annotations

import json
from pathlib import Path

from adbot import build_1_1_10, media
from adbot.clients.graph import GraphClient
from adbot.creative_groups import CAROUSEL, SINGLE_IMAGE, VIDEO, Asset, Unit
from adbot.logging import get_logger
from adbot.settings import REPO_ROOT, load_settings

START_TIME = "2026-06-20T00:00:00+0800"   # 20 Jun 2026, 00:00 GMT+8
LABEL = "1-1-10 #2 (JUN20)"
STATE_KEY = "entities_c2"

VIDEOS = {
    "hook1_1": "11gazT3_TU3RhV1F_hcycgW7u9Ukazdv4",
    "hook2": "1adQcyc96qEbDN24JwvPWZx2Iz35XGY_G",
    "video_11": "1sQTFNK8ZGtiD8Af-X-AhAjF45VoLTaj7",
    "video_12": "1nXfW-haBopu8-qL_3L94JdFza2rQ3WMA",
    "video_13": "1jvunjkYZVZLset_9jKxKZ39lbOn9_Nkp",
}
SINGLE_IMAGES = {
    "si_1": "1a75To8krIM8TPqEqMhq7jthPTkr8kY7X",
    "si_2": "1Hq0EyNNQktkqdpGHSzBDGq-Hotil5wiY",
}
CAROUSEL_CARDS = [  # (drive file id, local filename), in display order 1..6
    ("1RKYU3xhlU20Sb_A2xlCXpoewquWf-Iop", "car_1.png"),
    ("12uyVVpVImK4D-HDZw0cdsgZ6_Hny9X4m", "car_2.png"),
    ("16brTX4vnpwtmWbXHBJEV3N184uX6uZg7", "car_3.png"),
    ("1Ly4m8tYn2ioEYBKEwqdsGWBlSoDkA8Tp", "car_4.png"),
    ("1irWTogs62WDfLkctIe95ZXPFItqfH9vL", "car_5.png"),
    ("1EP7I5UM5CiR37vFZIqRZFZ3YmTBvVqMA", "car_6.png"),
]
ORDER = ["hook1_1", "hook2", "video_11", "video_12", "video_13", "si_1", "si_2", "carousel_1"]


def _dl(name: str) -> str:
    return str(Path(REPO_ROOT) / "downloads" / name)


def main() -> None:
    log = get_logger()
    settings = load_settings()
    settings.meta.build.activate_after_build = False
    manifest = json.loads(
        (Path(REPO_ROOT) / "config" / "captions_campaign2.json").read_text(encoding="utf-8"))

    by_id = {}
    for cid, fid in VIDEOS.items():
        by_id[cid] = Unit(cid, VIDEO, [Asset(file_id=fid, name=f"{cid}.mp4",
                                             mime="video/mp4", local_path=_dl(f"{cid}.mp4"))])
    for cid, fid in SINGLE_IMAGES.items():
        by_id[cid] = Unit(cid, SINGLE_IMAGE, [Asset(file_id=fid, name=f"{cid}.png",
                                                   mime="image/png", local_path=_dl(f"{cid}.png"))])
    by_id["carousel_1"] = Unit("carousel_1", CAROUSEL,
                               [Asset(file_id=fid, name=local, mime="image/png", local_path=_dl(local))
                                for fid, local in CAROUSEL_CARDS])
    units = [by_id[c] for c in ORDER]

    graph = GraphClient(settings.secrets.meta_token, settings.secrets.meta_app_secret, timeout=600)
    log.info("Uploading Campaign #2 media (5 videos + 8 images)...")
    log.info("media: %s", media.sync_media(graph, settings, units, dry_run=False))

    captions = {c: manifest[c] for c in ORDER}
    ents = build_1_1_10.build(graph, settings, units, captions, dry_run=False,
                              label=LABEL, state_key=STATE_KEY, start_time=START_TIME)
    log.info("ENTITIES C2: %s", json.dumps(ents, ensure_ascii=False))

    # Schedule live: activate now; the future start_time gates actual delivery/spend.
    graph.update_status(ents["campaign_id"], "ACTIVE")
    graph.update_status(ents["adset_id"], "ACTIVE")
    for ad in ents["ad_ids"]:
        graph.update_status(ad, "ACTIVE")
    log.info("Campaign #2 ACTIVE — scheduled to start %s", START_TIME)


if __name__ == "__main__":
    main()
