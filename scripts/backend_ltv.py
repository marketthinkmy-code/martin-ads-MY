"""Read-only: does the RM2,591 front end pay for itself, and what does the back end add per buyer?

Joins every front-end buyer (Paid Student List) to the back-end tabs of the same workbook — 10-month
陪跑, the JAN–MAY back-end list, VIP upgrades, 1-on-1 consultations, offline classes — on phone
(last 9 digits) or e-mail, IN MEMORY. Per front-end purchase-month cohort it prints how many buyers
later bought each back-end item and the back-end revenue per front-end buyer, plus refunds. That is
the number that decides how much CPA the front end can carry.

PRIVACY: phone / e-mail cells are read only as join keys; only counts and sums are printed.

Env:
  ADBOT_MARKET   MY (default) / SG / ALL — front-end buyers by phone country
"""
from __future__ import annotations

import datetime as dt
import os
import re
import statistics
from collections import defaultdict
from typing import Dict, List, Optional

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

FRONT_TAB = "Paid Student List (RM1997)"
REFUND_TAB = "Refund"
# tab -> fixed price when the tab carries no amount column
BACK_TABS = {
    "10months 陪跑 (RM18k) ": None,
    "美國新馬 後端付費家長 JAN 2026 - MAY 2026": None,
    "VIP upgrade (RM88)": 88.0,
    "Consultation 1 v 1": None,
    "1-2 AUG 2026 MY 线下课": None,
    "Oct 11-12 2025 线下课": None,
    "Dec 27-28 线下课": None,
    "Dec 27-28 2025 线下课": None,
    "15-16 AUG 2026 SG 線下課": None,
    "SUN 教练长高课": None,
}
PHONE_HINTS = ("whatsapp", "號碼", "号码", "phone", "联络号码", "聯絡號碼")
EMAIL_HINTS = ("email",)
AMOUNT_HINTS = ("purchaseamount", "amount")
DATE_HINTS = ("報名日期", "createddate", "timestamp", "date")


def _first(header: List[str], hints) -> int:
    hk = [cpa._hkey(h) for h in header]
    for n in hints:
        for i, k in enumerate(hk):
            if n in k:
                return i
    return -1


def _all(header: List[str], hints) -> List[int]:
    hk = [cpa._hkey(h) for h in header]
    return [i for i, k in enumerate(hk) if any(n in k for n in hints)]


