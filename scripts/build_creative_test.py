"""Build a creative-test 1-1-2: N new videos across M new campaigns.

Each campaign is a fresh CBO campaign that CLONES a source ad set's targeting (so the audience
is identical to a proven ad set) and runs every video as its own ad. Uploads each video once
(SU token — the only channel that can push a large video file) and reuses the id across
campaigns. Everything is created PAUSED; the operator activates after review.

Spec JSON (path via ADBOT_AD_SPEC, default scripts/ad_specs/creative_test_1_1_2.json):
  budget_myr   CBO daily budget per campaign (default 100)
  videos[]     {key, drive_file_id, video_name, content_id, ad_name, headline, body|body_file}
  campaigns[]  {name, adset_name, source_adset_id, videos:[key,...]}
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

# Fields safe to copy from a live ad set's targeting onto a new one (drops read-only/derived keys).
_KEEP = ("geo_locations", "age_min", "age_max", "genders", "locales",
         "excluded_custom_audiences", "flexible_spec")


def _clone_targeting(graph, source_adset_id: str) -> dict:
    t = (graph.get_object(source_adset_id, "targeting") or {}).get("targeting", {}) or {}
    out = {k: t[k] for k in _KEEP if k in t}
    # advantage_audience reads back as a dict of age suggestions when ON, or 0 when OFF.
    aa = (t.get("targeting_automation") or {}).get("advantage_audience")
    out["targeting_automation"] = {"advantage_audience": 0 if aa == 0 else 1}
    return out


def _body(v: dict) -> str:
    bf = v.get("body_file")
    if bf:
        p = Path(bf)
        p = p if p.is_absolute() else REPO_ROOT / p
        return p.read_text(encoding="utf-8").strip()
    return v.get("body", "")


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_AD_SPEC", "scripts/ad_specs/creative_test_1_1_2.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    settings = load_settings()
    settings.meta.budget.daily_amount_myr = float(spec.get("budget_myr", 100))
    settings.meta.build.activate_after_build = False
    m = settings.meta
    graph = graph_client(settings)
    drive = drive_client(settings)
    account = m.account_path

    # 1) Upload each video once (cached by Drive file id), keep its unit + thumbnail + caption.
    lib: dict = {}
    for v in spec["videos"]:
        asset = Asset(file_id=v["drive_file_id"], name=v.get("video_name", v["key"]), mime="video/mp4")
        unit = Unit(content_id=v.get("content_id", v["key"]), kind=VIDEO, assets=[asset])
        target = Path(DOWNLOAD_DIR) / f"{asset.file_id}_{v['key']}.mp4"
        drive.download_file(asset.file_id, target)
        asset.local_path = str(target)
        media.sync_media(graph, settings, [unit], dry_run=False)  # sets asset.meta_id
        lib[v["key"]] = {
            "unit": unit,
            "thumb": graph.get_video_thumbnail(asset.meta_id),
            "caption": {"caption": _body(v), "headline": v.get("headline", ""), "name": v.get("ad_name")},
        }
        print(f"[uploaded] {v['key']} -> {asset.meta_id}")

    # 2) Build each campaign: create campaign -> clone-targeted ad set -> one ad per video.
    results = []
    for camp in spec["campaigns"]:
        cid = graph.create_campaign(
            account, name=camp["name"], objective=m.objective, buying_type="AUCTION",
            status="PAUSED", special_ad_categories=m.special_ad_categories,
            daily_budget=m.budget.daily_amount_cents, bid_strategy="LOWEST_COST_WITHOUT_CAP",
        )["id"]
        aid = graph.create_adset(
            account, campaign_id=cid, name=camp["adset_name"],
            optimization_goal=m.optimization_goal, billing_event="IMPRESSIONS",
            promoted_object=m.promoted_object, targeting=_clone_targeting(graph, camp["source_adset_id"]),
            status="PAUSED",
        )["id"]
        ad_ids = []
        for vkey in camp["videos"]:
            item = lib[vkey]
            cspec = build_1_1_10.creative_spec(settings, item["unit"], item["caption"],
                                               thumbnail_url=item["thumb"])
            creative_id = graph.create_adcreative(account, **cspec)["id"]
            ad = graph.create_ad(
                account, name=item["caption"].get("name") or item["unit"].content_id,
                adset_id=aid, creative={"creative_id": creative_id}, status="PAUSED",
                conversion_domain=m.conversion_domain_bare or None,
            )
            ad_ids.append(ad["id"])
            print(f"  ad {ad['id']} ({vkey}, creative {creative_id})")
        results.append({"name": camp["name"], "campaign_id": cid, "adset_id": aid, "ad_ids": ad_ids})
        print(f"[campaign] {camp['name']}\n  campaign={cid} adset={aid} ads={ad_ids}")

    print("DONE " + json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
