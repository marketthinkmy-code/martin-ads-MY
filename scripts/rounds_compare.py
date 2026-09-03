"""The last N webinar rounds side by side: spend, CPL, MY sales, CPA, ROAS, regs-per-sale.

One row per Thursday-to-Wednesday cycle (the operator's week, ending on webinar night), so
"CPL spiked" and "lead quality dropped" stop being feelings and become a trend line. Sales are
MY-only via the buyer's dial code; revenue uses the sheet's own amounts.

Also prints a scorecard for every campaign built in the current push (ids listed below): per-ad
spend / regs / CPL since launch, so "how are the new ads doing" is one section, not a hunt.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from adbot import cpa
from adbot.clients.graph import GraphClient
from adbot.clients.sheets import SheetsClient
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

ROUNDS = 6
PHONE_HEADERS = ("whatsapp", "號碼", "号码", "phone", "电话", "電話")

# The August build wave — everything created since 8/5. Ids inline because this is a snapshot
# report for a specific review, not a permanent monitor.
NEW_CAMPAIGNS = [
    ("饮食吸收 1-1-4 (Hook Edits)", "120248380457270575"),
    ("生理体质 1-1-3 (Hook Edits)", "120248380460540575"),
    ("观念打破 1-1-3 (Hook Edits)", "120248380464420575"),
    ("早餐面包 Hook1+2 · Parents (8/20排期)", "120248453560750575"),
    ("早餐面包 Hook1+2 · 操作者自建", "120248464196820575"),
    ("LAL 分档 1% / 2-3% / 4-5% (8/20排期)", "120248449442210575"),
    ("LAL Top5 1-1-5 (8/20排期)", "120248431600650575"),
    ("LAL Top3 lifetime 1-1-3 (8/20排期)", "120248448827640575"),
    ("吉隆坡回顾 · 再营销", "120248451664160575"),
    ("被饿死的赢家 1-3-1", "120248464922520575"),
    ("F&R V12 1-1-1", "120248085451810575"),
    ("图文 A 知识科普", "120248264040980575"),
    ("图文 B 迷思打破", "120248264058290575"),
    ("图文 C 见证证明", "120248264069330575"),
    ("图文 D 焦虑情感", "120248264080910575"),
    ("图文 E 低期待值", "120248264086050575"),
    ("图文 F 备用", "120248264090860575"),
]


def phone_market(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return None
    if d.startswith("60") or d.startswith("0"):
        return "MY"
    if d.startswith("65") and len(d) >= 9:
        return "SG"
    if len(d) == 8 and d[0] in "3689":
        return "SG"
    return "OTHER"


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    graph = GraphClient(s.secrets.meta_token, s.secrets.meta_app_secret or "")
    token = result_action_type(s.meta.conversion_event)
    account = s.meta.account_path

    # ── MY sales rows once, bucketed later ───────────────────────────────────────────────────
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    header_idx, cols = 0, cpa.find_columns(values[0])
    for i, row in enumerate(values[:8]):
        cand = cpa.find_columns(row)
        if cand.get("campaign", -1) >= 0 and cand.get("ad", -1) >= 0:
            header_idx, cols = i, cand
            break
    phone_idx = next((i for i, h in enumerate(values[header_idx])
                      if any(t in (h or "").casefold() for t in PHONE_HEADERS)), -1)
    my_sales = []
    for raw in values[header_idx + 1:]:
        cell = lambda i: raw[i] if 0 <= i < len(raw) else ""
        ad = cpa.norm(cell(cols.get("ad", -1)))
        if not ad and not cpa.norm(cell(cols.get("campaign", -1))):
            continue
        if phone_market(cell(phone_idx)) != "MY":
            continue
        date = cpa.parse_date(cell(cols.get("date", -1)))
        if date:
            my_sales.append((date, ad, cpa._money(cell(cols.get("amount", -1)), s.cpa.price_myr)))

    # ── rounds: Thu..Wed cycles, most recent (possibly partial) first in time order ──────────
    this_thu = today - dt.timedelta(days=(today.weekday() - 3) % 7)
    print(f"=== LAST {ROUNDS} WEBINAR ROUNDS · today={today} ===")
    print(f"{'round (Thu..Wed)':>22} {'spend':>8} {'regs':>5} {'CPL':>5} {'sales':>5} "
          f"{'CPA':>7} {'ROAS':>5} {'regs/sale':>9}")
    for i in range(ROUNDS - 1, -1, -1):
        start = this_thu - dt.timedelta(days=7 * i)
        end = min(start + dt.timedelta(days=6), today)
        rows = graph.account_insights(account, level="account", fields="spend,actions",
                                      time_range={"since": start.isoformat(),
                                                  "until": end.isoformat()})
        spend = sum(float(r.get("spend") or 0) for r in rows)
        regs = sum(extract_results(r.get("actions"), token) for r in rows)
        sold = [x for x in my_sales if start <= x[0] <= end]
        rev = sum(x[2] for x in sold)
        n = len(sold)
        cpl = f"{spend / regs:,.0f}" if regs else "-"
        cpa_s = f"{spend / n:,.0f}" if n else "∞"
        roas = f"{rev / spend:.2f}" if spend else "-"
        per = f"{regs / n:,.1f}" if n else "-"
        mark = " <- 本轮(未完)" if end == today and (today.weekday() != 2) else ""
        print(f"{start.isoformat()}..{end.isoformat():>10} {spend:8,.0f} {regs:5.0f} {cpl:>5} "
              f"{n:5d} {cpa_s:>7} {roas:>5} {per:>9}{mark}")
        for d_, ad_, _amt in sold:
            print(f"{'':>24}sale {d_}  {ad_[:52]}")

    # ── new-build scorecard ──────────────────────────────────────────────────────────────────
    ins = {}
    for r in graph.account_insights(account, level="ad", fields="ad_id,spend,actions",
                                    date_preset="last_14d"):
        if r.get("ad_id"):
            ins[r["ad_id"]] = r
    print(f"\n\n=== NEW BUILDS · per-ad, last 14d ===")
    for label, cid in NEW_CAMPAIGNS:
        try:
            camp = graph.get_object(cid, "name,effective_status,daily_budget")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{label}] unreadable: {str(exc)[:120]}")
            continue
        print(f"\n[{label}] {camp.get('effective_status')} · budget {camp.get('daily_budget', '?')}")
        for ad in graph.list_ads_under_campaign(cid):
            row = ins.get(ad["id"])
            sp = float(row.get("spend") or 0) if row else 0.0
            rg = extract_results(row.get("actions"), token) if row else 0.0
            cpl = f"{sp / rg:,.0f}" if rg else ("-" if sp == 0 else "∞")
            print(f"    {ad.get('effective_status', '?'):>15} {sp:8,.2f} {rg:4.0f} {cpl:>5}  "
                  f"{str(ad.get('name'))[:52]}")


if __name__ == "__main__":
    main()
