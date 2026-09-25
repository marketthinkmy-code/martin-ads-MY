"""Read-only: creatives that PAID in the last 30 days (MY only) with a qualifying CPA but are OFF.

The operator pauses on CPL while the sheet keeps paying on CPA: a creative can register few
people expensively and still convert the ones it gets. For every creative with >= 1 MY sale in
the last 30 days this pools the spend of ALL its copies in this account (30 and 60 days),
computes CPA30 / CPA60 / CPL30, and reports whether any copy is still running — and if not,
which ad set carried the sale, so it can be re-opened where it converted.

Creatives whose sales cannot be tied to any spend here (older account, no copy in this account)
are listed separately: they cannot be re-opened from this account.
MY-only: sales whose campaign/ad-set UTM carries [MY] / MARTIN-MY. Aggregates only. No writes.
"""
from __future__ import annotations

import datetime as dt
import math
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
    line, hard = s.cpa.max_acceptable_myr, s.cpa.hard_stop_myr
    print(f"===== PAID IN 30d (MY) · CPA per creative · which are OFF · {today} MYT =====")
    print(f"qualifying line: CPA30 ≤ RM{line:.0f} (hard stop RM{hard:.0f}) · CPL line RM{s.kpi.cpl_threshold_myr:.0f}\n")

    # ── sheet: MY sales per creative, 30d / 60d, with the ad set that carried each sale ──
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _c, _h = cpa.parse_sales(values, s.cpa.price_myr)
    my30, my60, adsets_of, untagged30 = defaultdict(int), defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(int)
    names = {}
    for x in sales:
        if not x.ad or not x.date:
            continue
        age = (today - x.date).days
        if age < 0 or age > 60:
            continue
        k = cpa.ad_key(x.ad)
        names.setdefault(k, x.ad)
        mk = market_of(x.campaign, x.adset)
        if mk == "MY":
            my60[k] += 1
            if age <= 30:
                my30[k] += 1
                adsets_of[k][x.adset] += 1
        elif mk == "?" and age <= 30:
            untagged30[k] += 1
    paid30 = [k for k, n in my30.items() if n > 0]
    print(f"{len(paid30)} creatives with ≥1 MY sale in the last 30 days · "
          f"{sum(my30.values())} MY sales · {sum(untagged30.values())} untagged sales ignored\n")

    # ── Meta: spend / regs per ad for 30d and 60d, pooled per creative ─────────
    def window(days):
        since = (today - dt.timedelta(days=days)).isoformat()
        rows = g.account_insights(acct, level="ad",
                                  fields="ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,actions",
                                  time_range={"since": since, "until": today.isoformat()})
        return rows
    ads_by_key = defaultdict(dict)          # key -> ad_id -> info
    pooled = {30: defaultdict(lambda: [0.0, 0.0]), 60: defaultdict(lambda: [0.0, 0.0])}
    for days in (30, 60):
        for r in window(days):
            k = cpa.ad_key(r.get("ad_name") or "")
            if k not in my30:
                continue
            sp, rg = _money(r.get("spend")), extract_results(r.get("actions"), token)
            pooled[days][k][0] += sp
            pooled[days][k][1] += rg
            info = ads_by_key[k].setdefault(r["ad_id"], {"name": r.get("ad_name"), "adset_id": r.get("adset_id"),
                                                          "adset": r.get("adset_name"), "camp": r.get("campaign_name"),
                                                          "sp30": 0.0, "sp60": 0.0})
            info[f"sp{days}"] += sp

    # ── status of every copy (few dozen calls) ─────────────────────────────────
    for k in paid30:
        for ad_id, info in ads_by_key[k].items():
            try:
                o = g.get_object(ad_id, "effective_status,adset{id,name,daily_budget,effective_status},campaign{id,name,effective_status,daily_budget}")
                info["status"] = o.get("effective_status")
                a, c = o.get("adset") or {}, o.get("campaign") or {}
                info["adset_status"], info["camp_status"] = a.get("effective_status"), c.get("effective_status")
                info["budget"] = (int(a["daily_budget"]) / 100 if a.get("daily_budget") else None)
                info["cbo"] = (int(c["daily_budget"]) / 100 if c.get("daily_budget") else None)
            except Exception as exc:  # noqa: BLE001 - one unreadable copy must not sink the report
                info["status"] = f"?({str(exc)[:40]})"

    # ── report ─────────────────────────────────────────────────────────────────
    rows = []
    for k in paid30:
        sp30, rg30 = pooled[30][k]
        sp60, _ = pooled[60][k]
        cpa30 = sp30 / my30[k] if sp30 else math.inf
        cpa60 = sp60 / my60[k] if (sp60 and my60[k]) else math.inf
        cpl30 = sp30 / rg30 if rg30 else math.inf
        copies = ads_by_key[k]
        active = [a for a in copies.values() if a.get("status") == "ACTIVE"]
        rows.append((k, sp30, rg30, cpa30, cpa60, cpl30, copies, active))

    qualified_off, running, over, no_spend = [], [], [], []
    for k, sp30, rg30, cpa30, cpa60, cpl30, copies, active in rows:
        if not copies or sp30 <= 0:
            no_spend.append((k, sp30, copies))
        elif active:
            running.append((k, sp30, rg30, cpa30, cpa60, cpl30, active))
        elif cpa30 <= line:
            qualified_off.append((k, sp30, rg30, cpa30, cpa60, cpl30, copies))
        else:
            over.append((k, sp30, rg30, cpa30, cpa60, cpl30, copies))

    def carried(k):
        return ", ".join(f"{a}×{n}" for a, n in sorted(adsets_of[k].items(), key=lambda t: -t[1])[:3])

    print(f"=== A. CPA30 达标 但现在全部关着 ({len(qualified_off)}) — 建议开回 ===")
    print(f"  {'MY30':>4} {'MY60':>4} {'spend30':>8} {'reg30':>5} {'CPL30':>6} {'CPA30':>6} {'CPA60':>6}  creative")
    for k, sp30, rg30, cpa30, cpa60, cpl30, copies in sorted(qualified_off, key=lambda r: r[3]):
        print(f"  {my30[k]:>4} {my60[k]:>4} {sp30:>8,.0f} {rg30:>5.0f} {_f(cpl30):>6} {_f(cpa30):>6} {_f(cpa60):>6}  {names[k][:60]}")
        print(f"       成交来自 ad set: {carried(k)}")
        for ad_id, a in sorted(copies.items(), key=lambda t: -t[1]["sp30"])[:4]:
            b = f"RM{a['budget']:,.0f}" if a.get("budget") else (f"CBO{a['cbo']:,.0f}" if a.get("cbo") else "-")
            print(f"       copy {ad_id} {a.get('status')} · adset {a.get('adset_id')} {str(a.get('adset'))[:34]!r} {a.get('adset_status')} {b}"
                  f" · camp {str(a.get('camp'))[:40]!r} {a.get('camp_status')} · spend30 RM{a['sp30']:,.0f}")

    print(f"\n=== B. 有近单但 CPA30 超线，现在关着 ({len(over)}) — 不建议开 ===")
    for k, sp30, rg30, cpa30, cpa60, cpl30, copies in sorted(over, key=lambda r: r[3]):
        print(f"  {my30[k]:>4} {my60[k]:>4} {sp30:>8,.0f} {rg30:>5.0f} {_f(cpl30):>6} {_f(cpa30):>6} {_f(cpa60):>6}  {names[k][:60]}"
              f"   ← 来自 {carried(k)}")

    print(f"\n=== C. 有近单、现在在跑 ({len(running)}) ===")
    for k, sp30, rg30, cpa30, cpa60, cpl30, active in sorted(running, key=lambda r: r[3]):
        print(f"  {my30[k]:>4} {my60[k]:>4} {sp30:>8,.0f} {rg30:>5.0f} {_f(cpl30):>6} {_f(cpa30):>6} {_f(cpa60):>6}  {names[k][:60]}"
              f"   ← {len(active)} copy on")

    print(f"\n=== D. 有近单但这个账户 30 天没花费（旧账户素材 / 无法从这里开）({len(no_spend)}) ===")
    for k, sp30, copies in no_spend:
        print(f"  MY30 {my30[k]}  {names[k][:60]}   ← 来自 {carried(k)}")

    # Sales whose UTM carries no market tag cannot be counted as MY, but the operator should see
    # them: an untagged sale on a paused creative may be the one that would have qualified it.
    if untagged30:
        print(f"\n=== E. 30 天内没标市场的成交（Campaign Name 空白，没算进 MY）({sum(untagged30.values())}) ===")
        for k, n in sorted(untagged30.items(), key=lambda t: -t[1]):
            print(f"  {n} 单  {names.get(k, k)[:60]}   ← MY30 已算 {my30.get(k, 0)}")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
