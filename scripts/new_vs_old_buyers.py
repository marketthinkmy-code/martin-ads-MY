"""Read-only: was each recent buyer a NEW lead from this round's ads, or an OLD lead re-activated?

For every Paid Student List sale in the window, finds the same person in the Register tab (phone last
9 digits / e-mail, joined IN MEMORY) and classifies:
  NEW  first-ever registration within NEW_DAYS before the purchase (this round's lead)
  OLD  first registered earlier — an old lead who came back; the round's ads did not create them
  ?    no registration found
and prints, per sale, the webinar (the Wednesday on/before the sale), market, NEW/OLD, days since the
first registration, how many earlier registrations, and three ad names: the one tagged on the paid row,
the one on the registration just before the purchase, and the one on the very first registration.

PRIVACY: phone / e-mail are join keys only. Ad and campaign names are Meta objects, not people.

Env:
  ADBOT_SINCE      first sale date to include (default 2026-09-01)
  ADBOT_NEW_DAYS   a lead first registered within this many days before buying is NEW (default 14)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter, defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

PHONE_HINTS = ("whatsapp", "號碼", "号码", "phone")
EMAIL_HINTS = ("email",)


def _cols(header, hints):
    hk = [cpa._hkey(h) for h in header]
    return [i for i, k in enumerate(hk) if any(n in k for n in hints)]


def _ids(r, pc, ec):
    out = set()
    for i in pc:
        d = re.sub(r"\D", "", r[i] if i < len(r) else "")
        if len(d) >= 8:
            out.add("p:" + d[-9:])
    for i in ec:
        e = (r[i] if i < len(r) else "").strip().lower()
        if "@" in e:
            out.add("e:" + e)
    return out


def phone_market(r, pc) -> str:
    raw = next((r[i] for i in pc if i < len(r) and r[i].strip()), "")
    d = re.sub(r"\D", "", raw)
    if d.startswith("60") or d.startswith("0"):
        return "MY"
    if (d.startswith("65") and len(d) >= 9) or (len(d) == 8 and d[:1] in "3689"):
        return "SG"
    return "?"


def webinar_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() - 2) % 7)     # Wednesday on/before


def main() -> None:
    s = load_settings()
    since = dt.date.fromisoformat(os.environ.get("ADBOT_SINCE") or "2026-09-01")
    new_days = int(os.environ.get("ADBOT_NEW_DAYS") or 14)
    sheets = SheetsClient(s.secrets.google_sa_json)
    sid = s.cpa.spreadsheet_id

    def load(tab):
        v = sheets.read_tab(sid, tab)
        hi = next((i for i, r in enumerate(v[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
        return v[hi + 1:], v[hi], cpa.find_columns(v[hi])

    regs, rh, rc = load("Register")
    rpc, rec = _cols(rh, PHONE_HINTS), _cols(rh, EMAIL_HINTS)
    index = defaultdict(list)
    for r in regs:
        d = cpa.parse_date(r[rc["date"]] if 0 <= rc.get("date", -1) < len(r) else "")
        if not d:
            continue
        cell = lambda k: (r[rc[k]] if 0 <= rc.get(k, -1) < len(r) else "").strip()
        rec_ = (d, cell("ad"), cell("campaign"))
        for k in _ids(r, rpc, rec):
            index[k].append(rec_)

    paid, ph, pcols = load(s.cpa.sales_tab)
    ppc, pec = _cols(ph, PHONE_HINTS), _cols(ph, EMAIL_HINTS)
    print(f"===== NEW vs OLD LEAD BUYERS · sales since {since} · NEW = first registered ≤{new_days}d before buying =====\n")
    rows = []
    for r in paid:
        cell = lambda k: (r[pcols[k]] if 0 <= pcols.get(k, -1) < len(r) else "").strip()
        d = cpa.parse_date(cell("date"))
        if not d or d < since:
            continue
        hist = sorted({x for k in _ids(r, ppc, pec) for x in index.get(k, [])})
        before = [x for x in hist if x[0] <= d]
        if not before:
            kind, first, cur, n_prior, age = "?", None, None, 0, None
        else:
            first, cur = before[0], before[-1]
            age = (d - first[0]).days
            kind = "NEW" if age <= new_days else "OLD"
            n_prior = len({x[0] for x in before}) - 1
        rows.append((webinar_of(d), d, phone_market(r, ppc), kind, age, n_prior, cell("ad"),
                     cur[1] if cur else "", first[1] if first else "", first[0] if first else None))

    by_w = defaultdict(Counter)
    for w, d, mk, kind, *_ in rows:
        by_w[w][f"{mk}-{kind}"] += 1
    print("--- per webinar (Wednesday on/before the sale) ---")
    for w in sorted(by_w):
        c = by_w[w]
        print(f"{w}  total {sum(c.values()):>2} · MY new {c['MY-NEW']} old {c['MY-OLD']} ? {c['MY-?']}"
              f" · SG new {c['SG-NEW']} old {c['SG-OLD']} ? {c['SG-?']}")
    print("\n--- per sale ---")
    print("webinar     sale        mkt kind  days-since-1st  prior-regs | paid-row ad | ad of reg before buying | ad of first-ever reg (date)")
    for w, d, mk, kind, age, n_prior, paid_ad, cur_ad, first_ad, first_d in sorted(rows):
        print(f"{w}  {d}  {mk:<3} {kind:<4} {('—' if age is None else age):>6}  {n_prior:>6} | {paid_ad or '-'} | "
              f"{cur_ad or '-'} | {first_ad or '-'} ({first_d or '-'})")
    print("\nDONE (no writes · no personal data printed)")


if __name__ == "__main__":
    main()
