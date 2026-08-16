"""Upload Drive videos and mint ONE ad creative each — printing the page-post id.

Creating an ad creative also creates the (unpublished) page post behind it, and that post id is
what an "existing post" ad needs. So this is step 1 of the existing-post flow: mint the creative,
read back effective_object_story_id, then feed those post ids to clone_test.py, which points every
ad at the SAME post so likes / comments / shares accumulate in one place instead of per-ad.

No campaign, no ad set, no ad, no spend — creatives only.

Spec JSON (path via ADBOT_AD_SPEC, default scripts/ct_specs/new_creatives.json):
  videos[]  {key, drive_file_id, video_name, content_id, ad_name, headline, body|body_file}

Add `video_id` to an entry to reuse a clip ALREADY in the Meta library instead of fetching and
uploading it again. Copy — headline especially — is baked into a creative and cannot be edited
afterwards, so a wording change otherwise means re-uploading gigabytes to change one line. The
video ids are printed by every run of this script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot import build_1_1_10, media
from adbot.commands import drive_client, graph_client
from adbot.creative_groups import VIDEO, Asset, Unit
from adbot.drive_sync import DOWNLOAD_DIR
from adbot.settings import REPO_ROOT, load_settings


def _body(v: dict) -> str:
    bf = v.get("body_file")
    if bf:
        p = Path(bf)
        p = p if p.is_absolute() else REPO_ROOT / p
        return p.read_text(encoding="utf-8").strip()
    return v.get("body", "")


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_AD_SPEC", "scripts/ct_specs/new_creatives.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    settings = load_settings()
    settings.meta.build.activate_after_build = False
    graph = graph_client(settings)
    drive = drive_client(settings)
    account = settings.meta.account_path

    out = []
    for v in spec["videos"]:
        asset = Asset(file_id=v["drive_file_id"], name=v.get("video_name", v["key"]), mime="video/mp4")
        unit = Unit(content_id=v.get("content_id", v["key"]), kind=VIDEO, assets=[asset])
        if v.get("video_id"):
            asset.meta_id = str(v["video_id"])      # already in the library — skip Drive + upload
            print(f"[{v['key']}] reusing video {asset.meta_id}")
        else:
            target = Path(DOWNLOAD_DIR) / f"{asset.file_id}_{v['key']}.mp4"
            drive.download_file(asset.file_id, target)
            asset.local_path = str(target)
            media.sync_media(graph, settings, [unit], dry_run=False)   # sets asset.meta_id
        caption = {"caption": _body(v), "headline": v.get("headline", ""), "name": v.get("ad_name")}
        cspec = build_1_1_10.creative_spec(settings, unit, caption,
                                           thumbnail_url=graph.get_video_thumbnail(asset.meta_id))
        creative_id = graph.create_adcreative(account, **cspec)["id"]
        # The post only exists once the creative does — read it back for the existing-post build.
        post_id = graph.get_object(creative_id, "effective_object_story_id").get(
            "effective_object_story_id", "")
        out.append({"key": v["key"], "ad_name": v.get("ad_name"), "video_id": asset.meta_id,
                    "creative_id": creative_id, "post_id": post_id})
        print(f"[{v['key']}] video={asset.meta_id} creative={creative_id} post={post_id}")

    print("DONE " + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
