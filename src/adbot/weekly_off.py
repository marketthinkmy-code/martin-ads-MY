"""Weekly kill switch (Wed 15:00 GMT+8): pause every currently-live ad in the account.

Whole-account scope (every campaign in the Martin MY account). Each delivering ad is tagged with the weekly-off
label before being paused, so the Thursday ``weekly_on`` job can resume *exactly* these ads
(and nothing the CPL monitor or the operator paused). The label is the cross-run state — no
committed file needed.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import state
from .logging import final_summary, get_logger
from .settings import Settings


def _collect_active_ads(graph, settings: Settings) -> List[Dict[str, Any]]:
    active: List[Dict[str, Any]] = []
    for campaign in graph.list_campaigns(settings.meta.account_path):
        if campaign.get("effective_status") != "ACTIVE":
            continue
        for ad in graph.list_ads_under_campaign(campaign["id"]):
            if ad.get("effective_status") == "ACTIVE":
                ad["_campaign_id"] = campaign["id"]
                active.append(ad)
    return active


def run(graph, settings: Settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    active = _collect_active_ads(graph, settings)
    log.info("Found %d live ad(s) to pause.", len(active))

    if dry_run:
        for ad in active:
            log.info("  [WOULD PAUSE] %s", ad.get("name", ad["id"]))
        final_summary(log, f"weekly_off (dry-run): would pause {len(active)} live ads")
        return {"would_pause": len(active), "dry_run": True}

    if not active:
        final_summary(log, "weekly_off: nothing live to pause")
        return {"paused": 0, "dry_run": False}

    label_id = graph.get_or_create_label(settings.meta.account_path, settings.naming.weekly_off_label)
    adset_ids, campaign_ids = set(), set()
    for ad in active:
        graph.set_ad_labels(ad["id"], [label_id])
        graph.update_status(ad["id"], "PAUSED")
        if ad.get("adset_id"):
            adset_ids.add(ad["adset_id"])
        campaign_ids.add(ad["_campaign_id"])
        state.append_pause_log(ad["id"], "ad", "weekly_off", {"name": ad.get("name")})
        log.info("  [PAUSED+tagged] %s", ad.get("name", ad["id"]))

    for adset_id in adset_ids:
        graph.update_status(adset_id, "PAUSED")
    for campaign_id in campaign_ids:
        graph.update_status(campaign_id, "PAUSED")

    final_summary(log, f"weekly_off: paused {len(active)} ads, {len(adset_ids)} ad set(s), "
                       f"{len(campaign_ids)} campaign(s); tagged for Thursday resume")
    return {"paused": len(active), "adsets": len(adset_ids), "campaigns": len(campaign_ids),
            "dry_run": False}
