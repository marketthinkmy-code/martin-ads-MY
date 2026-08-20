"""Where the money went versus where the sales came from — per creative, MY only.

The question this answers: is the account starving the ads that actually produce buyers while
feeding the ones that only produce sign-ups? Registration cost alone cannot tell those apart —
an ad with the cheapest CPL in the account and no buyers behind it is the most expensive ad there
is. So every ad is scored on three things:

  CPL          what a registration cost
  CPA          what a BUYER cost           (spend / MY sales)
  regs/sale    how many sign-ups it burned per buyer   <- this is "lead quality"

A high regs/sale means the hook attracts people who register and never buy. Two ads can share a
CPL of RM40 and be opposites: one converts 1 in 12, the other 1 in never.

MY sales come from the Paid Student List with the market resolved by the buyer's dial code, and
spend comes from THIS ad account — so both sides are Malaysia only and the ratio is honest.
Sheet ad names and Meta ad names are matched on cpa.ad_key, which survives width and punctuation
differences between the two systems.

Caveat printed with the results: a sale lands days after the spend that caused it, so a window
this short flatters ads that spent early and penalises ones that spent late.

Env:
  ADBOT_WINDOW_DAYS   lookback for both spend and sales (default 90)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict

from adbot import cpa
from adbot.clients.graph import GraphClient
from adbot.clients.sheets import SheetsClient
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

PHONE_HEADERS = ("whatsapp", "號碼", "号码", "phone", "电话", "電話")


def phone_market(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return None
    if d.startswith("60"):
        return "MY"
    if d.startswith("65") and len(d) >= 9:
        return "SG"
    if d.startswith("1") and len(d) == 11:
        return "US"
    if d.startswith("0"):
        return "MY"
    if len(d) == 8 and d[0] in "3689":
        return "SG"
    return "OTHER"


def tagged_market(*names: str) -> str | None:
    blob = " ".join(n or "" for n in names)
    if "[sg]" in blob or "martin-sg" in blob:
        return "SG"
    if "[my]" in blob or "martin-my" in blob:
        return "MY"
    return None


def money(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    s = load_settings()
    days = int(os.environ.get("ADBOT_WINDOW_DAYS", "90"))
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    since = today - dt.timedelta(days=days)
    graph = GraphClient(s.secrets.meta_token, s.secrets.meta_app_secret or "")
    token = result_action_type(s.meta.conversion_event)

    print(f"=== SPEND vs MY SALES · {since} .. {today} ({days}d) ===")

    # ── spend + registrations per ad NAME (a creative may run as several ads) ────────────────
    rows = graph.account_insights(
        s.meta.account_path, level="ad", fields="ad_name,spend,actions",
        time_range={"since": since.isoformat(), "until": today.isoformat()})
    spend, regs = defaultdict(float), defaultdict(float)
    names = {}
    for r in rows:
        key = cpa.ad_key(r.get("ad_name") or "")
        if not key:
            continue
        names.setdefault(key, r.get("ad_name"))
        spend[key] += money(r.get("spend"))
        regs[key] += extract_results(r.get("actions"), token)
    print(f"Meta: {len(rows)} ad rows -> {len(spend)} distinct creatives · "
          f"RM{sum(spend.values()):,.0f} spent")

    # ── MY sales per ad NAME from the sheet ─────────────────────────────────────────────────
    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    header_idx, cols = 0, cpa.find_columns(values[0])
    for i, row in enumerate(values[:8]):
        cand = cpa.find_columns(row)
        if cand.get("campaign", -1) >= 0 and cand.get("ad", -1) >= 0:
            header_idx, cols = i, cand
            break
    header = values[header_idx]
    phone_idx = next((i for i, h in enumerate(header)
                      if any(t in (h or "").casefold() for t in PHONE_HEADERS)), -1)
    if phone_idx < 0:
        raise SystemExit("no phone column — cannot separate MY from SG, refusing to report")

    sales, revenue, my_total, unmatched = defaultdict(int), defaultdict(float), 0, defaultdict(int)
    for raw in values[header_idx + 1:]:
        cell = lambda i: raw[i] if 0 <= i < len(raw) else ""
        ad = cpa.norm(cell(cols.get("ad", -1)))
        camp = cpa.norm(cell(cols.get("campaign", -1)))
        if not ad and not camp:
            continue
        market = phone_market(cell(phone_idx)) or tagged_market(camp, ad) or "UNKNOWN"
        date = cpa.parse_date(cell(cols.get("date", -1)))
        if market != "MY" or not date or date < since:
            continue
        my_total += 1
        key = cpa.ad_key(ad)
        sales[key] += 1
        revenue[key] += cpa._money(cell(cols.get("amount", -1)), s.cpa.price_myr)
        if key and key not in spend:
            unmatched[ad] += 1
    print(f"Sheet: {my_total} MY sales dated in window")

    # ── the table ───────────────────────────────────────────────────────────────────────────
    total_spend = sum(spend.values())
    print(f"\n{'spend':>8} {'share':>6} {'regs':>5} {'CPL':>6} {'sales':>5} {'CPA':>7} "
          f"{'regs/sale':>9}   ad")
    ranked = sorted(spend.items(), key=lambda kv: -kv[1])
    for key, sp in ranked:
        if sp < 1:
            continue
        n, rg = sales.get(key, 0), regs.get(key, 0)
        cpl = f"{sp / rg:,.0f}" if rg else "-"
        cpa_v = f"{sp / n:,.0f}" if n else "∞"
        per = f"{rg / n:,.1f}" if n else "-"
        print(f"{sp:8,.0f} {sp / total_spend * 100:5.1f}% {rg:5.0f} {cpl:>6} {n:5d} {cpa_v:>7} "
              f"{per:>9}   {str(names.get(key))[:46]}")

    # ── the allocation question ─────────────────────────────────────────────────────────────
    with_sales = sum(sp for k, sp in spend.items() if sales.get(k))
    without = total_spend - with_sales
    print(f"\n########## ALLOCATION ##########")
    print(f"  total spend            RM{total_spend:>10,.0f}")
    print(f"  to ads that sold       RM{with_sales:>10,.0f}  ({with_sales / total_spend * 100:.0f}%)"
          f"   {sum(1 for k in spend if sales.get(k))} creatives")
    print(f"  to ads that did NOT    RM{without:>10,.0f}  ({without / total_spend * 100:.0f}%)"
          f"   {sum(1 for k, v in spend.items() if v >= 1 and not sales.get(k))} creatives")
    print(f"  MY sales               {my_total}")
    print(f"  blended CPA            RM{total_spend / my_total:,.0f}" if my_total else "  no sales")
    print(f"  revenue                RM{sum(revenue.values()):,.0f}"
          f"   ROAS {sum(revenue.values()) / total_spend:.2f}x" if total_spend else "")

    earners = [(k, spend[k], sales[k], spend[k] / sales[k]) for k in sales if spend.get(k)]
    if earners:
        best = sorted(earners, key=lambda x: x[3])
        print(f"\n########## CHEAPEST BUYERS — is the money following them? ##########")
        print(f"  {'CPA':>7} {'spend':>8} {'share':>6} {'sales':>5}   ad")
        for k, sp, n, c in best[:10]:
            print(f"  {c:7,.0f} {sp:8,.0f} {sp / total_spend * 100:5.1f}% {n:5d}   "
                  f"{str(names.get(k))[:46]}")

    burners = sorted(((k, v, regs.get(k, 0)) for k, v in spend.items()
                      if v >= 200 and not sales.get(k)), key=lambda x: -x[1])
    if burners:
        print(f"\n########## SPENT RM200+ WITH NO MY BUYER ##########")
        print(f"  {'spend':>8} {'regs':>5} {'CPL':>6}   ad")
        for k, sp, rg in burners[:15]:
            print(f"  {sp:8,.0f} {rg:5.0f} {(f'{sp / rg:,.0f}' if rg else '-'):>6}   "
                  f"{str(names.get(k))[:46]}")

    if unmatched:
        print(f"\n########## MY sales whose ad name is not in this account ##########")
        print("  (SG-side creative, renamed, or deleted — spend for these is not in the table)")
        for ad, n in sorted(unmatched.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {n:3d}   {ad[:60]}")

    print(f"\nNOTE: sales land days after the spend that caused them, so a {days}-day window "
          f"flatters ads that spent early and penalises ads that spent late.")


if __name__ == "__main__":
    main()
