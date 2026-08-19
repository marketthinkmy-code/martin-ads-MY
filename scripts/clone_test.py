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
  start_time       ISO-8601 WITH offset — the ad set is created scheduled for that moment. Meta
                   refuses to edit start_time once an ad set "has started", and it counts as
                   started from creation even while PAUSED, so this can only be set here.
  adsets[]         {name, budget_myr, custom_audience_ids[]} — build SEVERAL ad sets, each with
                   its own budget (ABO) and its own audience, all running the SAME ads. Use this
                   to compare audiences: the creatives are then the constant and the audience is
                   the only variable. Without it, one ad set is built on the campaign budget (CBO).
  advantage_audience  0 to switch Meta's audience expansion OFF for this build. Required when the
                   ad sets differ only by audience: with it ON, Meta may deliver outside each
                   lookalike band, the bands blur into each other, and the comparison measures
                   nothing.
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

    def build_targeting(audience_ids=None):
        """Targeting for one ad set. audience_ids overrides whatever the spec's top level says."""
        ids = audience_ids if audience_ids is not None else spec.get("custom_audience_ids")
        if ids:
            # Saved-audience targeting: the account's standard geo/age/locale plus the audiences
            # themselves. A lookalike is already a similarity model, so layering interests on top
            # only narrows what Meta chose — the audience IS the targeting.
            tgt = m.targeting.to_spec()
            tgt["custom_audiences"] = [{"id": str(a)} for a in ids]
        elif spec.get("interests"):
            # Brand-new audience: the account's standard targeting (MY / age / Chinese locale /
            # excluded customer lists) plus the interest ids, so the ONLY variable versus a proven
            # ad set is the interest itself. Ids must come from find_interests.py — never invented.
            tgt = m.targeting.to_spec()
            tgt["flexible_spec"] = [{"interests": [
                {"id": str(i["id"]), "name": i.get("name", "")} for i in spec["interests"]]}]
        else:
            tgt = _clone_targeting(graph, spec["source_adset_id"])

        if "advantage_audience" in spec:
            aa = int(spec["advantage_audience"])
            tgt["targeting_automation"] = {"advantage_audience": aa}
            if aa == 0:
                # Expansion off means Meta must stay inside the audience — the whole point when
                # ad sets differ only by which band they target.
                tgt["targeting_relaxation_types"] = {"lookalike": 0, "custom_audience": 0}

        for extra in spec.get("also_exclude", []):
            excl = tgt.setdefault("excluded_custom_audiences", [])
            if not any(str(e.get("id")) == str(extra) for e in excl):
                excl.append({"id": str(extra)})
        return tgt

    # Ad-set budgets (ABO) when several ad sets are being compared, campaign budget (CBO) when
    # there is only one — a CBO campaign would hand its budget to whichever ad set looks best
    # early, which is exactly the comparison this is trying to make.
    plans = spec.get("adsets") or [{"name": spec["adset_name"],
                                    "budget_myr": spec.get("budget_myr", 100),
                                    "custom_audience_ids": spec.get("custom_audience_ids")}]
    abo = len(plans) > 1

    campaign_fields = dict(
        name=spec["campaign_name"], objective=m.objective, buying_type="AUCTION",
        status="PAUSED", special_ad_categories=m.special_ad_categories,
    )
    if abo:
        # Meta demands this be stated outright once the budget lives on the ad sets. "true" would
        # let them lend each other 20% of their budget — which is precisely what a band comparison
        # must not allow, or a band's spend is no longer its own.
        campaign_fields["is_adset_budget_sharing_enabled"] = "false"
    else:
        campaign_fields.update(daily_budget=m.budget.daily_amount_cents,
                               bid_strategy="LOWEST_COST_WITHOUT_CAP")
    cid = graph.create_campaign(account, **campaign_fields)["id"]
    print(f"[campaign] {cid}  ({'ABO - budget per ad set' if abo else 'CBO'})")

    adset_ids = []
    for plan in plans:
        targeting = build_targeting(plan.get("custom_audience_ids"))
        fields = dict(
            campaign_id=cid, name=plan["name"],
            optimization_goal=m.optimization_goal, billing_event="IMPRESSIONS",
            promoted_object=m.promoted_object, targeting=targeting, status="PAUSED",
        )
        if abo:
            fields["daily_budget"] = int(round(float(plan.get("budget_myr", 100)) * 100))
            fields["bid_strategy"] = "LOWEST_COST_WITHOUT_CAP"
        if spec.get("start_time"):
            fields["start_time"] = spec["start_time"]
        aid = graph.create_adset(account, **fields)["id"]
        adset_ids.append(aid)
        print(f"  [adset] {aid}  {plan['name']}"
              + (f"  RM{float(plan.get('budget_myr', 100)):,.0f}/day" if abo else "")
              + (f"  starts {spec['start_time']}" if spec.get("start_time") else ""))
        print(f"          {json.dumps(targeting, ensure_ascii=False)[:240]}")

    # Mint each creative once, then place it in every ad set: reusing one creative_id keeps the
    # ads twins of each other, so the only difference between ad sets stays the audience.
    resolved = []
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
        resolved.append((item["name"], creative_id, src))

    ad_ids = []
    for aid in adset_ids:
        for name, creative_id, src in resolved:
            ad = graph.create_ad(
                account, name=name, adset_id=aid,
                creative={"creative_id": creative_id}, status="PAUSED",
                conversion_domain=m.conversion_domain_bare or None,
            )
            ad_ids.append(ad["id"])
            print(f"  ad {ad['id']} in {aid} <- {src} ({name}) - PAUSED")

    print("DONE " + json.dumps(
        {"campaign_id": cid, "adset_ids": adset_ids, "ad_ids": ad_ids}, ensure_ascii=False))


if __name__ == "__main__":
    main()
