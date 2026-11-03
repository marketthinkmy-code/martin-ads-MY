"""Read-only: Register-tab rows per created date — how many registrations the landing page really
recorded each day, split by whether the row carries an [MY] campaign UTM, an SG one, or none.
Compared with Meta's pixel Complete Registration count it shows whether a high-CPL day is fewer real
sign-ups or the pixel under-counting. Counts only; no personal data is read beyond the date and UTMs.

Env:
  ADBOT_DAYS   look back this many days (default 14)
"""
from __future__ import annotations

import datetime as dt
import os
from collections import Counter, defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def main() -> None:
    s = load_settings()
    days = int(os.environ.get("ADBOT_DAYS") or 14)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    since = today - dt.timedelta(days=days)
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, "Register")
    hi = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    cols = cpa.find_columns(values[hi])
    src_i = next((i for i, h in enumerate(values[hi]) if cpa._hkey(h) == "source"), -1)
    per = defaultdict(Counter)
    for r in values[hi + 1:]:
        cell = lambda i: (r[i] if 0 <= i < len(r) else "").strip()
        d = cpa.parse_date(cell(cols.get("date", -1)))
        if not d or d < since or d > today:
            continue
        camp = cell(cols.get("campaign", -1)).casefold()
        tag = "MY" if ("[my]" in camp or "martin-my" in camp) else "SG" if ("[sg]" in camp or "martin-sg" in camp) else ("other-utm" if camp else "no-utm")
        per[d][tag] += 1
        per[d]["src:" + cell(src_i)[:24]] += 0
    print(f"===== REGISTER rows per created date · {since}..{today} MYT =====")
    print(f"{'date':<11} {'MY':>4} {'SG':>4} {'otherUTM':>8} {'noUTM':>6} {'total':>6}")
    for d in sorted(per):
        c = per[d]
        print(f"{d}  {c['MY']:>4} {c['SG']:>4} {c['other-utm']:>8} {c['no-utm']:>6} {c['MY'] + c['SG'] + c['other-utm'] + c['no-utm']:>6}")
    print("\nDONE (counts only)")


if __name__ == "__main__":
    main()
