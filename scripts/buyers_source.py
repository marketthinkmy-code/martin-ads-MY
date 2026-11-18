"""Read-only: where did recent buyers come from? Paid Student List rows in a date range,
matched to Register rows by e-mail / phone last 9 digits (in memory only), printing the
Register source (H) and UTM campaign / ad set / ad. Never prints a person.

Env: ADBOT_SALE_FROM, ADBOT_SALE_TO (YYYY-MM-DD, inclusive).
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def ids_of(email: str, phone: str) -> set:
    out = set()
    e = (email or "").strip().lower()
    if "@" in e:
        out.add("e:" + e)
    d = re.sub(r"\D", "", phone or "")
    if len(d) >= 8:
        out.add("p:" + d[-9:])
    return out


def main() -> None:
    s = load_settings()
    sh = SheetsClient(s.secrets.google_sa_json)
    d0 = dt.date.fromisoformat(os.environ.get("ADBOT_SALE_FROM", "2026-11-18"))
    d1 = dt.date.fromisoformat(os.environ.get("ADBOT_SALE_TO", "2026-11-19"))
    reg = sh.read_tab(s.cpa.spreadsheet_id, "Register")
    idx = defaultdict(list)
    for ri, r in enumerate(reg[1:], start=2):
        c = lambda i: (r[i] if i < len(r) else "").strip()
        rec = {"row": ri, "date": cpa.parse_date(c(6)), "src": c(7), "camp": c(9), "adset": c(10), "ad": c(11)}
        for k in ids_of(c(2), c(1)):
            idx[k].append(rec)
    v = sh.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    hi = next((i for i, r in enumerate(v[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    hk = [cpa._hkey(h) for h in v[hi]]
    col = cpa.find_columns(v[hi])
    ec = [i for i, k in enumerate(hk) if "email" in k or "邮箱" in k or "電郵" in k]
    pc = [i for i, k in enumerate(hk) if any(n in k for n in ("whatsapp", "號碼", "号码", "phone"))]
    pv = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()
    print(f"===== BUYER SOURCES · paid rows dated {d0}..{d1} (read-only, no personal data) =====")
    for k, r in enumerate(v[hi + 1:]):
        sr = hi + 2 + k
        d = cpa.parse_date(pv(r, col.get("date", -1)))
        if not d or not (d0 <= d <= d1):
            continue
        email = next((pv(r, i) for i in ec if "@" in pv(r, i)), "")
        phone = next((pv(r, i) for i in pc if pv(r, i)), "")
        dg = re.sub(r"\D", "", phone)
        mk = ("MY" if dg.startswith(("60", "0")) else "SG" if (dg.startswith("65") and len(dg) >= 9) or (len(dg) == 8 and dg[0] in "3689")
              else "none" if not dg else "other")
        filled = sum(1 for c in r if c.strip())
        ids = ids_of(email, phone)
        if not ids:
            print(f"  paid row {sr}: date {d} · no e-mail/phone · {filled} filled cells · paid-list UTM campaign "
                  f"'{pv(r, col.get('campaign', -1))[:50]}' → template/blank, not counted")
            continue
        hits = sorted({x['row']: x for i in ids for x in idx.get(i, [])}.values(), key=lambda x: x["row"])
        print(f"  paid row {sr}: date {d} · phone market {mk} · e-mail {'yes' if email else 'no'} · Register matches {len(hits)} · "
              f"paid-list UTM '{pv(r, col.get('campaign', -1))[:40]}' / '{pv(r, col.get('ad', -1))[:40]}'")
        for x in hits[-4:]:
            print(f"      reg row {x['row']} · {x['date']} · H '{x['src'][:40]}' · {x['camp'][:60]} › {x['adset'][:30]} › {x['ad'][:45]}")


if __name__ == "__main__":
    main()
