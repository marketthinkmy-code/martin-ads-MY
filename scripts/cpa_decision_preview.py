"""Read-only preview of the combined CPA x CPL decision for every active ad.

Dress rehearsal before the live monitor uses cpa.combined_decision: per active ad it shows
the CPL (last-3d) verdict, the real-sales CPA (lifetime), the ad age, and the COMBINED
outcome — keep / CPA-rescued-from-CPL / CPA hard-stop pause. Writes nothing.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import (MANUAL_HOLD, decide, extract_results,
                               result_action_type)
from adbot.settings import load_settings


def _mkey(name: str) -> str:
    """Campaign match key: drop a leading '(Image)' tag Meta adds, then normalise."""
    s = (name or "").strip()
    if s.lower().startswith("(image)"):
        s = s[len("(image)"):]
    return cpa.norm(s)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    tiers = cpa.CpaTiers(s.cpa.healthy_max_myr, s.cpa.max_acceptable_myr, s.cpa.hard_stop_myr)
    token = result_action_type(s.meta.conversion_event)
    want_event = (s.meta.conversion_event or "").upper()

    # --- sheet sales grouped by (campaign match-key, ad name) -------------------
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _cols, _hdr = cpa.parse_sales(values, s.cpa.price_myr)
    sold = defaultdict(int)
    for sale in sales:
        sold[(_mkey(sale.campaign), sale.ad)] += 1  # lifetime count per (campaign, ad)

    # --- Meta: active ads + lifetime/3d spend (account-level, cheap) ------------
    g = graph_client(s)
    acct = s.meta.account_path
    ads = g._get_all(f"{acct}/ads", {
        "fields": "id,name,created_time,effective_status,campaign{name},adset{name,promoted_object}",
        "limit": 500})
    life = {r.get("ad_id"): _f(r.get("spend")) for r in g.account_insights(
        acct, level="ad", fields="ad_id,spend", date_preset="maximum")}
    d3 = {r.get("ad_id"): r for r in g.account_insights(
        acct, level="ad", fields="ad_id,spend,actions", date_preset=s.kpi.cpl_lookback)}

    rescued, hardstop, cpl_pause_rows, keep = [], [], [], []
    for ad in ads:
        if ad.get("effective_status") != "ACTIVE":
            continue
        adset = ad.get("adset") or {}
        if ((adset.get("promoted_object") or {}).get("custom_event_type") or "").upper() != want_event:
            continue  # not optimised for our event — same scope as the live monitor
        name = ad.get("name", ad["id"])
        camp = (ad.get("campaign") or {}).get("name", "")

        row3 = d3.get(ad["id"])
        spend3, results3 = (_f(row3.get("spend")), extract_results(row3.get("actions"), token)) if row3 else (0.0, 0.0)
        if any(h and h in name for h in s.kpi.cpl_hold):
            cpl_pause, cpl_reason = False, MANUAL_HOLD
        else:
            cpl_pause, cpl_reason, _cpl = decide(spend3, results3, s.kpi)

        spend_life = life.get(ad["id"], 0.0)
        n_sales = sold.get((_mkey(camp), name and cpa.norm(name)), 0)
        cpa_val = cpa.cpa(spend_life, n_sales)
        created = cpa.parse_date((ad.get("created_time") or "")[:10])
        age = (today - created).days if created else None

        should_pause, reason = cpa.combined_decision(
            cpl_pause=cpl_pause, cpl_reason=cpl_reason, cpa_value=cpa_val, cpa_sales=n_sales,
            cpa_spend=spend_life, age_days=age, tiers=tiers,
            conversion_days=s.cpa.conversion_days, min_spend=s.cpa.min_spend_myr)

        cpa_str = "—" if cpa_val is None else ("∞" if cpa_val == math.inf else f"RM{cpa_val:.0f}")
        line = (f"{cpa.norm(camp)[:22]:22} | {name[:30]:30} | age {str(age):>4}d | "
                f"life RM{spend_life:>6.0f}/{n_sales:>2} = {cpa_str:>7} | 3d CPL {'pause' if cpl_pause else 'ok ':>5} | {reason}")
        if reason == cpa.CPL_RESCUED:
            rescued.append(line)
        elif reason == cpa.HARD_STOP:
            hardstop.append(line)
        elif should_pause:
            cpl_pause_rows.append(line)
        else:
            keep.append(line)

    print(f"Combined CPA×CPL decision preview — today MYT={today}  "
          f"(conversion≥{s.cpa.conversion_days}d, min spend RM{s.cpa.min_spend_myr:.0f}, "
          f"hard-stop>RM{s.cpa.hard_stop_myr:.0f})\n")
    print(f"ACTIVE ads judged: {len(rescued)+len(hardstop)+len(cpl_pause_rows)+len(keep)}  ·  "
          f"🟢 keep {len(keep)} · 🛟 CPA-rescued {len(rescued)} · "
          f"✂️ CPA hard-stop {len(hardstop)} · ✂️ CPL-pause {len(cpl_pause_rows)}\n")
    for title, rows in (("🛟 RESCUED by CPA (over-CPL but real profitable sales) — would KEEP", rescued),
                        ("✂️ CPA HARD-STOP (real sales, CPA>hard-stop, matured) — would PAUSE", hardstop),
                        ("✂️ CPL pause stands (no CPA rescue) — would PAUSE", cpl_pause_rows)):
        print(f"\n=== {title} ===")
        for r in rows:
            print("  " + r)


if __name__ == "__main__":
    main()
