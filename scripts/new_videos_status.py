"""Read-only: how the Sept 15-video rebuild is doing — per creative, per audience, per ad set.

The three 1-15-15 campaigns (one per audience) carry the same 15 creatives. This pools spend and
registrations since launch per creative and per audience, joins MY sales from the Paid Student
List, and lists every ad set with its status and budget — so the operator sees which of the 15
videos earned their keep, which never got budget, and what is on right now. No writes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import defaultdict
from pathlib import Path

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import REPO_ROOT, load_settings

SPECS = ("sept15x15_fnr", "sept15x15_parents_engaged", "sept15x15_parents_kids", "sept5_test_fnr")
SINCE = os.environ.get("ADBOT_SINCE") or "2026-09-15"


def _money(x) -> float:
    try:
        return float(str(x).replace(",", "") or 0)
    except ValueError:
        return 0.0


def _f(v) -> str:
    return "∞" if v is None or v == math.inf else f"{v:,.0f}"


def market_of(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).casefold()
    if "[sg]" in blob or "martin-sg" in blob:
        return "SG"
    if "[my]" in blob or "martin-my" in blob:
        return "MY"
    return "?"


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    print(f"===== SEPT 15-VIDEO REBUILD · status since {SINCE} · {today} MYT =====\n")

    camps = {}
    for name in SPECS:
        spec = json.loads((REPO_ROOT / "scripts/clone_specs" / f"{name}.json").read_text(encoding="utf-8"))
        camps[spec["_built"]["campaign_id"]] = spec.get("adset_name") or spec["adsets"][0]["name"]

    # ads + ad sets under the three campaigns
    ads, adsets = {}, {}
    for cid, audience in camps.items():
        for a in g._get_all(f"{cid}/adsets", {"fields": "id,name,daily_budget,effective_status", "limit": 100}):
            adsets[a["id"]] = a
        for ad in g.list_ads_under_campaign(cid):
            ads[ad["id"]] = {"name": ad.get("name", ""), "key": cpa.ad_key(ad.get("name", "")),
                             "status": ad.get("effective_status"), "adset_id": ad.get("adset_id"),
                             "audience": audience, "spend": 0.0, "regs": 0.0}
    print(f"{len(camps)} campaigns · {len(adsets)} ad sets · {len(ads)} ads")

    for r in g.account_insights(s.meta.account_path, level="ad", fields="ad_id,spend,actions",
                                time_range={"since": SINCE, "until": today.isoformat()}):
        if r.get("ad_id") in ads:
            ads[r["ad_id"]]["spend"] += _money(r.get("spend"))
            ads[r["ad_id"]]["regs"] += extract_results(r.get("actions"), token)

    # MY sales per creative (30d) from the sheet, for completeness
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _c, _h = cpa.parse_sales(values, s.cpa.price_myr)
    keys = {a["key"] for a in ads.values()}
    my_sales, untagged = defaultdict(int), defaultdict(int)
    for x in sales:
        if x.ad and x.date and 0 <= (today - x.date).days <= 30 and cpa.ad_key(x.ad) in keys:
            (my_sales if market_of(x.campaign, x.adset) == "MY" else untagged)[cpa.ad_key(x.ad)] += 1

    total_sp = sum(a["spend"] for a in ads.values())
    total_rg = sum(a["regs"] for a in ads.values())
    on = [a for a in ads.values() if a["status"] == "ACTIVE"]
    print(f"spend RM{total_sp:,.0f} · {total_rg:.0f} registrations · CPL {_f(total_sp / total_rg if total_rg else math.inf)} · "
          f"MY sales {sum(my_sales.values())} (+{sum(untagged.values())} untagged) · {len(on)} of {len(ads)} ads ACTIVE now\n")

    # per creative
    per = defaultdict(lambda: {"spend": 0.0, "regs": 0.0, "on": 0, "copies": 0, "name": ""})
    for a in ads.values():
        p = per[a["key"]]
        p["spend"] += a["spend"]; p["regs"] += a["regs"]; p["copies"] += 1
        p["on"] += a["status"] == "ACTIVE"; p["name"] = p["name"] or a["name"]
    print(f"=== 15 creatives · pooled over the 3 audiences ===")
    print(f"  {'spend':>7} {'reg':>4} {'CPL':>6} {'MYsale':>6} {'on/3':>5}  creative")
    for k, p in sorted(per.items(), key=lambda kv: (kv[1]["regs"] == 0, kv[1]["spend"] / kv[1]["regs"] if kv[1]["regs"] else 0, -kv[1]["spend"])):
        cpl = p["spend"] / p["regs"] if p["regs"] else math.inf
        print(f"  {p['spend']:>7,.0f} {p['regs']:>4.0f} {_f(cpl):>6} {my_sales.get(k, 0):>6} {p['on']:>2}/{p['copies']:<2}  {p['name'][:50]}")

    # per audience
    print(f"\n=== 3 audiences · pooled over the 15 creatives ===")
    aud = defaultdict(lambda: {"spend": 0.0, "regs": 0.0, "on": 0})
    for a in ads.values():
        aud[a["audience"]]["spend"] += a["spend"]; aud[a["audience"]]["regs"] += a["regs"]
        aud[a["audience"]]["on"] += a["status"] == "ACTIVE"
    for name, v in sorted(aud.items(), key=lambda kv: -kv[1]["spend"]):
        cpl = v["spend"] / v["regs"] if v["regs"] else math.inf
        print(f"  RM{v['spend']:>7,.0f} · {v['regs']:>3.0f} reg · CPL {_f(cpl):>5} · {v['on']} ads on  {name}")

    # every ad set
    print(f"\n=== all {len(ads)} ad sets (sorted by spend) ===")
    print(f"  {'spend':>7} {'reg':>4} {'CPL':>6} {'budget':>7} {'status':10} audience · creative")
    for ad in sorted(ads.values(), key=lambda a: -a["spend"]):
        aset = adsets.get(ad["adset_id"], {})
        b = f"RM{int(aset['daily_budget']) / 100:,.0f}" if aset.get("daily_budget") else "-"
        cpl = ad["spend"] / ad["regs"] if ad["regs"] else math.inf
        st = ad["status"] if aset.get("effective_status") == "ACTIVE" or ad["status"] != "ACTIVE" else f"{ad['status']}/set {aset.get('effective_status')}"
        print(f"  {ad['spend']:>7,.0f} {ad['regs']:>4.0f} {_f(cpl):>6} {b:>7} {str(st)[:10]:10} {ad['audience'][:22]} · {ad['name'][:40]}")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
