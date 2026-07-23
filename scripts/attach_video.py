"""Attach ONE new Drive video as a PAUSED ad to one or more EXISTING ad sets.

Uploads the video once (SU token — the only channel that can push a large video file), builds
ONE ad creative, then creates a PAUSED ad in every target ad set that REUSES that same creative.
Sharing one creative means identical ads pool their social proof (likes / comments / shares)
instead of splitting it across ad sets. Nothing is activated — the operator reviews, then
activates. Kept in its own script + workflow + spec dir so a push never re-fires the
creative-test or upload-ad builds.

Spec JSON (path via ADBOT_AD_SPEC, default scripts/attach_specs/hook4.json):
  drive_file_id  Google Drive file id of the video (SA must have read access)
  video_name     human name for the uploaded Meta video (optional)
  content_id     internal id / caption key (optional; defaults to a slug)
  ad_name        ad name -> utm_content={{ad.name}} (optional)
  headline       creative title
  body           primary text  (or body_file: path to a .txt holding it verbatim)
  adset_ids      list of EXISTING ad set ids to attach the (shared) ad to
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


def _load_spec() -> dict:
    spec_path = Path(os.environ.get("ADBOT_AD_SPEC", "scripts/attach_specs/hook4.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        raise SystemExit(f"no spec at {spec_path} — add the spec (drive_file_id, body/body_file, "
                         "headline, adset_ids) before running")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    body_file = spec.get("body_file")
    if body_file:
        bf = Path(body_file)
        if not bf.is_absolute():
            bf = REPO_ROOT / bf
        spec["body"] = bf.read_text(encoding="utf-8").strip()
    if not spec.get("body"):
        raise SystemExit("spec has neither 'body' nor a readable 'body_file'")
    adset_ids = [str(a).strip() for a in (spec.get("adset_ids") or []) if str(a).strip()]
    if not adset_ids:
        raise SystemExit("spec needs a non-empty 'adset_ids' list")
    spec["adset_ids"] = adset_ids
    return spec


def main() -> None:
    spec = _load_spec()
    settings = load_settings()
    settings.meta.build.activate_after_build = False  # always PAUSED — operator activates

    graph = graph_client(settings)
    drive = drive_client(settings)
    account = settings.meta.account_path

    content_id = spec.get("content_id") or "adhoc_video"
    asset = Asset(file_id=spec["drive_file_id"],
                  name=spec.get("video_name", content_id), mime="video/mp4")
    unit = Unit(content_id=content_id, kind=VIDEO, assets=[asset])

    # Download from Drive (service account) then upload to Meta. sync_media caches the video_id by
    # Drive file id, so a re-run reuses the upload instead of pushing the file again.
    target = Path(DOWNLOAD_DIR) / f"{asset.file_id}_{content_id}.mp4"
    drive.download_file(asset.file_id, target)
    asset.local_path = str(target)
    print(f"[downloaded] {asset.name} -> {target}")
    media.sync_media(graph, settings, [unit], dry_run=False)  # sets asset.meta_id = video_id
    print(f"[uploaded] video -> {asset.meta_id}")

    caption = {"caption": spec["body"], "headline": spec.get("headline", ""),
               "name": spec.get("ad_name")}
    thumb = graph.get_video_thumbnail(asset.meta_id)
    cspec = build_1_1_10.creative_spec(settings, unit, caption, thumbnail_url=thumb)
    creative_id = graph.create_adcreative(account, **cspec)["id"]  # build once, share across ad sets
    print(f"[creative] {creative_id}")

    ads = []
    for adset_id in spec["adset_ids"]:
        ad = graph.create_ad(
            account, name=caption.get("name") or content_id,
            adset_id=adset_id, creative={"creative_id": creative_id}, status="PAUSED",
            conversion_domain=settings.meta.conversion_domain_bare or None,
        )
        ads.append({"adset_id": adset_id, "ad_id": ad["id"]})
        print(f"  attached ad {ad['id']} -> adset {adset_id} — PAUSED")

    print("DONE " + json.dumps(
        {"video_id": asset.meta_id, "creative_id": creative_id, "ads": ads}, ensure_ascii=False))


if __name__ == "__main__":
    main()