def _phone(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 8 else ""


def phone_market(raw: str) -> Optional[str]:
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return None
    if d.startswith("60") or d.startswith("0"):
        return "MY"
    if (d.startswith("65") and len(d) >= 9) or (len(d) == 8 and d[0] in "3689"):
        return "SG"
    return "OTHER"


def _amount(s: str) -> float:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", (s or "").replace(" ", ""))
    return float(m.group().replace(",", "")) if m else 0.0


def load(sheets, sid: str, tab: str):
    values = sheets.read_tab(sid, tab)
    hi = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    header = values[hi] if values else []
    cols = {"phone": _all(header, PHONE_HINTS), "email": _all(header, EMAIL_HINTS),
            "amount": _first(header, AMOUNT_HINTS), "date": _first(header, DATE_HINTS)}
    rows = []
    for r in values[hi + 1:]:
        cell = lambda i: (r[i] if 0 <= i < len(r) else "").strip()
        ids = {"p:" + p for p in (_phone(cell(i)) for i in cols["phone"]) if p}
        ids |= {"e:" + e.lower() for e in (cell(i) for i in cols["email"]) if "@" in e}
        if not ids:
            continue
        raw_phone = next((cell(i) for i in cols["phone"] if cell(i)), "")
        rows.append({"ids": ids, "date": cpa.parse_date(cell(cols["date"])),
                     "amount": _amount(cell(cols["amount"])), "market": phone_market(raw_phone)})
    return rows, cols, header


def main() -> None:
    s = load_settings()
    market = (os.environ.get("ADBOT_MARKET") or "MY").upper()
    sid = s.cpa.spreadsheet_id
    sheets = SheetsClient(s.secrets.google_sa_json)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    print(f"===== FRONT-END vs BACK-END · market {market} · {today} MYT =====\n")

    front, fc, fh = load(sheets, sid, FRONT_TAB)
    front = [f for f in front if market == "ALL" or f["market"] == market]
    print(f"front end: {len(front)} buyers with phone/email · amount col {fh[fc['amount']] if fc['amount'] >= 0 else '-'}"
          f" · dated {sum(1 for f in front if f['date'])}")
    amts = [f["amount"] for f in front if f["amount"] > 0]
    if amts:
        print(f"front-end amount: mean RM{statistics.mean(amts):,.0f} · median RM{statistics.median(amts):,.0f}")

    refunds, _rc, _rh = load(sheets, sid, REFUND_TAB)
    refund_ids = set().union(*[r["ids"] for r in refunds]) if refunds else set()

    back: Dict[str, List[dict]] = {}
    for tab, price in BACK_TABS.items():
        try:
            rows, c, h = load(sheets, sid, tab)
        except Exception as exc:  # noqa: BLE001 - a missing tab must not sink the report
            print(f"  back tab {tab!r}: unreadable ({str(exc)[:60]})")
            continue
        if price is not None:
            for r in rows:
                r["amount"] = r["amount"] or price
        back[tab] = rows
        print(f"  back tab {tab.strip()!r}: {len(rows)} rows · amount col "
              f"{h[c['amount']] if c['amount'] >= 0 else ('fixed RM%.0f' % price if price else 'none')}")
    index: Dict[str, List[tuple]] = defaultdict(list)
    for tab, rows in back.items():
        for r in rows:
            for k in r["ids"]:
                index[k].append((tab, id(r), r))

    cohorts = defaultdict(list)
    for f in front:
        cohorts[f["date"].strftime("%Y-%m") if f["date"] else "undated"].append(f)

    def tally(buyers):
        took, rev, lags, refunded = defaultdict(int), defaultdict(float), [], 0
        any_back = 0
        for f in buyers:
            hits = {}
            for k in f["ids"]:
                for tab, rid, r in index.get(k, []):
                    hits[(tab, rid)] = r
            if f["ids"] & refund_ids:
                refunded += 1
            if hits:
                any_back += 1
            for (tab, _rid), r in hits.items():
                took[tab] += 1
                rev[tab] += r["amount"]
                if f["date"] and r["date"] and r["date"] >= f["date"]:
                    lags.append((r["date"] - f["date"]).days)
        return took, rev, lags, refunded, any_back

    print(f"\n--- per front-end cohort (month of front-end purchase) ---")
    print(f"{'cohort':>8} {'front':>5} {'refund':>6} {'any back':>8} {'陪跑':>4} {'back RM':>9} {'back RM/front':>13}")
    for m in sorted(cohorts):
        b = cohorts[m]
        took, rev, _l, refunded, any_back = tally(b)
        p = took.get("10months 陪跑 (RM18k) ", 0) + took.get("美國新馬 後端付費家長 JAN 2026 - MAY 2026", 0)
        tot = sum(rev.values())
        print(f"{m:>8} {len(b):>5} {refunded:>6} {any_back:>8} {p:>4} {tot:>9,.0f} {tot / len(b):>13,.0f}")

    mature = [f for f in front if f["date"] and f["date"] <= today - dt.timedelta(days=90)]
    for label, group in (("ALL front-end buyers", front), ("front-end buyers ≥90 days old", mature)):
        if not group:
            continue
        took, rev, lags, refunded, any_back = tally(group)
        tot = sum(rev.values())
        print(f"\n=== {label}: {len(group)} ===")
        print(f"refunded {refunded} ({refunded / len(group):.1%}) · bought anything back-end {any_back} ({any_back / len(group):.1%})")
        for tab in back:
            if took.get(tab):
                print(f"  {tab.strip():<40} buyers {took[tab]:>4} ({took[tab] / len(group):5.1%}) · RM{rev[tab]:>10,.0f}")
        print(f"back-end revenue per front-end buyer: RM{tot / len(group):,.0f}")
        if lags:
            print(f"days front -> back: median {statistics.median(lags):.0f} · p75 {sorted(lags)[int(len(lags) * .75)]:.0f}")
    print("\nDONE (no writes · aggregates only)")


if __name__ == "__main__":
    main()
