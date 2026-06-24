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

    # Activate top-down so the hierarchy can deliver, then the ads, then untag. Each activation is
    # ISOLATED: one misconfigured entity (e.g. a campaign whose ad sets' combined minimum spend
    # exceeds its budget — a Meta 400) must never abort the resume of the rest of the account.
    # Failures are logged; the affected ads keep their label so a later weekly_on retries them.
    bad: set = set()

    def _activate(entity_id: str, kind: str) -> bool:
        try:
            graph.update_status(entity_id, "ACTIVE")
            return True
        except Exception as exc:  # noqa: BLE001 - one bad entity must not stop the others
            log.warning("  [skip] could not resume %s %s: %s", kind, entity_id, exc)
            bad.add(entity_id)
            return False

    for campaign_id in campaign_ids:
        _activate(campaign_id, "campaign")
    for adset_id in adset_ids:
        _activate(adset_id, "adset")

    resumed, stuck = 0, 0
    for ad in labeled:
        parent_failed = ad.get("campaign_id") in bad or ad.get("adset_id") in bad
        if parent_failed or not _activate(ad["id"], "ad"):
            stuck += 1            # leave the label ON so the next run retries this ad
            continue
        graph.set_ad_labels(ad["id"], [])  # clear the weekly-off tag only once the ad is live
        state.append_pause_log(ad["id"], "ad", "weekly_on_resume", {"name": ad.get("name")})
        resumed += 1
        log.info("  [RESUMED+untagged] %s", ad.get("name", ad["id"]))

    summary = f"weekly_on: resumed {resumed}/{len(labeled)} ads across {len(campaign_ids)} campaign(s)"
    if stuck:
        summary += f"; {stuck} left tagged (entity failed to activate — see [skip] warnings, fix & re-run)"
    final_summary(log, summary)
    return {"resumed": resumed, "stuck": stuck, "campaigns": len(campaign_ids), "dry_run": False}
