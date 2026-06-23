"""Weekly resume (Thu 00:00 GMT+8): re-activate exactly the ads weekly_off paused.

Finds ads carrying the weekly-off label, re-activates their campaigns, ad sets, and the
ads themselves, then clears the label. Ads paused by the CPL monitor or by the operator
are NOT labelled, so they correctly stay off.
"""

from __future__ import annotations

from typing import Any, Dict

from . import state
from .logging import final_summary, get_logger
from .settings import Settings


def run(graph, settings: Settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()

    if dry_run:
        # Read-only: find the label (without creating) and report what would resume.
        labels = graph._get_all(f"{settings.meta.account_path}/adlabels",
                                {"fields": "id,name", "limit": 200})
        label = next((l for l in labels if l.get("name") == settings.naming.weekly_off_label), None)
        labeled = graph.list_ad_ids_by_label(label["id"]) if label else []
        for ad in labeled:
            log.info("  [WOULD RESUME] %s", ad.get("name", ad["id"]))
        final_summary(log, f"weekly_on (dry-run): would resume {len(labeled)} ads")
        return {"would_resume": len(labeled), "dry_run": True}

    label_id = graph.get_or_create_label(settings.meta.account_path, settings.naming.weekly_off_label)
    labeled = graph.list_ad_ids_by_label(label_id)
    if not labeled:
        final_summary(log, "weekly_on: no tagged ads to resume")
        return {"resumed": 0, "dry_run": False}

    campaign_ids = {ad["campaign_id"] for ad in labeled if ad.get("campaign_id")}
    adset_ids = {ad["adset_id"] for ad in labeled if ad.get("adset_id")}

    # Activate top-down so the hierarchy can deliver, then the ads, then untag.
    for campaign_id in campaign_ids:
        graph.update_status(campaign_id, "ACTIVE")
    for adset_id in adset_ids:
        graph.update_status(adset_id, "ACTIVE")
    for ad in labeled:
        graph.update_status(ad["id"], "ACTIVE")
        graph.set_ad_labels(ad["id"], [])  # clear the weekly-off tag
        state.append_pause_log(ad["id"], "ad", "weekly_on_resume", {"name": ad.get("name")})
        log.info("  [RESUMED+untagged] %s", ad.get("name", ad["id"]))

    final_summary(log, f"weekly_on: resumed {len(labeled)} ads across {len(campaign_ids)} campaign(s)")
    return {"resumed": len(labeled), "campaigns": len(campaign_ids), "dry_run": False}
