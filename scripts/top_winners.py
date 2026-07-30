"""Read-only: rank the winning ADS and AD SETS by real paid sales.

Answers "which creatives and which audiences actually sell?" straight from the Paid Student List.
Ranks over 30d / 60d / lifetime so a historical winner that has gone cold is visible as such.

The sheet is SHARED between the MY and SG ad accounts, so every row is tagged by market from its
campaign name — a top-line number that silently mixes SG sales into a MY decision is worse than
no number at all. No writes.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

WINDOWS = (30, 60)


def market_of(campaign: str) -> str:
    """MY / SG / ? from the sheet's campaign name (normalised, lowercase)."""
    c = campaign or ""
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if "[my]" in c or "martin-my" in c:
        return "MY"
    return "?"          # legacy campaigns with no market tag — mostly the older MY account


def _rank(sales, key_fn, today, top_n, label):
    """Print a top-N table for whichever grouping key_fn extracts."""
    groups = defaultdict(list)
    for s in sales:
        k = (key_fn(s) or "").strip()
        if k:
            groups[k].append(s)

    def n_in(rows, days):
        cut = today - dt.timedelta(days=days)
        return sum(1 for r in rows if r.date and r.date > cut)

    ranked = sorted(groups.items(), key=lambda kv: (-n_in(kv[1], 60), -len(kv[1])))
    print(f"\n=== TOP {top_n} {label} (排名依 60 天成交) ===")
    print(f"  {'#':>2} {'60d':>4} {'30d':>4} {'life':>5}  {'市场':4} name")
    for i, (name, rows) in enumerate(ranked[:top_n], 1):
        markets = {market_of(r.campaign) for r in rows if r.date and r.date > today - dt.timedelta(days=60)}
        tag = "+".join(sorted(markets)) or "-"
        print(f"  {i:>2} {n_in(rows, 60):>4} {n_in(rows, 30):>4} {len(rows):>5}  {tag:4} {name[:52]}")
    return ranked


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT
    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, cols, _hdr = cpa.parse_sales(values, s.cpa.price_myr)
    dated = [x for x in sales if x.date]

    print(f"=== WINNERS · today MYT={today} · tab '{s.cpa.sales_tab}' ===")
    print(f"{len(values)} sheet rows · {len(sales)} sales parsed · {len(dated)} dated")
    if dated:
        print(f"date range {min(d.date for d in dated)} .. {max(d.date for d in dated)}")
    for w in WINDOWS:
        cut = today - dt.timedelta(days=w)
        rows = [x for x in dated if x.date > cut]
        by_mkt = defaultdict(int)
        for r in rows:
            by_mkt[market_of(r.campaign)] += 1
        print(f"  last {w}d: {len(rows)} sales  ({dict(sorted(by_mkt.items()))})")

    _rank([x for x in sales if x.ad], lambda x: x.ad, today, 5, "ADS")
    _rank([x for x in sales if x.adset], lambda x: x.adset, today, 3, "AD SETS")

    # MY-only view: the SG rows are a different ad account and must not drive MY decisions.
    my_sales = [x for x in sales if market_of(x.campaign) != "SG"]
    print(f"\n\n########## MY ONLY (剔除 SG，{len(my_sales)} sales) ##########")
    _rank([x for x in my_sales if x.ad], lambda x: x.ad, today, 5, "ADS · MY")
    _rank([x for x in my_sales if x.adset], lambda x: x.adset, today, 3, "AD SETS · MY")


if __name__ == "__main__":
    main()
