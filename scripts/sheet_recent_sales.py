"""Read-only: the Paid Student List by sale date for the last N days — how many rows per date and
which campaign / ad set / ad tags (normalised UTM values) each row carries, plus how many rows in
the window carry NO tag at all (those never match an ad). No names, emails or phones are read or
printed. Answers "the team entered the webinar sales — why does no ad show a buyer?".
"""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def main() -> None:
    s = load_settings()
    days = int(os.environ.get("ADBOT_DAYS", "10"))
    sheets = SheetsClient(s.secrets.google_sa_json)
    values = sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    price = getattr(s.cpa, "default_price", None) or getattr(s.cpa, "price_myr", None) or 1997.0
    sales, cols, header = cpa.parse_sales(values, float(price))
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    since = today - dt.timedelta(days=days)
    print(f"===== PAID STUDENT LIST · rows by date since {since} · {today} MYT =====")
    print("columns used: " + ", ".join(f"{k}=<{header[i]}>" for k, i in cols.items() if 0 <= i < len(header)))
    print(f"tagged rows parsed: {len(sales)} · undated among them: {sum(1 for x in sales if x.date is None)}")

    # Raw pass: rows with a date in the window but no campaign/ad tag are skipped by parse_sales,
    # so count them here — they are exactly the sales no ad can ever be credited with.
    header_idx = next((i for i, row in enumerate(values[:8]) if row == header), 0)
    date_i, camp_i, ad_i = cols.get("date", -1), cols.get("campaign", -1), cols.get("ad", -1)
    untagged = defaultdict(int)
    for row in values[header_idx + 1:]:
        cell = lambda i: row[i] if 0 <= i < len(row) else ""
        d = cpa.parse_date(cell(date_i))
        if d and d >= since and not cpa.norm(cell(camp_i)) and not cpa.norm(cell(ad_i)):
            untagged[d] += 1

    by_date = defaultdict(list)
    for x in sales:
        if x.date and x.date >= since:
            by_date[x.date].append(x)
    for d in sorted(set(by_date) | set(untagged)):
        rows = by_date.get(d, [])
        print(f"\n{d}  tagged {len(rows)} · untagged {untagged.get(d, 0)}")
        for x in rows:
            print(f"    campaign={x.campaign!r:60} adset={x.adset!r:40} ad={x.ad!r}")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
