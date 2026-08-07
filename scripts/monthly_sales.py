"""Read-only: month-by-month MY paid-sales analysis straight from the Paid Student List.

The workbook is SHARED between the MY and SG ad accounts, so the whole exercise turns on getting
the market of each row right. A prefix heuristic alone is not enough — plenty of older rows carry
no [MY]/[SG] tag at all. So each row is resolved in three passes, most trustworthy first:

  1. TAG    — campaign name contains [my]/martin-my (or [sg]/martin-sg)  -> that market, certain.
  2. MATCH  — untagged, but its campaign / ad set / ad name matches a real entity in THIS ad
              account (act_...MY) by ad_key (width- and punctuation-insensitive)  -> MY.
  3. UNKNOWN— neither. Printed in full rather than silently bucketed: this token only sees the MY
              account, so a non-match may be an SG row OR a deleted MY campaign. Guessing either
              way would corrupt the number the operator is asking for.

Only rows resolved MY are counted in the monthly figures. No writes.
"""
from __future__ import annotations

import calendar
import datetime as dt
import os
from collections import defaultdict

from adbot import cpa
from adbot.clients.graph import GraphClient
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

MONTHS = [(2026, 6), (2026, 7), (2026, 8)]


def tagged_market(campaign: str) -> str | None:
    """MY / SG when the campaign name says so outright, else None."""
    c = campaign or ""
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if "[my]" in c or "martin-my" in c:
        return "MY"
    return None


def my_name_keys(graph: GraphClient, account: str) -> tuple[set, set, set]:
    """ad_key sets for every campaign / ad set / ad name living in the MY account."""
    def keys(edge: str) -> set:
        rows = graph._get_all(f"{account}/{edge}", {"fields": "name", "limit": 500})
        return {cpa.ad_key(r.get("name", "")) for r in rows if r.get("name")}
    camps, adsets, ads = keys("campaigns"), keys("adsets"), keys("ads")
    print(f"MY account inventory: {len(camps)} campaigns · {len(adsets)} ad sets · {len(ads)} ads")
    return camps, adsets, ads


def resolve(sale, my_camps, my_adsets, my_ads) -> tuple[str, str]:
    """(market, how) for one sale row."""
    tag = tagged_market(sale.campaign)
    if tag:
        return tag, "tag"
    if cpa.ad_key(sale.campaign) in my_camps:
        return "MY", "match:campaign"
    if sale.adset and cpa.ad_key(sale.adset) in my_adsets:
        return "MY", "match:adset"
    if sale.ad and cpa.ad_key(sale.ad) in my_ads:
        return "MY", "match:ad"
    return "UNKNOWN", "-"


def month_of(d: dt.date) -> tuple[int, int]:
    return (d.year, d.month)


def _table(rows, key_fn, label, indent="  "):
    groups = defaultdict(list)
    for r in rows:
        k = (key_fn(r) or "").strip()
        groups[k or "(空白)"].append(r)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    print(f"{indent}--- {label} ---")
    for name, rs in ranked:
        rev = sum(r.amount for r in rs)
        print(f"{indent}  {len(rs):>3} 单  RM{rev:>9,.0f}   {name[:66]}")
    return ranked


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    graph = GraphClient(s.secrets.meta_token, s.secrets.meta_app_secret or "")
    account = s.meta.account_path

    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, cols, header = cpa.parse_sales(values, s.cpa.price_myr)

    print(f"=== MONTHLY MY SALES · today MYT={today} ===")
    print(f"sheet '{s.cpa.sales_tab}' · {len(values)} rows · {len(sales)} sales parsed")
    print(f"columns: {cols}")
    dated = [x for x in sales if x.date]
    if dated:
        print(f"dated {len(dated)} · range {min(d.date for d in dated)} .. {max(d.date for d in dated)}")
    print(f"undated (excluded from monthly figures): {len(sales) - len(dated)}")

    my_camps, my_adsets, my_ads = my_name_keys(graph, account)

    resolved = []
    how_counts = defaultdict(int)
    for x in sales:
        mkt, how = resolve(x, my_camps, my_adsets, my_ads)
        resolved.append((x, mkt, how))
        how_counts[f"{mkt}/{how}"] += 1
    print("\nmarket resolution over ALL rows:")
    for k, v in sorted(how_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>5}  {k}")

    # ---- the answer: MY only, month by month -------------------------------------------------
    for (yr, mo) in MONTHS:
        last = calendar.monthrange(yr, mo)[1]
        rows = [x for x, m, _ in resolved if m == "MY" and x.date and month_of(x.date) == (yr, mo)]
        rev = sum(r.amount for r in rows)
        label = f"{yr}-{mo:02d}"
        span = f"{yr}-{mo:02d}-01..{yr}-{mo:02d}-{last:02d}"
        print(f"\n\n########## {label} · MY · {len(rows)} 单 · RM{rev:,.0f} ({span}) ##########")
        if not rows:
            print("  (no MY sales dated in this month)")
            continue
        _table(rows, lambda r: r.ad, "转化的广告 (ad)")
        _table(rows, lambda r: r.adset, "广告组 (ad set)")
        _table(rows, lambda r: r.campaign, "campaign")

    # ---- what we could NOT place, printed in full so nothing hides ---------------------------
    unknown = [x for x, m, _ in resolved
               if m == "UNKNOWN" and x.date and month_of(x.date) in MONTHS]
    print(f"\n\n########## UNKNOWN market · {len(unknown)} 单 in Jun-Aug (NOT counted above) ##########")
    print("这些行的 campaign/adset/ad 名字在 MY 账户里找不到，也没有 [MY]/[SG] 标签。")
    ug = defaultdict(list)
    for x in unknown:
        ug[(x.campaign, x.ad)].append(x)
    for (c, a), rs in sorted(ug.items(), key=lambda kv: -len(kv[1])):
        months = sorted({f"{r.date:%Y-%m}" for r in rs})
        print(f"  {len(rs):>3} 单  {','.join(months)}  ad='{a[:44]}'  camp='{c[:44]}'")

    sg = [x for x, m, _ in resolved if m == "SG" and x.date and month_of(x.date) in MONTHS]
    print(f"\n(参考) 同期 SG: {len(sg)} 单 — 已排除，不进 MY 数字")


if __name__ == "__main__":
    main()
