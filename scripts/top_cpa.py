"""Read-only: the creatives with the best MY CPA over the last N days (default 60), with the page post
behind each so the operator can rebuild them by hand with "use existing post".

For every creative that has >= 1 MY sale in the window (Paid Student List, market from the UTM
campaign/ad-set tags), the spend of ALL its copies in this account over the same window is pooled
(Meta insights, ad level, joined by cpa.ad_key), giving CPA = spend / MY sales and CPL. Ranked by
CPA ascending; the top N are printed with the effective_object_story_id of the copy that spent
the most (all copies of a creative share one post when they were built from it). Creatives whose
sales cannot be tied to any spend here are listed separately. Aggregates only. No writes.
"""
from __future__ import annotations

import datetime as dt
import math
import os
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings


def market_of(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).casefold()
    if "[sg]" in blob or "martin-sg" in blob:
        return "SG"
    if "[my]" in blob or "martin-my" in blob:
        return "MY"
    return "?"


def _money(x) -> float:
    try:
        return float(str(x).replace(",", "") or 0)
    except ValueError:
        return 0.0


def _f(v) -> str:
    return "∞" if v is None or v == math.inf else f"{v:,.0f}"



def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    days = int(os.environ.get("ADBOT_WINDOW") or 60)
    top_n = int(os.environ.get("ADBOT_TOP_N") or 5)
    line = s.cpa.max_acceptable_myr
    print(f"===== TOP CPA · MY sales in the last {days} days · {today} MYT =====")
    print(f"line: CPA ≤ RM{line:.0f} · CPL line RM{s.kpi.cpl_threshold_myr:.0f} · spend pooled over every copy in this account\n")

    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _c, _h = cpa.parse_sales(values, s.cpa.price_myr)
    my_n, my30, names, adsets_of, untagged = defaultdict(int), defaultdict(int), {}, defaultdict(lambda: defaultdict(int)), 0
    for x in sales:
        if not x.ad or not x.date:
            continue
        age = (today - x.date).days
        if age < 0 or age > days:
            continue
        k = cpa.ad_key(x.ad)
        names.setdefault(k, x.ad)
        mk = market_of(x.campaign, x.adset)
        if mk == "MY":
            my_n[k] += 1
            adsets_of[k][x.adset or "(空)"] += 1
            if age <= 30:
                my30[k] += 1
        elif mk == "?":
            untagged += 1
    print(f"{len(my_n)} creatives with ≥1 MY sale · {sum(my_n.values())} MY sales · {untagged} untagged sales ignored\n")

    since = (today - dt.timedelta(days=days)).isoformat()
    rows = g.account_insights(acct, level="ad",
                              fields="ad_id,ad_name,adset_id,adset_name,campaign_name,spend,actions",
                              time_range={"since": since, "until": today.isoformat()})
    pooled, copies = defaultdict(lambda: [0.0, 0.0]), defaultdict(dict)
    for r in rows:
        k = cpa.ad_key(r.get("ad_name") or "")
        if k not in my_n:
            continue
        sp, rg = _money(r.get("spend")), extract_results(r.get("actions"), token)
        pooled[k][0] += sp
        pooled[k][1] += rg
        c = copies[k].setdefault(r["ad_id"], {"name": r.get("ad_name"), "adset": r.get("adset_name"), "camp": r.get("campaign_name"), "sp": 0.0})
        c["sp"] += sp

    ranked, no_spend = [], []
    for k, n in my_n.items():
        sp, rg = pooled[k]
        if sp <= 0:
            no_spend.append(k)
            continue
        ranked.append((sp / n, k, n, sp, rg))
    ranked.sort()

    print(f"=== TOP {top_n} by CPA{days} (all creatives with spend here: {len(ranked)}) ===")
    print(f"  #  MY{days} MY30  spend{days}   CPA{days}  CPL{days}  on/copies  ad name  →  post id")
    for i, (cpa_v, k, n, sp, rg) in enumerate(ranked[:max(top_n, 10)], 1):
        cps = copies[k]
        best_id = max(cps, key=lambda a: cps[a]["sp"])
        post, status, active = "?", "?", 0
        try:
            for ad_id in cps:
                o = g.get_object(ad_id, "effective_status")
                cps[ad_id]["status"] = o.get("effective_status")
            active = sum(1 for c in cps.values() if c.get("status") == "ACTIVE")
            o = g.get_object(best_id, "name,effective_status,creative{id,effective_object_story_id}")
            cr = o.get("creative") or {}
            post = cr.get("effective_object_story_id") or f"(no post; creative {cr.get('id')})"
        except Exception as exc:  # noqa: BLE001 - one unreadable copy must not sink the report
            post = f"?({str(exc)[:60]})"
        mark = "✅" if cpa_v <= line else "  "
        flag = "" if i <= top_n else "   (next)"
        print(f"{mark}{i:>2}  {n:>4} {my30[k]:>4}  {sp:>8,.0f}  {_f(cpa_v):>7}  {_f(sp / rg if rg else None):>6}  {active}/{len(cps):<7}  "
              f"{cps[best_id]['name']}  →  {post}{flag}")
        print(f"        sold in: {', '.join(f'{a}×{c}' for a, c in sorted(adsets_of[k].items(), key=lambda t: -t[1])[:3])} · "
              f"biggest copy in '{cps[best_id]['camp'][:60]}' / '{cps[best_id]['adset'][:40]}'")
    if no_spend:
        print(f"\n=== MY sales in {days}d but no spend in this account ({len(no_spend)}) — 旧账户素材，这里没有 post 可用 ===")
        for k in sorted(no_spend, key=lambda k: -my_n[k]):
            print(f"  MY{days} {my_n[k]}  {names[k]}")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
