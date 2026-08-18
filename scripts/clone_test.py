"""Build a new campaign + cloned ad set that RE-USES creatives already live in the account.

Same shape as build_creative_test.py, but it never touches Drive or uploads video: the creatives
already exist, so each ad just references an existing creative_id. Two wins over rebuilding —
no 200 MB re-upload per video, and identical ads keep pooling their social proof (likes /
comments / shares) instead of splitting it across a second set of creatives.

Use it to run a proven creative line-up against a second audience. Everything is created PAUSED.

Spec JSON (path via ADBOT_AD_SPEC, default scripts/clone_specs/fnr_v11_v15.json):
  budget_myr       CBO daily budget (default 100)
  campaign_name    name for the new campaign
  adset_name       name for the new ad set
  source_adset_id  ad set whose targeting is cloned   (either this...)
  interests[]      {id, name} real Meta interest ids  (...or this — config targeting + these)
  custom_audience_ids[]  saved audience / lookalike ids (...or this — config targeting + these).
                   Lookalikes carry their own signal, so nothing else is layered on top: adding
                   interests would shrink an audience Meta already picked for resemblance.
  also_exclude[]   extra audience ids to exclude on top of the ones in config (e.g. a freshly
                   rebuilt buyer list, so prospecting does not pay to reach existing customers)
  ads[]            {name, creative_id} or {name, post_id} — one PAUSED ad per entry.
                   post_id ("<page>_<post>") is the EXISTING-POST route: a fresh creative is
                   minted against that page post, so every ad pointing at it accumulates the same
                   likes / comments / shares instead of each ad building proof from zero.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot.commands import graph_client
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


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_AD_SPEC", "scripts/clone_specs/fnr_v11_v15.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        raise SystemExit(f"no spec at {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    settings = load_settings()
    settings.meta.budget.daily_amount_myr = float(spec.get("budget_myr", 100))
    settings.meta.build.activate_after_build = False   # always PAUSED — operator activates
    m = settings.meta
    graph = graph_client(settings)
    account = m.account_path

    if spec.get("custom_audience_ids"):
        # Saved-audience targeting: the account's standard geo/age/locale plus the audiences
        # themselves. A lookalike is already a similarity model, so layering interests on top
        # only narrows what Meta chose — the audience IS the targeting.
        targeting = m.targeting.to_spec()
        targeting["custom_audiences"] = [{"id": str(a)} for a in spec["custom_audience_ids"]]
        print(f"[targeting] {len(spec['custom_audience_ids'])} saved audience(s)")
    elif spec.get("interests"):
        # Brand-new audience: the account's standard targeting (MY / age / Chinese locale /
        # excluded customer lists) plus the interest ids, so the ONLY variable versus a proven
        # ad set is the interest itself. Ids must come from find_interests.py — never invented.
        targeting = m.targeting.to_spec()
        targeting["flexible_spec"] = [{"interests": [
            {"id": str(i["id"]), "name": i.get("name", "")} for i in spec["interests"]]}]
        print(f"[targeting] built from {len(spec['interests'])} interest id(s)")
    else:
        targeting = _clone_targeting(graph, spec["source_adset_id"])
        print(f"[targeting] cloned from {spec['source_adset_id']}")

    for extra in spec.get("also_exclude", []):
        excl = targeting.setdefault("excluded_custom_audiences", [])
        if not any(str(e.get("id")) == str(extra) for e in excl):
            excl.append({"id": str(extra)})
            print(f"[targeting] also excluding {extra}")

    print(f"           {json.dumps(targeting, ensure_ascii=False)[:300]}")

    cid = graph.create_campaign(
        account, name=spec["campaign_name"], objective=m.objective, buying_type="AUCTION",
        status="PAUSED", special_ad_categories=m.special_ad_categories,
        daily_budget=m.budget.daily_amount_cents, bid_strategy="LOWEST_COST_WITHOUT_CAP",
    )["id"]
    aid = graph.create_adset(
        account, campaign_id=cid, name=spec["adset_name"],
        optimization_goal=m.optimization_goal, billing_event="IMPRESSIONS",
        promoted_object=m.promoted_object, targeting=targeting, status="PAUSED",
    )["id"]
    print(f"[campaign] {cid}  [adset] {aid}")

    ad_ids = []
    for item in spec["ads"]:
        if item.get("post_id"):
            # Existing-post ad: mint a creative that points at the page post. url_tags still ride
            # along so UTMs keep resolving per ad name.
            fields = {"object_story_id": str(item["post_id"])}
            if m.url_tags:
                fields["url_tags"] = m.url_tags
            creative_id = graph.create_adcreative(account, **fields)["id"]
            src = f"post {item['post_id']} -> creative {creative_id}"
        else:
            creative_id = str(item["creative_id"])
            src = f"creative {creative_id}"
        ad = graph.create_ad(
            account, name=item["name"], adset_id=aid,
            creative={"creative_id": creative_id}, status="PAUSED",
            conversion_domain=m.conversion_domain_bare or None,
        )
        ad_ids.append(ad["id"])
        print(f"  ad {ad['id']} <- {src} ({item['name']}) — PAUSED")

    print("DONE " + json.dumps(
        {"campaign_id": cid, "adset_id": aid, "ad_ids": ad_ids}, ensure_ascii=False))


if __name__ == "__main__":
    main()
