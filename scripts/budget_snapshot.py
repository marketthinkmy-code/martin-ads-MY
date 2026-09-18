"""Read-only: what the account is set to spend per day right now, and what it actually spent.

Planned = the sum of every ACTIVE ad set's daily budget (ABO) plus every ACTIVE CBO campaign's
daily budget (its ad sets carry none). Actual = account spend per day for the last 7 days and
today so far. No writes.
"""
from __future__ import annotations

import datetime as dt
import json

from adbot.commands import graph_client
from adbot.settings import load_settings


def _myr(cents) -> float:
    try:
        return int(cents) / 100.0
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    now_myt = dt.datetime.utcnow() + dt.timedelta(hours=8)
    print(f"===== DAILY BUDGET SNAPSHOT · {now_myt:%Y-%m-%d %H:%M} MYT · {acct} =====")

    campaigns = g._get_all(f"{acct}/campaigns", {
        "fields": "id,name,effective_status,daily_budget,lifetime_budget",
        "effective_status": json.dumps(["ACTIVE"]), "limit": 200})
    adsets = g._get_all(f"{acct}/adsets", {
        "fields": "id,name,effective_status,daily_budget,lifetime_budget,campaign{id,name}",
        "effective_status": json.dumps(["ACTIVE"]), "limit": 200})

    # An ACTIVE ad set whose ads are all paused spends nothing — count it separately so the
    # planned total is what the account can actually spend today.
    live_adsets = set()
    for ad in g._get_all(f"{acct}/ads", {"fields": "id,adset_id",
                                          "effective_status": json.dumps(["ACTIVE"]), "limit": 500}):
        live_adsets.add(ad.get("adset_id"))
    idle = [a for a in adsets if a["id"] not in live_adsets]
    adsets = [a for a in adsets if a["id"] in live_adsets]

    cbo = {c["id"]: c for c in campaigns if c.get("daily_budget")}
    by_camp: dict = {}
    for a in adsets:
        c = a.get("campaign") or {}
        by_camp.setdefault((c.get("id"), c.get("name") or "?"), []).append(a)

    total = 0.0
    print(f"\nACTIVE ad sets: {len(adsets)} in {len(by_camp)} campaigns "
          f"(CBO campaigns active: {len(cbo)})\n")
    for (cid, cname), lst in sorted(by_camp.items(), key=lambda kv: kv[0][1]):
        if cid in cbo:
            day = _myr(cbo[cid].get("daily_budget"))
            total += day
            print(f"  RM{day:>7,.0f}/day  CBO  {cname}  ({len(lst)} ad sets share it)")
            continue
        sub = 0.0
        for a in lst:
            sub += _myr(a.get("daily_budget"))
        total += sub
        print(f"  RM{sub:>7,.0f}/day  ABO  {cname}")
        for a in sorted(lst, key=lambda x: -_myr(x.get("daily_budget"))):
            print(f"      RM{_myr(a.get('daily_budget')):>5,.0f}  {a.get('name')}  ({a.get('id')})")
    # CBO campaigns that are ACTIVE but have no ACTIVE ad set (spend nothing)
    for cid, c in cbo.items():
        if not any(k[0] == cid for k in by_camp):
            print(f"  RM{0:>7,.0f}/day  CBO  {c.get('name')}  (no active ad set — spends nothing)")
    print(f"\nPLANNED TOTAL: RM{total:,.0f}/day  (ad sets with at least one ACTIVE ad)")
    if idle:
        idle_sum = sum(_myr(a.get("daily_budget")) for a in idle)
        print(f"  + RM{idle_sum:,.0f}/day sitting on {len(idle)} ACTIVE ad set(s) whose ads are all paused (spend nothing):")
        for a in idle:
            print(f"      RM{_myr(a.get('daily_budget')):>5,.0f}  {a.get('name')}  ({a.get('id')})  in {((a.get('campaign') or {}).get('name') or '?')[:50]}")
    print()

    print("ACTUAL spend per day (account):")
    rows = g._get_all(f"{acct}/insights", {"fields": "spend,date_start", "time_increment": 1,
                                           "date_preset": "last_7d"})
    for r in rows:
        print(f"  {r.get('date_start')}  RM{float(r.get('spend') or 0):>8,.0f}")
    today = g._get_all(f"{acct}/insights", {"fields": "spend", "date_preset": "today"})
    print(f"  today so far  RM{float((today[0] if today else {}).get('spend') or 0):>8,.0f}")
    print("\nDONE")


if __name__ == "__main__":
    main()
