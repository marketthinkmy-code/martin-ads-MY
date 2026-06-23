"""Read-only CPA report: join Meta campaign spend to Paid Student List sales -> CPA.

Proves the sheet<->Meta join (by normalised campaign name) and shows real profitability
per campaign over 30d / 60d / lifetime, plus the two waste signals: sheet sales with no
matching Meta campaign, and Meta campaigns spending with no attributed sales. Read-only —
no pause logic. Run via the adbot-cpa-report workflow.
"""
from __future__ import annotations

import datetime as dt
import math

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.settings import load_settings

WINDOWS = ("30d", "60d", "life")


def _spend_by_campaign(rows) -> dict:
    """norm(campaign name) -> (display name, spend) from account insights rows."""
    out = {}
    for r in rows:
        name = r.get("campaign_name", "") or ""
        try:
            spend = float(r.get("spend", 0) or 0)
        except (TypeError, ValueError):
            spend = 0.0
        out[cpa.norm(name)] = (name, spend)
    return out


def _cpa_str(c) -> str:
    if c is None:
        return "—"
    return "∞" if c == math.inf else f"{c:.0f}"


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT

    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _cols, _hdr = cpa.parse_sales(values, s.cpa.price_myr)
    _by_ad, _by_adset, by_campaign = cpa.attribute(sales, today)

    g = graph_client(s)
    acct = s.meta.account_path
    until = today.isoformat()
    spend = {
        "30d": _spend_by_campaign(g.account_insights(
            acct, level="campaign", fields="campaign_name,spend", date_preset="last_30d")),
        "60d": _spend_by_campaign(g.account_insights(
            acct, level="campaign", fields="campaign_name,spend",
            time_range={"since": (today - dt.timedelta(days=60)).isoformat(), "until": until})),
        "life": _spend_by_campaign(g.account_insights(
            acct, level="campaign", fields="campaign_name,spend", date_preset="maximum")),
    }
    tiers = cpa.CpaTiers(s.cpa.healthy_max_myr, s.cpa.max_acceptable_myr, s.cpa.hard_stop_myr)

    keys = set(by_campaign) | set().union(*(set(spend[w]) for w in WINDOWS))
    rows = []
    for k in keys:
        disp = next((spend[w][k][0] for w in ("life", "60d", "30d") if k in spend[w]), k)
        cell = {}
        for w in WINDOWS:
            sp = spend[w].get(k, (None, 0.0))[1]
            sa = by_campaign.get(k, {}).get(w, 0)
            cell[w] = (sp, sa, cpa.cpa(sp, sa))
        rows.append((disp, cell))

    print(f"CPA report — today MYT={today}  ·  target RM{s.cpa.target_myr:.0f} · "
          f"healthy≤RM{s.cpa.healthy_max_myr:.0f} · monitor≤RM{s.cpa.max_acceptable_myr:.0f} · "
          f"hard-stop>RM{s.cpa.hard_stop_myr:.0f}\n")
    header = f"{'campaign':36} | {'30d  sp/sale/CPA':>20} | {'60d  sp/sale/CPA':>20} | {'life CPA':>9} | verdict"
    print(header)
    print("-" * len(header))

    def fmt(cell) -> str:
        sp, sa, c = cell
        return f"{sp:6.0f}/{sa:>3}/{_cpa_str(c):>5}"

    for disp, cell in sorted(rows, key=lambda r: -(r[1]["60d"][0] or 0)):
        verdict = cpa.cpa_tier(cell["60d"][2], tiers).replace("cpa_", "")
        life_c = cell["life"][2]
        lc = "—" if life_c is None else ("∞" if life_c == math.inf else f"RM{life_c:.0f}")
        print(f"{disp[:36]:36} | {fmt(cell['30d']):>20} | {fmt(cell['60d']):>20} | {lc:>9} | {verdict}")

    tot_sp = sum(v[1] for v in spend["life"].values())
    tot_sa = sum(v.get("life", 0) for v in by_campaign.values())
    blended = cpa.cpa(tot_sp, tot_sa)
    print(f"\nAccount blended (lifetime): RM{tot_sp:,.0f} spend / {tot_sa} paid sales = RM{_cpa_str(blended)}")

    unmatched = sorted(((k, v.get("life", 0)) for k, v in by_campaign.items() if k not in spend["life"]),
                       key=lambda x: -x[1])[:12]
    print("\n⚠️  Sheet sales with NO matching Meta campaign (check UTM naming):")
    for k, n in unmatched:
        print(f"   {n:>3} sales  ·  {k[:54]}")

    waste = sorted(((disp, sp) for k, (disp, sp) in spend["60d"].items()
                    if sp > 0 and by_campaign.get(k, {}).get("60d", 0) == 0),
                   key=lambda x: -x[1])[:12]
    print("\n🔥 Meta campaigns SPENDING (60d) with 0 attributed paid sales:")
    for disp, sp in waste:
        print(f"   RM{sp:>7,.0f}  ·  {disp[:54]}")


if __name__ == "__main__":
    main()
