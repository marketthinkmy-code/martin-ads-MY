"""Read-only: what audiences have we already run, and who actually buys?

Two inputs for picking NEW interest targeting:
  1. every ad-set name that ever carried a sale (the "already tried" list, with sales counts)
  2. the buyer profile the sheet already collects — kid age, kid gender, and the parent's stated
     worry — which is the closest thing to a real persona we have.

No writes. Counts are lifetime and 90-day so a stale audience is visible as stale.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def _col(header, *needles):
    """Index of the first header cell containing any needle (loose, case-insensitive)."""
    for i, h in enumerate(header):
        h_l = (h or "").strip().lower()
        for n in needles:
            if n.lower() in h_l:
                return i
    return -1


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, cols, header = cpa.parse_sales(values, s.cpa.price_myr)
    cut90 = today - dt.timedelta(days=90)

    print(f"=== AUDIENCES ALREADY RUN (by sales) · today {today} ===")
    life = Counter(x.adset for x in sales if x.adset)
    recent = Counter(x.adset for x in sales if x.adset and x.date and x.date > cut90)
    print(f"  {'life':>5} {'90d':>4}  ad set")
    for name, n in life.most_common(40):
        print(f"  {n:>5} {recent.get(name, 0):>4}  {name[:60]}")
    print(f"  ({len(life)} distinct ad sets have ever carried a sale)")

    # ── buyer persona from the sheet's own survey columns ────────────────────
    hdr_i = values.index(header) if header in values else 0
    age_i = _col(header, "孩子几岁", "kids age")
    sex_i = _col(header, "男孩还是女孩")
    worry_i = _col(header, "最担心")
    print(f"\n=== BUYER PROFILE (survey columns: age={age_i} gender={sex_i} worry={worry_i}) ===")

    for label, idx in (("孩子年龄", age_i), ("性别", sex_i), ("最担心什么", worry_i)):
        if idx < 0:
            print(f"\n-- {label}: column not found")
            continue
        c = Counter()
        for row in values[hdr_i + 1:]:
            v = (row[idx] if idx < len(row) else "").strip()
            if v:
                c[v] += 1
        total = sum(c.values())
        print(f"\n-- {label} (n={total})")
        for v, n in c.most_common(10):
            print(f"     {n:>4}  {100*n/total:5.1f}%  {v[:56]}")


if __name__ == "__main__":
    main()
