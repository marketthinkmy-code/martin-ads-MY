"""Read-only: show recent paid sales mapped to campaign / ad set / ad name.

So the operator can confirm the ads that converted are still running and the CPL/CPA monitor
won't mis-pause them. Reads the Paid Student List tab via the service account. No writes.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def main() -> None:
    s = load_settings()
    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, cols, header = cpa.parse_sales(values, s.cpa.price_myr)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT

    print("today (MYT):", today, "| rows parsed:", len(sales))
    print("matched columns:", cols)
    print("RAW HEADER:", header)

    for d in (1, 2, 3, 7):
        n = sum(1 for x in sales if x.date and x.date > today - dt.timedelta(days=d))
        print(f"  last {d}d: {n} sales")

    for label, day in (("TODAY", today), ("YESTERDAY", today - dt.timedelta(days=1))):
        rows = [x for x in sales if x.date == day]
        print(f"\n===== {label} {day} — {len(rows)} sales =====")
        grp = Counter((x.campaign, x.adset, x.ad) for x in rows)
        for (camp, adset, ad), n in grp.most_common():
            print(f"  x{n}  ad='{ad}'  |  adset='{adset}'  |  camp='{camp}'")

    print("\n===== LAST 20 DATED SALES =====")
    for x in sorted((x for x in sales if x.date), key=lambda x: x.date, reverse=True)[:20]:
        print(f"  {x.date}  ad='{x.ad}'  |  adset='{x.adset}'  |  camp='{x.campaign}'")

    # Raw tail so any payment/deal-date column (not just 報名日期) is visible.
    print("\n===== LAST 12 RAW ROWS (first 22 cols) =====")
    for row in values[-12:]:
        print(row[:22])


if __name__ == "__main__":
    main()
