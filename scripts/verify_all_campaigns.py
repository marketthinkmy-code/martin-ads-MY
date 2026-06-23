"""Read-only verification: every campaign in the account, spend + results + CPL
for a date range, counting ONLY each campaign's exact optimized-event pixel bucket
(= Ads Manager "Results"). No writes. Run: python scripts/verify_all_campaigns.py
"""
from __future__ import annotations

import datetime as dt

from adbot.commands import graph_client  # builds GraphClient from .env token
from adbot.settings import load_settings

KL = dt.timezone(dt.timedelta(hours=8))
SINCE = "2026-06-18"
UNTIL = dt.datetime.now(KL).date().isoformat()  # "till now"


def result_bucket(event: str) -> str:
    return f"offsite_conversion.fb_pixel_{(event or 'complete_registration').lower()}"


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path

    # 1) every campaign (id -> name, status)
    campaigns = g._get_all(
        f"{acct}/campaigns",
        {"fields": "id,name,effective_status", "limit": 500},
    )
    cmap = {c["id"]: c for c in campaigns}

    # 2) per-campaign optimized event (from any ad set's promoted_object)
    adsets = g._get_all(
        f"{acct}/adsets",
        {"fields": "campaign_id,promoted_object", "limit": 500},
    )
    event_by_campaign: dict[str, str] = {}
    for a in adsets:
        po = a.get("promoted_object") or {}
        evt = po.get("custom_event_type")
        if evt and a.get("campaign_id") not in event_by_campaign:
            event_by_campaign[a["campaign_id"]] = evt

    # 3) campaign-level insights for the window
    rows = g._get_all(
        f"{acct}/insights",
        {
            "level": "campaign",
            "time_range": f'{{"since":"{SINCE}","until":"{UNTIL}"}}',
            "fields": "campaign_id,campaign_name,spend,actions",
            "limit": 500,
        },
    )

    out = []
    tot_spend = tot_res = 0.0
    for r in rows:
        cid = r.get("campaign_id")
        spend = float(r.get("spend", 0) or 0)
        evt = event_by_campaign.get(cid, "COMPLETE_REGISTRATION")
        bucket = result_bucket(evt)
        res = 0.0
        for act in r.get("actions") or []:
            if act.get("action_type") == bucket:
                res += float(act.get("value", 0) or 0)
        status = (cmap.get(cid) or {}).get("effective_status", "?")
        out.append((status, r.get("campaign_name", cid), spend, res, evt))
        tot_spend += spend
        tot_res += res

    out.sort(key=lambda x: x[2], reverse=True)
    print(f"Range (Asia/KL): {SINCE} -> {UNTIL}\n")
    print(f"{'STATUS':9} {'CAMPAIGN':40} {'SPEND':>10} {'RES':>5} {'CPL':>9}  EVENT")
    for status, name, spend, res, evt in out:
        cpl = f"RM{spend/res:7.2f}" if res else "    --   "
        print(f"[{status:7}] {name[:40]:40} RM{spend:8.2f} {res:5.0f} {cpl}  {evt}")
    tcpl = tot_spend / tot_res if tot_res else 0
    print(f"\n>>> OVERALL {SINCE} -> {UNTIL}: "
          f"spend RM{tot_spend:.2f} | results {tot_res:.0f} | TOTAL CPL RM{tcpl:.2f}")


if __name__ == "__main__":
    main()
