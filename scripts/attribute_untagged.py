"""Read-only: credit untagged Paid Student List sales to an ad via the registration list.

A sale row entered without Campaign / UTM tags can never be matched to an ad. The same person
usually registered for the webinar first, and the Register tab carries the UTM tags of that
registration. This joins the two IN MEMORY on normalised phone (last 9 digits) or lower-cased
email and prints, per untagged sale, only the sale date and the matched registration's date +
campaign / ad set / ad (Meta object names).

PRIVACY: phone / email cells are read only to build the join key; no name, phone, email or any
other personal cell is ever printed.

Env:
  ADBOT_DAYS                look back this many days of sales (default 7)
  ADBOT_REGISTER_GID        gid of the Register tab (default 1364330964)
  ADBOT_REGISTER_TAB        tab title fallback (default "Register")
"""
from __future__ import annotations

import datetime as dt
import os
import re
from typing import Dict, List

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

PHONE_HINTS = ("whatsapp", "號碼", "号码", "phone", "电话", "電話", "手机", "手機")
EMAIL_HINTS = ("email", "邮箱", "郵箱", "電郵", "电邮")


def _keys_for(header: List[str]) -> Dict[str, List[int]]:
    hk = [cpa._hkey(h) for h in header]
    return {
        "phone": [i for i, k in enumerate(hk) if any(p in k for p in PHONE_HINTS)],
        "email": [i for i, k in enumerate(hk) if any(p in k for p in EMAIL_HINTS)],
    }


def _phone(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 8 else ""


def _email(s: str) -> str:
    s = (s or "").strip().lower()
    return s if "@" in s else ""


def _load(sheets, sheet_id, tab):
    values = sheets.read_tab(sheet_id, tab)
    hdr_i = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    header = values[hdr_i]
    cols = cpa.find_columns(header)
    return values[hdr_i + 1:], header, cols, _keys_for(header)


def _cell(r, i):
    return (r[i] if 0 <= i < len(r) else "").strip()


def _ids(r, keys) -> set:
    out = set()
    for i in keys["phone"]:
        p = _phone(_cell(r, i))
        if p:
            out.add("p:" + p)
    for i in keys["email"]:
        e = _email(_cell(r, i))
        if e:
            out.add("e:" + e)
    return out


def main() -> None:
    s = load_settings()
    days = int(os.environ.get("ADBOT_DAYS", "7"))
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    since = today - dt.timedelta(days=days)
    sheets = SheetsClient(s.secrets.google_sa_json)

    sid = s.cpa.spreadsheet_id
    gid = os.environ.get("ADBOT_REGISTER_GID", "1364330964").strip()
    reg_tab = os.environ.get("ADBOT_REGISTER_TAB", "Register").strip()
    match = [t for t, g in sheets.tabs(sid) if str(g) == gid]
    if match:
        reg_tab = match[0]

    paid, p_hdr, p_cols, p_keys = _load(sheets, sid, s.cpa.sales_tab)
    regs, r_hdr, r_cols, r_keys = _load(sheets, sid, reg_tab)
    print(f"===== UNTAGGED SALES -> REGISTRATION UTM · since {since} · {today} MYT =====")
    print(f"paid tab {s.cpa.sales_tab!r}: join cols phone={[p_hdr[i] for i in p_keys['phone']]} "
          f"email={[p_hdr[i] for i in p_keys['email']]}")
    print(f"register tab {reg_tab!r}: {len(regs)} rows · join cols phone={[r_hdr[i] for i in r_keys['phone']]} "
          f"email={[r_hdr[i] for i in r_keys['email']]} · utm cols "
          f"{ {k: r_hdr[v] for k, v in r_cols.items() if v >= 0 and k in ('date', 'campaign', 'adset', 'ad')} }")

    index: Dict[str, List[List[str]]] = {}
    for r in regs:
        for k in _ids(r, r_keys):
            index.setdefault(k, []).append(r)

    n = 0
    for r in paid:
        d = cpa.parse_date(_cell(r, p_cols.get("date", -1)))
        if not d or d < since:
            continue
        tagged = cpa.norm(_cell(r, p_cols.get("campaign", -1))) or cpa.norm(_cell(r, p_cols.get("ad", -1)))
        n += 1
        label = "tagged" if tagged else "UNTAGGED"
        print(f"\nsale #{n}  date {d}  ({label})"
              + (f"  sheet ad={_cell(r, p_cols.get('ad', -1))!r}" if tagged else ""))
        hits = []
        for k in _ids(r, p_keys):
            hits.extend(index.get(k, []))
        seen = set()
        for h in hits:
            key = id(h)
            if key in seen:
                continue
            seen.add(key)
            rd = cpa.parse_date(_cell(h, r_cols.get("date", -1)))
            print(f"    registered {rd}  campaign={_cell(h, r_cols.get('campaign', -1))!r}"
                  f"  adset={_cell(h, r_cols.get('adset', -1))!r}  ad={_cell(h, r_cols.get('ad', -1))!r}")
        if not seen:
            print("    no matching registration found (phone/email differ or not in Register tab)")
    if not n:
        print("\nno sales in window")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
