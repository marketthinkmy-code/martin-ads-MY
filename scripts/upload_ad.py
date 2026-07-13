"""One-shot: upload a NEW video ad from Google Drive via the adbot system-user token.

Reads an ad spec (JSON) describing one video creative + the operator-written copy, downloads
the video through the service-account Drive client, uploads it to Meta (resumable chunked upload,
so large files are fine), then builds the ad. With no ``adset_id`` it creates a fresh 1-1-1 CBO
campaign by reusing ``build_1_1_10`` — so every naming / url_tags / targeting / promoted-object
convention matches a normal build. With an ``adset_id`` it just attaches one ad to that existing
ad set. Everything is created PAUSED; the operator activates after review.

This is the SU-token path (GitHub Actions), independent of the interactive Meta connector — the
only channel that can actually upload a new video file to the account.

Spec JSON (path via ADBOT_AD_SPEC, default scripts/ad_specs/july_v1.json):
  drive_file_id  Google Drive file id of the video (SA must have read access)
  video_name     human name for the uploaded Meta video (optional)
  content_id     internal id / caption key (optional; defaults to a slug)
  ad_name        ad name -> utm_content={{ad.name}} (optional)
  headline       creative title
  body           primary text  (or body_file: path to a .txt holding it verbatim)
  label          campaign-name suffix -> "PREFIX | <label>"  (new-campaign path)
  budget_myr     CBO daily budget for the new campaign (default 100)
  adset_id       ""=create a new campaign; else attach one ad to this ad set
  state_key      state file for the new-campaign build (keep unique per campaign)
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
    spec_path = Path(os.environ.get("ADBOT_AD_SPEC", "scripts/ad_specs/july_v1.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    body_file = spec.get("body_file")
    if body_file:
        bf = Path(body_file)
        if not bf.is_absolute():
            bf = REPO_ROOT / bf
        spec["body"] = bf.read_text(encoding="utf-8").strip()
    if not spec.get("body"):
        raise SystemExit("spec has neither 'body' nor a readable 'body_file'")
    return spec


def main() -> None:
    spec = _load_spec()
    settings = load_settings()
    # RM100/day CBO for this test by default; never auto-activate — the operator reviews first.
    settings.meta.budget.daily_amount_myr = float(spec.get("budget_myr", 100))
    settings.meta.build.activate_after_build = False

    graph = graph_client(settings)
    drive = drive_client(settings)

    content_id = spec.get("content_id") or "adhoc_video"
    asset = Asset(file_id=spec["drive_file_id"],
                  name=spec.get("video_name", content_id), mime="video/mp4")
    unit = Unit(content_id=content_id, kind=VIDEO, assets=[asset])

    # Download from Drive (service account) then upload to Meta. sync_media caches the video_id by
    # Drive file id, so a re-run reuses the upload instead of pushing 200 MB again.
    target = Path(DOWNLOAD_DIR) / f"{asset.file_id}_{content_id}.mp4"
    drive.download_file(asset.file_id, target)
    asset.local_path = str(target)
    print(f"[downloaded] {asset.name} -> {target}")
    media.sync_media(graph, settings, [unit], dry_run=False)  # sets asset.meta_id = video_id
    print(f"[uploaded] video -> {asset.meta_id}")

    caption = {"caption": spec["body"], "headline": spec.get("headline", ""),
               "name": spec.get("ad_name")}
    captions = {content_id: caption}

    adset_id = str(spec.get("adset_id") or "").strip()
    if adset_id:
        # Attach a single ad to an existing ad set (options "into 冠军6强" / "other ad set").
        thumb = graph.get_video_thumbnail(asset.meta_id)
        cspec = build_1_1_10.creative_spec(settings, unit, caption, thumbnail_url=thumb)
        creative_id = graph.create_adcreative(settings.meta.account_path, **cspec)["id"]
        ad = graph.create_ad(
            settings.meta.account_path, name=caption.get("name") or content_id,
            adset_id=adset_id, creative={"creative_id": creative_id}, status="PAUSED",
            conversion_domain=settings.meta.conversion_domain_bare or None,
        )
        print(f"[done] ad {ad['id']} (creative {creative_id}) -> ad set {adset_id} — PAUSED")
        return

    # New 1-1-1 CBO campaign (default) — reuse the tested builder; everything PAUSED.
    result = build_1_1_10.build(
        graph, settings, [unit], captions, dry_run=False,
        label=spec.get("label", "Ad-hoc Test"),
        state_key=spec.get("state_key", "adhoc_test"),
    )
    print(f"[done] {result}")


if __name__ == "__main__":
    main()
