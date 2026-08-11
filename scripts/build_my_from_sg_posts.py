"""Mirror the SG creative batch into the MY account by REUSING the same page posts.

MY and SG run on the SAME Facebook page (341825319024143 in both configs), so the unpublished
page posts behind the SG ads can be pointed at from MY with creative {"object_story_id": ...}.
Nothing is re-uploaded: no 52 assets, no 24MB video, and — because both markets share one post —
likes, comments and shares pool together instead of splitting across two copies of the creative.

The flip side of sharing a post is that the COMMENTS are shared too: an MY parent's question
shows up under the SG ad and vice versa. Both markets bill in MYR and share the landing page, so
that is survivable here; it would not be if the offers diverged.

What deliberately does NOT come across from SG:
  · regional_regulated_categories / regional_regulation_identities — Singapore-only declarations;
    MY's config carries neither, so they are simply absent here.
  · SG's excluded custom audiences — this uses MY's own (from MY config).
  · SG's ad-set targeting — cloned from the MY ad set of the same name where one exists, else
    MY's broad config spec. Logged per group so it is obvious which happened.

Everything is created PAUSED. Budget mirrors what SG is actually running (RM100/day/campaign),
so activating all six would be RM600/day — activate selectively.

Idempotent: each group's ids live in its own state file and each ad is tracked by post id, so a
re-dispatch reuses rather than duplicates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from adbot.clients.graph import GraphError
from adbot.commands import graph_client
from adbot.logging import final_summary, get_logger
from adbot.settings import load_settings

PREFIX = "[MY] 儿童长高方程式"
DAILY_MYR = 100                      # mirrors what SG is live at (operator raised it from 60)
STATE_DIR = Path("state")

DETAIL_KEYS = ["interests", "behaviors", "life_events", "family_statuses", "industries",
               "income", "education_statuses", "work_positions", "work_employers",
               "relationship_statuses", "user_adclusters", "moms"]

# Exported from the SG account 2026-08-11 (scripts/export_batch_post_ids.py, run 31496668850).
# "<page_id>_<post_id>" — the page is shared with MY, which is what makes reuse possible.
GROUPS: List[Dict[str, Any]] = [
    {"key": "a_knowledge", "label": "知识科普 | A", "clone": "Parents",
     "adset_name": "Parents 3-17 | 知识科普", "state_key": "entities_mybatch_a_knowledge",
     "ads": [
         ("Carousel：孩子晚上喊脚痛？可能是生长痛",        "341825319024143_122193882572485585"),
         ("Carousel：晚上 9 点到凌晨 3 点，才是长高黄金时段", "341825319024143_122193882488485585"),
         ("Video：你的孩子身高，达标了吗？",               "341825319024143_122193882494485585"),
         ("Single Image：你的孩子身高，达标了吗？",         "341825319024143_122193882644485585"),
     ]},
    {"key": "b_myths", "label": "迷思打破 | B", "clone": "Milk & Bread",
     "adset_name": "Milk & Bread | 迷思打破", "state_key": "entities_mybatch_b_myths",
     "ads": [
         ("Carousel：别再逼孩子喝牛奶了",                 "341825319024143_122193887096485585"),
         ("Single Image：这 5 种运动，越做越矮",           "341825319024143_122193887006485585"),
         ("Single Image：初经、变声，不是青春期的开始",      "341825319024143_122193886994485585"),
     ]},
    {"key": "c_proof", "label": "见证证明 | C", "clone": "Health & Wellness",
     "adset_name": "Health & Wellness | 见证证明", "state_key": "entities_mybatch_c_proof",
     "ads": [
         ("Carousel：7 个月，哥哥长了 12cm",              "341825319024143_122193899336485585"),
         ("Carousel：免费线上分享会 · 儿童长高方程式",       "341825319024143_122193899264485585"),
         ("Single Image：看过专科西医和营养师，她还是改了做法", "341825319024143_122193899354485585"),
     ]},
    {"key": "d_emotion", "label": "焦虑情感 | D", "clone": "Family and Relationships",
     "adset_name": "Family and Relationships | 焦虑情感", "state_key": "entities_mybatch_d_emotion",
     "ads": [
         ("Single Image：孩子身高停止不长了？",             "341825319024143_122193871928485585"),
         ("Single Image：3～15 岁，孩子一生只有一次的长高黄金期", "341825319024143_122193871946485585"),
         ("Single Image：就算爸妈都不高，孩子一样能长高",     "341825319024143_122193871964485585"),
     ]},
    {"key": "e_lowkey", "label": "低期待值 | E", "clone": "Parents",
     "adset_name": "Parents 3-17 | 低期待值", "state_key": "entities_mybatch_e_lowkey",
     "ads": [
         ("Carousel：停了两年，两个月又开始长了",            "341825319024143_122193872072485585"),
         ("Single Image：一开始我也不信，结果两个月长了 3–4cm", "341825319024143_122193872096485585"),
         ("Single Image：「也许只是 2cm，但对我们来说是希望的开始」", "341825319024143_122193872132485585"),
     ]},
    {"key": "f_backup", "label": "备用 | F", "clone": "Health & Wellness",
     "adset_name": "Health & Wellness | 备用", "state_key": "entities_mybatch_f_backup",
     "ads": [
         ("Single Image：医生说他不会再长了，结果呢？",       "341825319024143_122193882368485585"),
         ("Single Image：不吃保健品，孩子反而长高了",        "341825319024143_122193872270485585"),
     ]},
]


def _richness(t: dict) -> int:
    if not isinstance(t, dict):
        return 0
    cnt = lambda spec: sum(len(spec.get(k) or []) for k in DETAIL_KEYS)
    return cnt(t) + sum(cnt(grp) for grp in (t.get("flexible_spec") or []))


def clone_targeting(adsets: List[dict], name: str, s) -> Tuple[Optional[dict], Optional[str]]:
    """Clone interest targeting from the richest MY ad set matching `name`, shaped for MY."""
    key = name.strip().lower()
    matches = [a for a in adsets if (a.get("name") or "").strip().lower() == key] \
        or [a for a in adsets if key in (a.get("name") or "").strip().lower()]
    if not matches:
        return None, None
    best = max(matches, key=lambda a: _richness(a.get("targeting") or {}))
    t = best.get("targeting") or {}
    adv_raw = (t.get("targeting_automation") or {}).get("advantage_audience")
    adv = 1 if adv_raw is None else int(adv_raw)
    age_min, age_max = int(t.get("age_min") or 25), int(t.get("age_max") or 65)
    if adv == 1 and age_min > 25:
        age_min = 25
    spec: Dict[str, Any] = {
        "geo_locations": {"countries": s.meta.targeting.countries or ["MY"]},
        "age_min": age_min, "age_max": age_max,
        "targeting_automation": {"advantage_audience": adv},
        "excluded_custom_audiences": [{"id": i} for i in
                                      (s.meta.targeting.excluded_custom_audiences or [])],
        "locales": s.meta.targeting.locales or [1004],
    }
    if t.get("genders"):
        spec["genders"] = t["genders"]
    fs = t.get("flexible_spec")
    if fs:
        spec["flexible_spec"] = fs
    else:
        legacy = {k: t[k] for k in DETAIL_KEYS if t.get(k)}
        if legacy:
            spec["flexible_spec"] = [legacy]
    return spec, best.get("name")


def main() -> None:
    log = get_logger()
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    m = s.meta
    conv = m.conversion_domain_bare or None

    s.naming.prefix = PREFIX
    m.budget.daily_amount_myr = DAILY_MYR

    my_adsets = g._get_all(f"{acct}/adsets", {"fields": "name,effective_status,targeting", "limit": 500})
    log.info("pulled %d MY ad sets for cloning", len(my_adsets))

    summary: List[str] = []
    total_ads = 0

    for grp in GROUPS:
        log.info("═" * 92)
        log.info("── %s  (%d ads, reusing SG page posts)", grp["label"], len(grp["ads"]))

        st_path = STATE_DIR / f"{grp['state_key']}.json"
        st = json.loads(st_path.read_text()) if st_path.exists() else {}
        campaign_id, adset_id = st.get("campaign_id"), st.get("adset_id")
        built = set(st.get("built_post_ids", []))
        ad_ids: List[str] = list(st.get("ad_ids", []))

        def persist() -> None:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            st_path.write_text(json.dumps(
                {"campaign_id": campaign_id, "adset_id": adset_id, "ad_ids": ad_ids,
                 "built_post_ids": sorted(built)}, ensure_ascii=False, indent=2))

        try:
            if campaign_id:
                log.info("   reusing campaign %s", campaign_id)
            else:
                campaign_id = g.create_campaign(acct, **{
                    "name": s.naming.campaign_name(grp["label"]),
                    "objective": m.objective, "buying_type": "AUCTION", "status": "PAUSED",
                    "special_ad_categories": m.special_ad_categories,
                    "daily_budget": m.budget.daily_amount_cents,
                    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                })["id"]
                log.info("   + campaign %s", campaign_id)
                persist()

            if adset_id:
                log.info("   reusing ad set %s", adset_id)
            else:
                spec, src = clone_targeting(my_adsets, grp["clone"], s)
                if spec is None:
                    spec = m.targeting.to_spec()
                    log.warning("   !! no MY ad set like %r — falling back to broad config targeting",
                                grp["clone"])
                else:
                    log.info("   targeting ← %r (age %s-%s · adv=%s · %d detail entries)", src,
                             spec["age_min"], spec["age_max"],
                             (spec.get("targeting_automation") or {}).get("advantage_audience"),
                             _richness({"flexible_spec": spec.get("flexible_spec", [])}))
                adset_id = g.create_adset(acct, **{
                    "name": grp["adset_name"], "campaign_id": campaign_id,
                    "optimization_goal": m.optimization_goal, "billing_event": "IMPRESSIONS",
                    "promoted_object": m.promoted_object, "targeting": spec, "status": "PAUSED",
                })["id"]
                log.info("   + ad set %s", adset_id)
                persist()

            for ad_name, post_id in grp["ads"]:
                if post_id in built:
                    log.info("   · already built %s", post_id)
                    continue
                creative_fields: Dict[str, Any] = {
                    "name": f"{PREFIX} | {ad_name}", "object_story_id": post_id}
                if m.instagram_user_id:
                    creative_fields["instagram_user_id"] = m.instagram_user_id
                if m.url_tags:
                    creative_fields["url_tags"] = m.url_tags
                creative = g.create_adcreative(acct, **creative_fields)
                ad = g.create_ad(acct, name=ad_name, adset_id=adset_id,
                                 creative={"creative_id": creative["id"]},
                                 status="PAUSED", conversion_domain=conv)
                ad_ids.append(ad["id"])
                built.add(post_id)
                persist()
                total_ads += 1
                log.info("   + ad %s ⟵ post %s  (%s)", ad["id"], post_id, ad_name[:38])

            summary.append(f"  {grp['label']:16} campaign={campaign_id} adset={adset_id} ads={len(ad_ids)}")
        except GraphError as exc:
            log.error("   !! %s failed: %s", grp["label"], exc)
            summary.append(f"  ✗ FAIL {grp['label']}: {exc}")

    log.info("═" * 92)
    for line in summary:
        log.info(line)
    final_summary(
        log, f"MY mirror built PAUSED: {total_ads} new ad(s) across {len(GROUPS)} CBO campaigns "
             f"(RM{DAILY_MYR}/day each — all six live would be RM{DAILY_MYR*len(GROUPS)}/day). Every ad "
             f"points at the SAME page post as its SG twin, so engagement pools across both markets "
             f"and no asset was re-uploaded. Review previews in Ads Manager, then activate "
             f"selectively — MY already has its own campaigns competing for the same audiences.")


if __name__ == "__main__":
    main()
