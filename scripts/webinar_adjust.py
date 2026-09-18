"""Per-webinar budget adjustment, following the operator's budget doc (2026-09-12).

The doc's rule, applied to MY with MY-only buyers, one creative at a time (all copies pooled):
  SCALE   fresh MY buyer in the LATEST webinar cycle and 60-day CPA <= healthy_max  -> +15%
  KEEP    MY buyer inside 60 days, none in the latest cycle                         -> 0
  REDUCE  no MY buyer in the last TWO cycles, but a MY buyer earlier (lifetime)     -> -25%
  OFF     FOUR consecutive cycles with no MY buyer and >= RM600 spent across them   -> pause
  NEW     younger than two cycles (the doc's TEST lines)                             -> 0
"Budget follows the buyer, not the CPL" — CPL is printed for context only.

A cycle is (previous webinar, webinar]; buyers are counted on the webinar date and the two
days after it (post-event sales land a day or two late in the sheet). Webinar dates come in
newest-first via ADBOT_ADJ_WEBINAR_DATES (comma list, YYYY-MM-DD).

Default is PROPOSE ONLY (prints the table). ADBOT_ADJ_APPLY=1 sets the budgets (ad set under
ABO, campaign under CBO) and pauses the OFF ad sets. Nothing else is written.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

SCALE_PCT, REDUCE_PCT = 15.0, 25.0
OFF_CYCLES, OFF_MIN_SPEND = 4, 600.0
MIN_BUDGET_MYR = 5.0
SETTLE_DAYS = 2


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


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    apply = os.environ.get("ADBOT_ADJ_APPLY", "0").strip() in ("1", "true", "yes")
    raw = os.environ.get("ADBOT_ADJ_WEBINAR_DATES", "").strip()
    webinars = [cpa.parse_date(x.strip()) for x in raw.split(",") if x.strip()]
    webinars = [w for w in webinars if w]
    if len(webinars) < 2:
        raise SystemExit("ADBOT_ADJ_WEBINAR_DATES needs at least two webinar dates, newest first")
    webinars.sort(reverse=True)
    cycles = []   # newest first: (label, spend_since, spend_until, buyers_from, buyers_to)
    for i, w in enumerate(webinars[:OFF_CYCLES]):
        prev = webinars[i + 1] if i + 1 < len(webinars) else w - dt.timedelta(days=7)
        cycles.append((w.isoformat(), prev + dt.timedelta(days=1), w, w, w + dt.timedelta(days=SETTLE_DAYS)))
    print(f"===== WEBINAR ADJUST · {today} MYT · {'APPLY' if apply else 'PROPOSE ONLY'} · MY-only buyers =====")
    print("cycles (newest first): " + " | ".join(f"{c[0]} spend {c[1]}..{c[2]}" for c in cycles))

    # ── sales: MY-only, by creative name, per cycle + 60d + lifetime ─────────
    sheets = SheetsClient(s.secrets.google_sa_json)
    sales, _c, _h = cpa.parse_sales(sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab), s.cpa.price_myr)
    buyers_cycle = [defaultdict(int) for _ in cycles]
    buyers60, buyers_life = defaultdict(int), defaultdict(int)
    for x in sales:
        if not x.ad or market_of(x.campaign, x.adset) != "MY":
            continue
        k = cpa.ad_key(x.ad)
        buyers_life[k] += 1
        if not x.date:
            continue
        if (today - x.date).days <= 60:
            buyers60[k] += 1
        for i, (_l, _a, _b, bf, bt) in enumerate(cycles):
            if bf <= x.date <= bt:
                buyers_cycle[i][k] += 1

    # ── spend per creative per cycle (all copies pooled) + 60d ───────────────
    spend_cycle = [defaultdict(float) for _ in cycles]
    regs_cycle = [defaultdict(float) for _ in cycles]
    for i, (_l, a, b, _bf, _bt) in enumerate(cycles):
        for r in g.account_insights(acct, level="ad", fields="ad_name,spend,actions",
                                    time_range={"since": a.isoformat(), "until": b.isoformat()}):
            k = cpa.ad_key(r.get("ad_name") or "")
            spend_cycle[i][k] += _money(r.get("spend"))
            regs_cycle[i][k] += extract_results(r.get("actions"), token)
    spend60 = defaultdict(float)
    since60 = (today - dt.timedelta(days=60)).isoformat()
    for r in g.account_insights(acct, level="ad", fields="ad_name,spend",
                                time_range={"since": since60, "until": today.isoformat()}):
        spend60[cpa.ad_key(r.get("ad_name") or "")] += _money(r.get("spend"))

    # ── active ads with their budgets ────────────────────────────────────────
    adsets = {a["id"]: a for a in g._get_all(f"{acct}/adsets", {
        "fields": "id,name,daily_budget,campaign_id", "effective_status": json.dumps(["ACTIVE"]), "limit": 200})}
    camps = {c["id"]: c for c in g._get_all(f"{acct}/campaigns", {
        "fields": "id,name,daily_budget", "effective_status": json.dumps(["ACTIVE"]), "limit": 200})}
    rows = []
    for cid, camp in camps.items():
        for ad in g.list_ads_under_campaign(cid):
            if ad.get("effective_status") != "ACTIVE":
                continue
            aset = adsets.get(ad.get("adset_id"), {})
            created = cpa.parse_date((ad.get("created_time") or "")[:10])
            k = cpa.ad_key(ad.get("name", ""))
            bc = [buyers_cycle[i].get(k, 0) for i in range(len(cycles))]
            sc = [spend_cycle[i].get(k, 0.0) for i in range(len(cycles))]
            rc = [regs_cycle[i].get(k, 0.0) for i in range(len(cycles))]
            b60, life = buyers60.get(k, 0), buyers_life.get(k, 0)
            cpa60 = (spend60.get(k, 0.0) / b60) if b60 else math.inf
            # "how many webinars has this CREATIVE sat through" — counted on pooled spend, not on
            # this copy's creation date: a rebuilt copy of a two-month-old creative is not new.
            cycles_alive = sum(1 for v in sc if v > 0)

            if cycles_alive < 2:
                verdict, factor = "NEW", 1.0
            elif (len(cycles) >= OFF_CYCLES and all(v == 0 for v in bc[:OFF_CYCLES])
                  and all(v > 0 for v in sc[:OFF_CYCLES]) and sum(sc[:OFF_CYCLES]) >= OFF_MIN_SPEND):
                verdict, factor = "OFF", 0.0
            elif bc[0] >= 1 and cpa60 <= s.cpa.healthy_max_myr:
                verdict, factor = "SCALE", 1 + SCALE_PCT / 100
            elif bc[0] >= 1:
                verdict, factor = "KEEP (fresh buyer, CPA over healthy)", 1.0
            elif b60 >= 1:
                verdict, factor = "KEEP", 1.0
            elif life >= 1 and sum(bc[:2]) == 0:
                verdict, factor = "REDUCE", 1 - REDUCE_PCT / 100
            else:
                verdict, factor = "KEEP (no MY buyer yet, sample not full)", 1.0

            budget_entity, budget_kind = ad.get("adset_id"), "adset"
            cur = _money(aset.get("daily_budget")) / 100 if aset.get("daily_budget") else None
            if cur is None and camp.get("daily_budget"):
                budget_entity, budget_kind, cur = cid, "campaign(CBO)", _money(camp["daily_budget"]) / 100
            new = None
            if cur is not None and factor not in (1.0, 0.0):
                new = max(MIN_BUDGET_MYR, round(cur * factor))
            rows.append(dict(name=ad.get("name", ""), ad_id=ad["id"], adset_id=ad.get("adset_id"),
                             camp=camp.get("name", ""), bc=bc, sc=sc, rc=rc, b60=b60, life=life, cpa60=cpa60,
                             verdict=verdict, cur=cur, new=new, budget_entity=budget_entity, budget_kind=budget_kind))

    order = {"OFF": 0, "REDUCE": 1, "SCALE": 2, "NEW": 4}
    rows.sort(key=lambda r: (order.get(r["verdict"].split()[0], 3), -(r["cur"] or 0)))
    hdr = "buyers " + "/".join(c[0][5:] for c in cycles)
    print(f"\n{'ad':40} {'budget':>7} {hdr:>22} {'spend/cycle':>26} {'b60':>3} {'CPA60':>6}  verdict -> new")
    total_cur = total_new = 0.0
    for r in rows:
        cur_s = f"RM{r['cur']:,.0f}" if r["cur"] is not None else "-"
        new_s = (f"RM{r['new']:,.0f}" if r["new"] is not None else ("pause" if r["verdict"] == "OFF" else "="))
        print(f"{r['name'][:40]:40} {cur_s:>7} {'/'.join(str(v) for v in r['bc']):>22} "
              f"{'/'.join(f'{v:,.0f}' for v in r['sc']):>26} {r['b60']:>3} "
              f"{('∞' if r['cpa60'] == math.inf else format(r['cpa60'], ',.0f')):>6}  {r['verdict']} -> {new_s}")
        print(f"{'':40} ↳ {r['camp'][:60]}  {r['budget_kind']} {r['budget_entity']}")
        if r["cur"] is not None:
            total_cur += r["cur"]
            total_new += 0.0 if r["verdict"] == "OFF" else (r["new"] if r["new"] is not None else r["cur"])
    print(f"\nplanned now RM{total_cur:,.0f}/day -> after RM{total_new:,.0f}/day")

    if not apply:
        print("\nPROPOSE ONLY — nothing written. Re-run with apply=true to execute.")
        return
    done = 0
    for r in rows:
        try:
            if r["verdict"] == "OFF" and r["adset_id"]:
                g.update_status(r["adset_id"], "PAUSED")
                print(f"[PAUSED] adset {r['adset_id']}  {r['name']}")
                done += 1
            elif r["new"] is not None and r["budget_entity"]:
                g.set_daily_budget(r["budget_entity"], int(round(r["new"] * 100)))
                print(f"[BUDGET] {r['budget_kind']} {r['budget_entity']}  RM{r['cur']:,.0f} -> RM{r['new']:,.0f}  {r['name']}")
                done += 1
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the rest
            print(f"[FAILED] {r['name']}: {exc}")
    print(f"done: {done} change(s) applied")


if __name__ == "__main__":
    main()
