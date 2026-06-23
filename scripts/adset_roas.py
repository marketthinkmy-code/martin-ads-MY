"""Read-only: rank ad sets by ROAS (Paid Student List revenue / Meta spend) + CPL.

Used to pick the single best-performing ad set to clone when scaling into a new campaign.
Revenue = sum of the sheet's purchase Amount attributed to the ad set (by UTM ad-set name);
ROAS = revenue / Meta spend; CPL = spend / registrations. Window: last 60 days. No writes.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import _mkey, extract_results, result_action_type
from adbot.settings import load_settings

WINDOW_DAYS = 60


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    cutoff = today - dt.timedelta(days=WINDOW_DAYS)
    ceiling = s.kpi.cpl_threshold_myr

    # revenue + paid sales per (campaign, ad set) from the sheet
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _cols, _hdr = cpa.parse_sales(values, s.cpa.price_myr)
    rev, cnt = defaultdict(float), defaultdict(int)
    for sale in sales:
        if sale.date and sale.date > cutoff:
            k = (_mkey(sale.campaign), sale.adset)
            rev[k] += sale.amount
            cnt[k] += 1

    # spend + registrations per ad set from Meta
    token = result_action_type(s.meta.conversion_event)
    g = graph_client(s)
    insights = g.account_insights(
        s.meta.account_path, level="adset",
        fields="campaign_name,adset_name,adset_id,spend,actions",
        time_range={"since": cutoff.isoformat(), "until": today.isoformat()})

    data = []
    for r in insights:
        try:
            spend = float(r.get("spend") or 0)
        except (TypeError, ValueError):
            spend = 0.0
        if spend <= 0:
            continue
        k = (_mkey(r.get("campaign_name", "")), cpa.norm(r.get("adset_name", "")))
        regs = extract_results(r.get("actions"), token)
        revenue, n = rev.get(k, 0.0), cnt.get(k, 0)
        cpl = spend / regs if regs else math.inf
        roas = revenue / spend if spend else 0.0
        data.append({"campaign": r.get("campaign_name", ""), "adset": r.get("adset_name", ""),
                     "adset_id": r.get("adset_id", ""), "spend": spend, "regs": regs,
                     "cpl": cpl, "sales": n, "revenue": revenue, "roas": roas})

    data.sort(key=lambda d: -d["roas"])
    print(f"Ad-set ROAS · last {WINDOW_DAYS} days  (revenue = Paid Student List Amount; ceiling RM{ceiling:.0f})\n")
    hdr = f"{'campaign':26} {'ad set':18} {'spend':>8} {'reg':>4} {'CPL':>6} {'sale':>4} {'revenue':>9} {'ROAS':>5}"
    print(hdr)
    print("-" * len(hdr))
    for d in data[:25]:
        cpls = "∞" if d["cpl"] == math.inf else f"{d['cpl']:.0f}"
        print(f"{d['campaign'][:26]:26} {d['adset'][:18]:18} {d['spend']:8.0f} {d['regs']:4.0f} "
              f"{cpls:>6} {d['sales']:4} {d['revenue']:9.0f} {d['roas']:5.2f}")

    winners = [d for d in data if d["cpl"] <= ceiling and d["revenue"] > 0]
    winners.sort(key=lambda d: -d["roas"])
    print(f"\n🏆 Best ad set — CPL ≤ RM{ceiling:.0f} AND highest ROAS:")
    for d in winners[:5]:
        print(f"  ROAS {d['roas']:.2f} · CPL RM{d['cpl']:.0f} · {d['sales']} sales · "
              f"revenue RM{d['revenue']:,.0f} · spend RM{d['spend']:,.0f}  ·  "
              f"{d['campaign']} | {d['adset']}  (adset_id {d['adset_id']})")
    if not winners:
        print("  (no ad set with CPL under ceiling + attributed revenue)")


if __name__ == "__main__":
    main()
