"""Build the 1-1-10 structure: 1 CBO campaign, 1 broad ad set (geo + age from config), 10 ads.

Everything is created PAUSED. If ``meta.build.activate_after_build`` is true, the whole
hierarchy is then activated (campaign -> ad set -> ads) at the configured CBO budget.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import state
from .creative_groups import CAROUSEL, SINGLE_IMAGE, VIDEO, Unit
from .logging import final_summary, get_logger
from .settings import Settings


def _cta(settings: Settings) -> Dict[str, Any]:
    return {"type": settings.meta.call_to_action,
            "value": {"link": settings.meta.lead_destination.link_url}}


def creative_spec(settings: Settings, unit: Unit, caption: Dict[str, Any],
                  thumbnail_url: Optional[str] = None) -> Dict[str, Any]:
    """Build the ad-creative payload for one unit (video / image / carousel)."""
    page_id = settings.meta.page_id
    link = settings.meta.lead_destination.link_url
    message = caption.get("caption", "")
    headline = caption.get("headline", "")
    cta = _cta(settings)

    if unit.kind == VIDEO:
        video_data = {"video_id": unit.assets[0].meta_id, "title": headline,
                      "message": message, "call_to_action": cta}
        if thumbnail_url:  # Meta requires a thumbnail (image_url/image_hash) for video creatives
            video_data["image_url"] = thumbnail_url
        story = {"page_id": page_id, "video_data": video_data}
    elif unit.kind == SINGLE_IMAGE:
        story = {"page_id": page_id, "link_data": {
            "link": link, "image_hash": unit.assets[0].meta_id,
            "message": message, "name": headline, "call_to_action": cta}}
    elif unit.kind == CAROUSEL:
        cards = caption.get("carousel_card_texts") or []
        child = []
        for i, asset in enumerate(unit.assets):
            card = cards[i] if i < len(cards) else {}
            child.append({"link": link, "image_hash": asset.meta_id,
                          "name": card.get("name", headline),
                          "description": card.get("description", ""), "call_to_action": cta})
        story = {"page_id": page_id, "link_data": {
            "link": link, "message": message, "child_attachments": child,
            "multi_share_optimized": True, "multi_share_end_card": True}}
    else:  # pragma: no cover - guarded by grouping
        raise ValueError(f"unknown unit kind: {unit.kind}")

    spec = {"name": f"{settings.naming.prefix} | {unit.content_id}", "object_story_spec": story}
    if settings.meta.instagram_user_id:
        spec["instagram_user_id"] = settings.meta.instagram_user_id
    if settings.meta.url_tags:  # UTM tracking params appended to clicked URLs
        spec["url_tags"] = settings.meta.url_tags
    return spec


def build(graph, settings: Settings, units: List[Unit],
          captions: Dict[str, Dict[str, Any]], *, dry_run: bool = False,
          label: str = "1-1-10", state_key: str = "entities",
          start_time: Optional[str] = None) -> Dict[str, Any]:
    log = get_logger()
    account = settings.meta.account_path
    m = settings.meta

    campaign_fields = {
        "name": settings.naming.campaign_name(label),
        "objective": m.objective, "buying_type": "AUCTION", "status": "PAUSED",
        "special_ad_categories": m.special_ad_categories,
        "daily_budget": m.budget.daily_amount_cents,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    }
    adset_fields = {
        "name": settings.naming.campaign_name(f"{label} | AdSet (Broad {(m.targeting.countries or ['MY'])[0]} {m.targeting.age_min}+)"),
        "optimization_goal": m.optimization_goal, "billing_event": "IMPRESSIONS",
        "promoted_object": m.promoted_object, "targeting": m.targeting.to_spec(),
        "status": "PAUSED",
    }
    if start_time:  # schedule delivery to begin at this ISO8601 time (with tz offset)
        adset_fields["start_time"] = start_time

    if dry_run:
        log.info("[dry-run] CAMPAIGN: %s", campaign_fields)
        log.info("[dry-run] AD SET:   %s", adset_fields)
        for unit in units:
            log.info("[dry-run] CREATIVE %s (%s): %s", unit.content_id, unit.kind,
                     creative_spec(settings, unit, captions.get(unit.content_id, {})))
        final_summary(log, f"build (dry-run): would create 1 campaign, 1 ad set, {len(units)} ads "
                           f"at {m.budget.daily_amount_myr:.0f} MYR/day CBO (nothing created)")
        return {"dry_run": True, "ads": len(units)}

    # Resumable: reuse any campaign / ad set / ads already recorded in state, and persist
    # after every entity so a mid-build failure never strands work or duplicates a campaign.
    existing = state.load(state_key)
    campaign_id = existing.get("campaign_id")
    adset_id = existing.get("adset_id")
    ad_ids: List[str] = list(existing.get("ad_ids", []))
    built = set(existing.get("built_content_ids", []))
    created_at = existing.get("created_at") or state.now_iso()

    def _persist() -> None:
        state.save(state_key, {"campaign_id": campaign_id, "adset_id": adset_id,
                                "ad_ids": ad_ids, "built_content_ids": sorted(built),
                                "created_at": created_at})

    if campaign_id:
        log.info("Reusing campaign %s", campaign_id)
    else:
        campaign_id = graph.create_campaign(account, **campaign_fields)["id"]
        log.info("Created campaign %s", campaign_id)
        _persist()

    if adset_id:
        log.info("Reusing ad set %s", adset_id)
    else:
        adset_id = graph.create_adset(account, campaign_id=campaign_id, **adset_fields)["id"]
        log.info("Created ad set %s", adset_id)
        _persist()

    for unit in units:
        if unit.content_id in built:
            log.info("  Skipping %s (already built)", unit.content_id)
            continue
        thumb = graph.get_video_thumbnail(unit.assets[0].meta_id) if unit.kind == VIDEO else None
        spec = creative_spec(settings, unit, captions.get(unit.content_id, {}), thumbnail_url=thumb)
        creative_id = graph.create_adcreative(account, **spec)["id"]
        ad_name = captions.get(unit.content_id, {}).get("name") or f"{settings.naming.prefix} | {unit.content_id}"
        ad = graph.create_ad(
            account, name=ad_name,
            adset_id=adset_id, creative={"creative_id": creative_id}, status="PAUSED",
            conversion_domain=m.conversion_domain_bare or None,
        )
        ad_ids.append(ad["id"])
        built.add(unit.content_id)
        _persist()
        log.info("  Created ad %s (%s, creative %s)", ad["id"], unit.kind, creative_id)

    activated = False
    if m.build.activate_after_build:
        graph.update_status(campaign_id, "ACTIVE")
        graph.update_status(adset_id, "ACTIVE")
        for ad_id in ad_ids:
            graph.update_status(ad_id, "ACTIVE")
        activated = True

    state_word = "ACTIVE" if activated else "PAUSED"
    final_summary(log, f"build: 1 campaign + 1 ad set + {len(ad_ids)} ads "
                       f"at {m.budget.daily_amount_myr:.0f} MYR/day CBO — now {state_word}")
    return {"campaign_id": campaign_id, "adset_id": adset_id, "ad_ids": ad_ids, "activated": activated}
