"""Read-only: every ACTIVE ad's CPL and its MY-only CPA from the Paid Student List, with a
pause / lower / keep suggestion per ad. Nothing is written — the operator decides.

Why MY-only: the sheet holds MY and SG sales together and both markets run the same creatives.
An ad that sells in SG can be a zero in MY (2026-09-11 lead-quality report), so CPA here counts
only sales whose UTM campaign is tagged [MY] / MARTIN-MY. Sales are matched by ad NAME (all
copies of a creative pooled) against the pooled spend of every copy in the same window, so
a creative running in three ad sets is judged once, not three times.

Suggestion rules (tiers from config: max_acceptable / hard_stop; lead-quality gate from PR #20):
  关   ·  ≥ RM1,000 spent in 60d with 0 MY sales
       ·  ≥ 30 MY registrants in 90d with 0 MY buyers (registers, never pays)
       ·  CPA60 above the hard stop
  调低 ·  CPA60 between max_acceptable and hard_stop
       ·  RM500–1,000 spent in 60d with 0 MY sales (trend is bad, verdict not yet fair)
       ·  CPL14 above 1.3x the CPL threshold while CPA is still acceptable
  保留 ·  CPA60 at or under max_acceptable      (可加 when under healthy_max and CPL in line)
  新   ·  younger than 7 days or under RM300 spent — no verdict yet
Aggregates only: no personal data is read from the sheet beyond date / UTM / child age.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

WINDOWS = (14, 30, 60, 90)


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


def _fmt_cpa(v) -> str:
    if v is None or v == math.inf:
        return "∞"
    return f"{v:,.0f}"


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    print(f"===== ACTIVE ADS · CPL + MY-only CPA · {today} MYT =====")

    # ── 1. active ads, their ad sets and budgets ─────────────────────────────
    adsets = {a["id"]: a for a in g._get_all(f"{acct}/adsets", {
        "fields": "id,name,daily_budget,campaign_id",
        "effective_status": json.dumps(["ACTIVE"]), "limit": 200})}
    camps = {c["id"]: c for c in g._get_all(f"{acct}/campaigns", {
        "fields": "id,name,daily_budget,effective_status",
        "effective_status": json.dumps(["ACTIVE"]), "limit": 200})}
    active = []
    for cid, camp in camps.items():
        for ad in g.list_ads_under_campaign(cid):
            if ad.get("effective_status") != "ACTIVE":
                continue
            aset = adsets.get(ad.get("adset_id"), {})
            created = cpa.parse_date((ad.get("created_time") or "")[:10])
            active.append({
                "id": ad["id"], "name": ad.get("name", ad["id"]), "key": cpa.ad_key(ad.get("name", "")),
                "camp": camp.get("name", ""), "adset_id": ad.get("adset_id"),
                "budget": (int(aset["daily_budget"]) / 100 if aset.get("daily_budget") else None),
                "cbo": (int(camp["daily_budget"]) / 100 if camp.get("daily_budget") else None),
                "age_days": (today - created).days if created else None,
            })
    print(f"active ads: {len(active)}")

    # ── 2. spend + registrations per ad and pooled per creative name, per window ─
    by_ad = {w: {} for w in WINDOWS}
    by_key = {w: defaultdict(lambda: {"spend": 0.0, "regs": 0.0}) for w in WINDOWS}
    for w in WINDOWS:
        since = (today - dt.timedelta(days=w)).isoformat()
        for r in g.account_insights(acct, level="ad", fields="ad_id,ad_name,spend,actions",
                                    time_range={"since": since, "until": today.isoformat()}):
            sp, regs = _money(r.get("spend")), extract_results(r.get("actions"), token)
            by_ad[w][r.get("ad_id")] = (sp, regs)
            k = cpa.ad_key(r.get("ad_name") or "")
            by_key[w][k]["spend"] += sp
            by_key[w][k]["regs"] += regs

    # ── 3. MY sales per creative name from the Paid Student List ─────────────
    sheets = SheetsClient(s.secrets.google_sa_json)
    sales, _cols, _hdr = cpa.parse_sales(sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab),
                                          s.cpa.price_myr)
    my_sales = {w: defaultdict(int) for w in WINDOWS}
    my_life, all_life = defaultdict(int), defaultdict(int)
    untagged = {w: defaultdict(int) for w in WINDOWS}
    for x in sales:
        if not x.ad:
            continue
        k, mk = cpa.ad_key(x.ad), market_of(x.campaign, x.adset)
        all_life[k] += 1
        if mk == "MY":
            my_life[k] += 1
        if not x.date:
            continue
        age = (today - x.date).days
        for w in WINDOWS:
            if 0 <= age <= w:
                if mk == "MY":
                    my_sales[w][k] += 1
                elif mk == "?":
                    untagged[w][k] += 1

    # ── 4. MY registrants per creative (90d) + teen share, from the Register tab ─
    regs90, teen90 = defaultdict(int), defaultdict(int)
    try:
        vals = sheets.read_tab(s.cpa.spreadsheet_id, "Register")
        hdr = vals[0]
        keys = [cpa._hkey(h) for h in hdr]

        def col(*needles):
            for n in needles:
                for i, k in enumerate(keys):
                    if n in k:
                        return i
            return -1
        ci = {"date": col("createddate", "date", "日期"), "camp": col("utmcampaign", "campaign"),
              "ad": col("utmadsname", "utmadname", "utmcontent"), "age": col("几岁", "幾歲", "childrensage", "age")}
        for r in vals[1:]:
            def cell(i):
                return (r[i] if 0 <= i < len(r) else "").strip()
            d = cpa.parse_date(cell(ci["date"]))
            if not d or (today - d).days > 90 or market_of(cell(ci["camp"])) != "MY":
                continue
            k = cpa.ad_key(cell(ci["ad"]))
            regs90[k] += 1
            m = re.search(r"\d{1,2}", cell(ci["age"]))
            if m and int(m.group()) >= 15:
                teen90[k] += 1
    except Exception as exc:  # noqa: BLE001 - the register join is a bonus, not the core
        print(f"(register tab not joined: {str(exc)[:120]})")

    # ── 5. verdict per active ad ─────────────────────────────────────────────
    thr = s.kpi.cpl_threshold_myr
    tiers = s.cpa
    print(f"\nrules: CPL threshold RM{thr:.0f} · CPA max_acceptable RM{tiers.max_acceptable_myr:.0f} · "
          f"hard_stop RM{tiers.hard_stop_myr:.0f} · MY-only sales, matched by creative name\n")
    hdr_line = (f"{'ad':42} {'budget':>7} {'14d$':>6} {'reg':>4} {'CPL14':>6} {'30d$':>6} {'reg':>4} {'CPL30':>6} "
                f"{'MYsale 30/60/90':>15} {'CPA60':>6} {'reg90/buy':>9} {'teen':>5}  建议")
    print(hdr_line)
    rows = []
    for a in active:
        k = a["key"]
        sp14, rg14 = by_ad[14].get(a["id"], (0.0, 0.0))
        sp30, rg30 = by_ad[30].get(a["id"], (0.0, 0.0))
        pooled60 = by_key[60][k]["spend"]
        my30, my60, my90 = my_sales[30][k], my_sales[60][k], my_sales[90][k]
        un60 = untagged[60][k]
        cpa60 = (pooled60 / my60) if my60 else math.inf
        cpl14 = (sp14 / rg14) if rg14 else math.inf
        cpl30 = (sp30 / rg30) if rg30 else math.inf
        r90, t90 = regs90.get(k, 0), teen90.get(k, 0)
        teen = (t90 / r90) if r90 else None

        why = []
        if (a["age_days"] is not None and a["age_days"] < 7) or (pooled60 < 300 and my60 == 0):
            verdict = "新·等"
            why.append(f"{a['age_days']}d / RM{pooled60:,.0f}")
        elif my60 and cpa60 > tiers.hard_stop_myr:
            verdict = "关"; why.append(f"CPA60 RM{cpa60:,.0f} > hard stop")
        elif my60 == 0 and pooled60 >= 1000:
            verdict = "关"; why.append(f"60d RM{pooled60:,.0f} 0 MY sale")
        elif my90 == 0 and r90 >= 30:
            verdict = "关"; why.append(f"90d {r90} MY regs, 0 buyer")
        elif my60 and cpa60 > tiers.max_acceptable_myr:
            verdict = "调低"; why.append(f"CPA60 RM{cpa60:,.0f} 过 max_acceptable")
        elif my60 == 0 and pooled60 >= 500:
            verdict = "调低"; why.append(f"60d RM{pooled60:,.0f} 0 MY sale (未到裁决线)")
        elif rg14 and cpl14 > 1.3 * thr:
            verdict = "调低"; why.append(f"CPL14 RM{cpl14:,.0f} > 1.3x 门槛")
        else:
            verdict = "保留"
            if my60 and cpa60 <= tiers.healthy_max_myr and (not rg14 or cpl14 <= thr * 1.3):
                verdict = "保留·可加"
            why.append(f"CPA60 RM{_fmt_cpa(cpa60)}")
        if un60:
            why.append(f"+{un60} untagged sale")
        if teen is not None and teen >= 0.3:
            why.append(f"teen {teen:.0%}")
        rows.append((verdict, a, sp14, rg14, cpl14, sp30, rg30, cpl30, my30, my60, my90, cpa60, r90, my90, teen, why))

    order = {"关": 0, "调低": 1, "新·等": 2, "保留": 3, "保留·可加": 4}
    for (verdict, a, sp14, rg14, cpl14, sp30, rg30, cpl30, my30, my60, my90, cpa60, r90, b90, teen, why) in \
            sorted(rows, key=lambda r: (order.get(r[0], 9), -r[2])):
        budget = f"RM{a['budget']:,.0f}" if a["budget"] else (f"CBO{a['cbo']:,.0f}" if a["cbo"] else "-")
        rb = f"{r90}/{b90}" if r90 or b90 else "-"
        print(f"{a['name'][:42]:42} {budget:>7} {sp14:>6,.0f} {rg14:>4.0f} {_fmt_cpa(cpl14):>6} "
              f"{sp30:>6,.0f} {rg30:>4.0f} {_fmt_cpa(cpl30):>6} {f'{my30}/{my60}/{my90}':>15} "
              f"{_fmt_cpa(cpa60):>6} {rb:>9} {(f'{teen:.0%}' if teen is not None else '-'):>5}  "
              f"{verdict}  ({'; '.join(why)})")
        print(f"{'':42} ↳ {a['camp'][:70]}  adset {a['adset_id']}")

    planned = sum(a["budget"] or 0 for a in active) + sum(
        c for c in {a["camp"]: a["cbo"] for a in active if a["cbo"]}.values())
    cut = sum((a["budget"] or 0) for v, a, *_ in rows if v == "关")
    lower = sum(max((a["budget"] or 0) - 50, 0) for v, a, *_ in rows if v == "调低" and a["budget"])
    print(f"\nplanned RM{planned:,.0f}/day · 关 frees RM{cut:,.0f}/day · 调低到 RM50 frees RM{lower:,.0f}/day"
          f" · after: ~RM{planned - cut - lower:,.0f}/day (CBO campaigns excluded from the cut maths)")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
