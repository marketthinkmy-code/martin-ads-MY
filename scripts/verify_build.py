"""Read-only: print everything that matters about freshly built campaigns, side by side with a
reference campaign, so a build can be checked field by field before it goes live.

ADBOT_VERIFY_IDS = comma-separated campaign ids (the first may be the reference).
"""
from __future__ import annotations

import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    for cid in [c.strip() for c in os.environ["ADBOT_VERIFY_IDS"].split(",") if c.strip()]:
        c = g.get_object(cid, "name,objective,status,effective_status,buying_type,bid_strategy,daily_budget,"
                              "special_ad_categories,special_ad_category_country")
        print(f"\n=== CAMPAIGN {cid} · {c.get('name')}")
        print(f"  objective {c.get('objective')} · status {c.get('status')}/{c.get('effective_status')} · "
              f"buying {c.get('buying_type')} · bid {c.get('bid_strategy')} · campaign budget {c.get('daily_budget')} · "
              f"special {c.get('special_ad_categories')} {c.get('special_ad_category_country')}")
        for a in g._get_all(f"{cid}/adsets", {"fields": "id,name,status,effective_status,daily_budget,start_time,"
                                                         "optimization_goal,promoted_object,targeting,"
                                                         "regional_regulated_categories,regional_regulation_identities",
                                                "limit": 50}):
            t = a.get("targeting") or {}
            inter = sum(len(fs.get(k) or []) for fs in t.get("flexible_spec") or [] for k in ("interests", "behaviors", "family_statuses"))
            print(f"  ADSET {a['id']} {a.get('name')} · {a.get('status')}/{a.get('effective_status')} · RM{int(a.get('daily_budget') or 0) / 100:.0f} · "
                  f"start {a.get('start_time')} · {a.get('optimization_goal')} {(a.get('promoted_object') or {}).get('custom_event_type')}")
            print(f"     geo {(t.get('geo_locations') or {}).get('countries')} · age {t.get('age_min')}-{t.get('age_max')} · locales {t.get('locales')} · "
                  f"interest/behaviour/family items {inter} · advantage_audience {(t.get('targeting_automation') or {}).get('advantage_audience')} · "
                  f"placements {'manual ' + str(t.get('publisher_platforms')) if t.get('publisher_platforms') else 'Advantage+'}")
            print(f"     INCLUDED {[x.get('name') for x in t.get('custom_audiences') or []]}")
            print(f"     EXCLUDED {[x.get('name') for x in t.get('excluded_custom_audiences') or []]}")
            print(f"     regulation {a.get('regional_regulated_categories')} {a.get('regional_regulation_identities')}")
            for ad in g._get_all(f"{a['id']}/ads", {"fields": "id,name,status,effective_status,"
                                                             "creative{id,object_story_id,effective_object_story_id,url_tags}",
                                                    "limit": 50}):
                cr = ad.get("creative") or {}
                print(f"     AD {ad['id']} {ad.get('name')} · {ad.get('status')}/{ad.get('effective_status')} · "
                      f"existing post {cr.get('object_story_id') or '(none — creative-built)'} · "
                      f"effective post {cr.get('effective_object_story_id')} · url_tags {cr.get('url_tags')}")
    print("\nDONE (read-only)")


if __name__ == "__main__":
    main()
