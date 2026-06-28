"""Read-only: which ADS drove real paid sales, joined to live Meta ad spend + leads.

Answers the operator's "what should I scale?" — reads the Paid Student List (RM1997) tab
(which now carries Campaign Name / UTM Ads Set / UTM Ads Name per sale), tallies real paid
sales per ad over rolling windows, then joins each currently-ACTIVE ad's week-to-date spend
+ registrations (CPL) and 60-day spend (CPA). Also surfaces proven winners that are paused
(reactivation candidates) and active ads burning spend with zero attributed sales.

No writes, no pauses. Run via the adbot-paid-by-ad workflow.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import _mkey, cpl_window, extract_results, result_action_type
from adbot.settings import load_settings

WINDOWS = (7, 14, 30, 60)


def _wins(rows, today):
    out = {f"{w}d": 0 for w in WINDOWS}
    out["life"] = len(rows)
    for r in rows:
        if r.date:
            for w in WINDOWS:
                if r.date > today - dt.timedelta(days=w):
                    out[f"{w}d"] += 1
    return out


def _money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _cpl(spend, results):
    if results > 0:
        return spend / results
    return math.inf if spend > 0 else None


def _fmt(v):
    if v is None:
        return "—"
    return "∞" if v == math.inf else f"{v:.0f}"


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)

    sheets = SheetsClient(s.secrets.google_sa_json)

    # ── 0. data-freshness diagnostic across every "Paid"-like tab ────────────
    # Why: the configured tab can hold lifetime attribution yet 0 recent-dated sales (e.g.
    # historical import, or recent rows missing dates / UTM). Surface WHERE the recent
    # attributed sales actually live before trusting any CPA window.
    print(f"=== TAB DIAGNOSTIC · today MYT={today} ===")
    try:
        titles = sheets.tab_titles(s.cpa.spreadsheet_id)
        print(f"Workbook tabs ({len(titles)}): {titles}")
    except Exception as exc:  # noqa: BLE001
        titles = [s.cpa.sales_tab]
        print(f"(could not list tabs: {exc}; falling back to configured tab only)")
    for t in [t for t in titles if "paid" in t.lower()] or [s.cpa.sales_tab]:
        try:
            vals = sheets.read_tab(s.cpa.spreadsheet_id, t)
        except Exception as exc:  # noqa: BLE001
            print(f"  · {t!r}: read failed ({exc})")
            continue
        tsales, tcols, _h = cpa.parse_sales(vals, s.cpa.price_myr)
        tattr = [x for x in tsales if x.ad]
        dated = [x for x in tattr if x.date]
        has_ad_cols = tcols.get("campaign", -1) >= 0 and tcols.get("ad", -1) >= 0
        dmin = min((x.date for x in dated), default=None)
        dmax = max((x.date for x in dated), default=None)
        in60 = sum(1 for x in dated if x.date > today - dt.timedelta(days=60))
        print(f"  · {t!r}: {len(vals)} rows · ad-cols={has_ad_cols} "
              f"(campaign={tcols.get('campaign')},adset={tcols.get('adset')},ad={tcols.get('ad')},date={tcols.get('date')}) "
              f"· {len(tattr)} attributed · {len(dated)} dated · "
              f"range {dmin}..{dmax} · {in60} in last 60d")
        di = tcols.get("date", -1)
        if di >= 0:                      # show RAW date cells so an unparsed format is visible
            raw = [r[di] for r in vals[-8:] if di < len(r) and (r[di] or "").strip()]
            print(f"       raw date samples (bottom rows): {raw}")
        for x in sorted(dated, key=lambda r: r.date, reverse=True)[:5]:
            print(f"       recent {x.date}  ad={x.ad[:34]!r}  camp={x.campaign[:24]!r}")
    print()

    # ── 1. paid sales per ad from the configured tab ─────────────────────────
    values = sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, cols, hdr = cpa.parse_sales(values, s.cpa.price_myr)
    attributed = [x for x in sales if x.ad]

    print(f"=== PAID-SALES-PER-AD REPORT · today MYT={today} ===")
    print(f"Paid tab '{s.cpa.sales_tab}': {len(values)} sheet rows")
    print(f"  header row: {hdr}")
    print(f"  columns matched: date={cols.get('date')} campaign={cols.get('campaign')} "
          f"adset={cols.get('adset')} ad={cols.get('ad')} amount={cols.get('amount')}")
    print(f"  {len(sales)} sales parsed · {len(attributed)} carry an ad name\n")
    if not attributed:
        print("⚠️  No sales carry an ad name — attribution columns may be empty. Stopping.")
        return

    by_ad = defaultdict(list)          # (campaign_key, ad_norm) -> sales  (monitor's join key)
    by_adname = defaultdict(list)      # ad_norm -> sales                  (status-agnostic view)
    for x in attributed:
        by_ad[(_mkey(x.campaign), x.ad)].append(x)
        by_adname[x.ad].append(x)

    # ── 2. Meta ad-level insights: week-to-date (CPL) and 60-day (CPA) ────────
    cpl_preset, cpl_range = cpl_window(s, today)             # 'week_thu' -> since most-recent-Thu
    week_by_ad = {}
    for r in g.account_insights(acct, level="ad",
                                fields="ad_id,ad_name,campaign_name,spend,actions",
                                date_preset=cpl_preset, time_range=cpl_range):
        if r.get("ad_id"):
            week_by_ad[r["ad_id"]] = r
    cutoff60 = today - dt.timedelta(days=60)
    spend60 = {}
    for r in g.account_insights(acct, level="ad", fields="ad_id,spend",
                                time_range={"since": cutoff60.isoformat(), "until": today.isoformat()}):
        if r.get("ad_id"):
            spend60[r["ad_id"]] = _money(r.get("spend"))
    # 30-day spend summed by normalised ad NAME (a creative may have many copies/ad_ids) — used
    # to score paused-but-converting creatives on CPA/ROAS.
    cutoff30 = today - dt.timedelta(days=30)
    spend30_byname = defaultdict(float)
    for r in g.account_insights(acct, level="ad", fields="ad_name,spend",
                                time_range={"since": cutoff30.isoformat(), "until": today.isoformat()}):
        nm = cpa.norm(r.get("ad_name") or "")
        if nm:
            spend30_byname[nm] += _money(r.get("spend"))

    # ── 3. currently-ACTIVE ads (campaign walk; status lives here, not in insights) ──
    active = []                         # (ad_id, ad_name, campaign_name)
    active_adnorms = set()
    for camp in g.list_campaigns(acct):
        if camp.get("effective_status") != "ACTIVE":
            continue
        for ad in g.list_ads_under_campaign(camp["id"]):
            if ad.get("effective_status") != "ACTIVE":
                continue
            active.append((ad["id"], ad.get("name", ad["id"]), camp.get("name", "")))
            active_adnorms.add(cpa.norm(ad.get("name", "")))

    def paid_for(camp_name, ad_name):
        """Sales matched the monitor's way (campaign+ad), with an ad-name-only fallback."""
        k = (_mkey(camp_name), cpa.norm(ad_name))
        if by_ad.get(k):
            return _wins(by_ad[k], today)
        return _wins(by_adname.get(cpa.norm(ad_name), []), today)

    # ── leaderboard: paid sales per ad (all attributed, regardless of status) ─
    print("--- Paid sales per AD (life / 60d / 30d / 14d / 7d) ---")
    board = sorted(by_adname.items(), key=lambda kv: (-len(kv[1]),))
    for ad_norm, rows in board[:30]:
        w = _wins(rows, today)
        camp = rows[-1].campaign or "?"
        live = "● LIVE" if ad_norm in active_adnorms else "  off "
        print(f"  {w['life']:>3} /{w['60d']:>3} /{w['30d']:>3} /{w['14d']:>3} /{w['7d']:>3}  {live}  "
              f"{ad_norm[:44]:44}  [{camp[:26]}]")

    # ── scale table: every ACTIVE ad with spend + CPL + CPA ──────────────────
    print("\n--- ACTIVE ads — scale decisions (wk = since most-recent-Thursday) ---")
    print(f"  {'ad name':40} {'wk$':>6} {'reg':>4} {'CPL':>5}  {'p30':>3} {'p60':>3} "
          f"{'60d$':>6} {'CPA':>5}  flag")
    rows_out = []
    for ad_id, ad_name, camp_name in active:
        wi = week_by_ad.get(ad_id)
        wk_spend = _money(wi.get("spend")) if wi else 0.0
        wk_reg = extract_results(wi.get("actions"), token) if wi else 0.0
        cpl = _cpl(wk_spend, wk_reg)
        paid = paid_for(camp_name, ad_name)
        sp60 = spend60.get(ad_id, 0.0)
        cpa_val = cpa.cpa(sp60, paid["60d"])
        rows_out.append((ad_name, camp_name, wk_spend, wk_reg, cpl, paid, sp60, cpa_val))

    healthy, hard = s.cpa.healthy_max_myr, s.cpa.hard_stop_myr
    cpl_thr = s.kpi.cpl_threshold_myr
    for ad_name, camp_name, wk_spend, wk_reg, cpl, paid, sp60, cpa_val in sorted(
            rows_out, key=lambda r: (-r[5]["60d"], -(r[2] or 0))):
        flag = ""
        if paid["60d"] > 0 and cpa_val is not None and cpa_val != math.inf:
            if cpa_val <= healthy:
                flag = "★ SCALE (profitable)"
            elif cpa_val <= hard:
                flag = "✓ keep (CPA ok)"
            else:
                flag = "✗ CPA over hard-stop"
        elif wk_spend >= s.kpi.cpl_min_spend_myr and wk_reg <= 0:
            flag = "⚠ spend, 0 reg this wk"
        elif cpl is not None and cpl != math.inf and cpl > cpl_thr:
            flag = "△ CPL high, no paid yet"
        print(f"  {ad_name[:40]:40} {wk_spend:6.0f} {wk_reg:4.0f} {_fmt(cpl):>5}  "
              f"{paid['30d']:>3} {paid['60d']:>3} {sp60:6.0f} {_fmt(cpa_val):>5}  {flag}")

    # ── GOOD 30d CPA/ROAS but NOT currently running (the operator's exact question) ─
    # For each creative with NO live copy, sum its 30-day spend across all its ad_ids and its
    # 30-day attributed sales/revenue → CPA (spend/sale) and ROAS (revenue/spend). Sorted by
    # ROAS so the best paused money-makers surface first.
    print("\n--- GOOD CPA/ROAS (30d) but NOT currently running ---")
    print(f"  {'sale30':>6} {'spend30':>8} {'CPA':>7} {'ROAS':>7}  creative  [last campaign]")
    off_rows = []
    for ad_norm, rows in by_adname.items():
        if ad_norm in active_adnorms:
            continue                              # a copy is still live → not "off"
        s30 = [r for r in rows if r.date and r.date > cutoff30]
        if not s30:
            continue                              # no recent sale → not a current winner
        n30 = len(s30)
        rev30 = sum(r.amount for r in s30)
        sp30 = spend30_byname.get(ad_norm, 0.0)
        cpa30 = (sp30 / n30) if n30 else None
        roas30 = (rev30 / sp30) if sp30 > 0 else None
        off_rows.append((ad_norm, rows[-1].campaign or "?", n30, sp30, cpa30, roas30))
    # best ROAS first; creatives with sales but ~no 30d spend (ROAS undefined) listed after.
    off_rows.sort(key=lambda r: (r[5] is None, -(r[5] or 0)))
    if not off_rows:
        print("  (none — every creative with a 30d sale still has a live copy)")
    for ad_norm, camp, n30, sp30, cpa30, roas30 in off_rows:
        cpa_s = "n/a" if cpa30 is None else f"RM{cpa30:.0f}"
        roas_s = "n/a" if roas30 is None else f"{roas30:.1f}x"
        print(f"  {n30:>6} {sp30:8.0f} {cpa_s:>7} {roas_s:>7}  {ad_norm[:40]:40} [{camp[:24]}]")

    # ── account totals ───────────────────────────────────────────────────────
    tot_life = len(attributed)
    tot_60 = sum(1 for x in attributed if x.date and x.date > cutoff60)
    print(f"\nAttributed paid sales: {tot_life} lifetime · {tot_60} in last 60d · "
          f"{len(active)} ads currently ACTIVE")


if __name__ == "__main__":
    main()
