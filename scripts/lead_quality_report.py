"""Read-only: WHO registers vs WHO buys — kid age / worry / gender / hook, aggregates only.

Question (operator, 2026-09-11): the room is full and engaged, spend is there, the ads are
proven sellers — yet the people who come do not reply and do not buy. The registration form
asks every registrant the child's age, gender and the parent's biggest worry, and the Paid
Student List carries the same columns for buyers, so the two populations can be compared
directly: which ages / worries / hooks bring registrants that never become buyers.

PRIVACY: this prints NOTHING row-level. Names, emails, phone numbers and any other personal
cell never reach the log — only header names, counts, and answer categories that occur at
least MIN_CAT times (free-text one-offs are dropped). Ad / campaign names are Meta objects,
not people.

Env:
  ADBOT_LQ_REGISTER_SHEET_ID  workbook holding the registrations (default: the CPA sheet)
  ADBOT_LQ_REGISTER_GID       tab gid (#gid=... in the URL) of the registration list
  ADBOT_LQ_REGISTER_TAB       tab title instead of gid (gid wins when both are set)
  ADBOT_LQ_PAID_SHEET_ID / ADBOT_LQ_PAID_TAB   buyers (default: CPA sheet + configured tab)
  ADBOT_LQ_WINDOW_DAYS        lookback for both populations (default 90)
  ADBOT_LQ_EXTRA_SHEET_ID     another workbook to list tabs of (access diagnostic only)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

MIN_CAT = 5          # an answer category must occur this often before it is printed
AGE_BUCKETS = [(0, 4, "0-4"), (5, 8, "5-8"), (9, 12, "9-12"), (13, 14, "13-14"),
               (15, 17, "15-17"), (18, 200, "18+")]
PII_HINTS = ("email", "phone", "whatsapp", "號碼", "号码", "電話", "电话", "姓名", "手机", "手機")
PII_EXACT = ("name", "fullname", "ic", "nric")


def _hk(s: str) -> str:
    return cpa._hkey(s)


def find_extra_columns(header: List[str]) -> Dict[str, int]:
    keys = [_hk(h) for h in header]

    def first(*needles) -> int:
        for n in needles:
            for i, k in enumerate(keys):
                if k == n:
                    return i
        for n in needles:
            for i, k in enumerate(keys):
                if n in k:
                    return i
        return -1

    age_cols = []
    for n in ("几岁", "幾歲", "孩子年龄", "孩子年齡", "kidsage", "childage", "age"):
        for i, k in enumerate(keys):
            if n in k and i not in age_cols:
                age_cols.append(i)
    return {
        "age": age_cols[0] if age_cols else -1,
        "age_cols": age_cols,
        "gender": first("男孩", "gender", "性别", "性別"),
        "worry": first("担心", "擔心", "worry", "concern"),
        "source": first("source", "ways", "来源", "來源"),
        "channel": first("付費管道", "付费管道", "paymentchannel", "channel"),
        "market": first("market", "country", "市场", "市場", "地区", "地區"),
        "ad_fallback": first("utmcontent", "adname", "广告名", "廣告名"),
        "adset_fallback": first("utmsource", "adsetname"),
        "campaign_fallback": first("utmcampaign", "campaign"),
    }


def parse_age(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"\d{1,2}", s)
    if not m:
        return None
    v = int(m.group())
    return v if 0 <= v <= 60 else None


def bucket(age: Optional[int]) -> str:
    if age is None:
        return "unknown"
    for lo, hi, label in AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    return "unknown"


def market_of(campaign: str, source: str, market: str) -> str:
    blob = " ".join([campaign or "", source or "", market or ""]).casefold()
    if "[sg]" in blob or "martin-sg" in blob or "singapore" in blob or re.search(r"\bsg\b", blob):
        return "SG"
    if "[my]" in blob or "martin-my" in blob or "malaysia" in blob or re.search(r"\bmy\b", blob):
        return "MY"
    return "?"


def load_rows(sheets: SheetsClient, sheet_id: str, tab: str, today: dt.date, window: int,
              label: str) -> Tuple[List[dict], dict]:
    values = sheets.read_tab(sheet_id, tab)
    if not values:
        print(f"[{label}] tab {tab!r}: EMPTY")
        return [], {}
    # header = first row that has at least 3 non-empty cells (some tabs carry a title row)
    hdr_i = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    header = values[hdr_i]
    cols = cpa.find_columns(header)
    extra = find_extra_columns(header)
    age_cols = extra.pop("age_cols", [])
    cols.update(extra)
    for k in ("ad", "adset", "campaign"):
        if cols.get(k, -1) < 0 and cols.get(f"{k}_fallback", -1) >= 0:
            cols[k] = cols[f"{k}_fallback"]
    pii_cols = [i for i, h in enumerate(header)
                if any(p in _hk(h) for p in PII_HINTS) or _hk(h) in PII_EXACT]
    print(f"[{label}] tab {tab!r}: {len(values) - hdr_i - 1} data rows")
    print(f"[{label}] header ({len(header)} cols): {header}")
    print(f"[{label}] matched columns: { {k: v for k, v in cols.items() if v >= 0 and not k.endswith('_fallback')} }"
          f"  age candidates {age_cols}")
    print(f"[{label}] PII columns (never read): {[header[i] for i in pii_cols]}")

    def cell(r: List[str], key: str) -> str:
        i = cols.get(key, -1)
        return (r[i] if 0 <= i < len(r) else "").strip()

    rows, undated, old = [], 0, 0
    for r in values[hdr_i + 1:]:
        if not any(c.strip() for c in r):
            continue
        d = cpa.parse_date(cell(r, "date")) if cols.get("date", -1) >= 0 else None
        if d is None:
            undated += 1
            continue
        if d < today - dt.timedelta(days=window) or d > today:
            old += 1
            continue
        camp, adset, ad = cell(r, "campaign"), cell(r, "adset"), cell(r, "ad")
        rows.append({
            "date": d, "campaign": camp, "adset": adset, "ad": ad,
            "ad_key": cpa.ad_key(ad),
            "age": next((a for a in (parse_age(r[i] if i < len(r) else "") for i in age_cols)
                         if a is not None), None),
            "gender": cell(r, "gender")[:20], "worry": " ".join(cell(r, "worry").split())[:60],
            "source": cell(r, "source")[:30], "channel": cell(r, "channel")[:30],
            "market": market_of(camp, cell(r, "source"), cell(r, "market")),
        })
    dmin = min((x["date"] for x in rows), default=None)
    dmax = max((x["date"] for x in rows), default=None)
    print(f"[{label}] in window ({window}d): {len(rows)}  · undated skipped {undated} · outside window {old}"
          f" · dates {dmin}..{dmax}")
    return rows, cols


def dist(rows: List[dict], key, label_fn=lambda v: v) -> Counter:
    c = Counter()
    for r in rows:
        c[label_fn(r[key])] += 1
    return c


def print_compare(title: str, regs: List[dict], buyers: List[dict], key, label_fn=lambda v: v,
                  order: Optional[List[str]] = None, min_cat: int = 1) -> None:
    cr, cb = dist(regs, key, label_fn), dist(buyers, key, label_fn)
    cats = order or [k for k, _ in (cr + cb).most_common()]
    nr, nb = max(len(regs), 1), max(len(buyers), 1)
    print(f"\n--- {title}  (registrants n={len(regs)} · buyers n={len(buyers)}) ---")
    print(f"  {'category':<28} {'regs':>6} {'share':>6}   {'buyers':>6} {'share':>6}   {'regs/buyer':>10}")
    for cat in cats:
        r, b = cr.get(cat, 0), cb.get(cat, 0)
        if r + b < min_cat:
            continue
        rb = f"{r / b:.0f}" if b else ("∞" if r else "-")
        print(f"  {str(cat)[:28]:<28} {r:>6} {r / nr:>6.0%}   {b:>6} {b / nb:>6.0%}   {rb:>10}")


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    window = int(os.environ.get("ADBOT_LQ_WINDOW_DAYS", "90"))
    sheets = SheetsClient(s.secrets.google_sa_json)

    reg_sheet = os.environ.get("ADBOT_LQ_REGISTER_SHEET_ID") or s.cpa.spreadsheet_id
    paid_sheet = os.environ.get("ADBOT_LQ_PAID_SHEET_ID") or s.cpa.spreadsheet_id
    paid_tab = os.environ.get("ADBOT_LQ_PAID_TAB") or s.cpa.sales_tab

    print(f"===== LEAD QUALITY · today MYT={today} · window {window}d =====")
    for sid, label in [(reg_sheet, "register workbook"), (paid_sheet, "paid workbook"),
                       (os.environ.get("ADBOT_LQ_EXTRA_SHEET_ID", ""), "extra workbook")]:
        if not sid:
            continue
        try:
            tabs = sheets.tabs(sid)
            print(f"[{label}] {sid[:12]}…  tabs: {[(t, g) for t, g in tabs]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{label}] {sid[:12]}…  !! cannot open: {str(exc)[:160]}")
            print("        (share it with the service account, then re-run)")

    gid = os.environ.get("ADBOT_LQ_REGISTER_GID", "").strip()
    reg_tab = os.environ.get("ADBOT_LQ_REGISTER_TAB", "").strip() or "Register"
    if gid:
        try:
            match = [t for t, g in sheets.tabs(reg_sheet) if str(g) == gid]
            if match:
                reg_tab = match[0]
                print(f"[register] gid {gid} -> tab {reg_tab!r}")
            else:
                print(f"[register] gid {gid} not in this workbook; using tab {reg_tab!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[register] gid lookup failed: {str(exc)[:120]}; using tab {reg_tab!r}")

    regs, rcols = load_rows(sheets, reg_sheet, reg_tab, today, window, "register")
    buyers, bcols = load_rows(sheets, paid_sheet, paid_tab, today, window, "paid")
    if not regs or not buyers:
        print("\nnothing to compare — check the matched columns above")
        return

    # ── market split ────────────────────────────────────────────────────────
    print_compare("MARKET (from campaign / source)", regs, buyers, "market",
                  order=["MY", "SG", "?"])

    for mk in ("MY", "SG", "ALL"):
        R = [r for r in regs if mk == "ALL" or r["market"] == mk]
        B = [b for b in buyers if mk == "ALL" or b["market"] == mk]
        if not R and not B:
            continue
        print(f"\n================ {mk} ================")
        print_compare("CHILD AGE", R, B, "age", label_fn=bucket,
                      order=[b for _, _, b in AGE_BUCKETS] + ["unknown"])
        print_compare("GENDER", R, B, "gender", min_cat=MIN_CAT)
        print_compare("BIGGEST WORRY (categories seen ≥5×)", R, B, "worry", min_cat=MIN_CAT)
        print_compare("SOURCE / WAYS", R, B, "source", min_cat=MIN_CAT)
        if any(b["channel"] for b in B):
            print_compare("PAYMENT CHANNEL (buyers)", [], B, "channel", min_cat=1)

        # per hook: registrants vs buyers, and how "old" the kids each hook attracts
        print(f"\n--- {mk} · PER AD (top 25 by registrants, {window}d) ---")
        print(f"  {'regs':>5} {'buy':>4} {'regs/buy':>8} {'age15+':>7} {'age≤8':>6}  ad")
        by_ad_r: Dict[str, List[dict]] = defaultdict(list)
        by_ad_b: Dict[str, List[dict]] = defaultdict(list)
        for r in R:
            by_ad_r[r["ad_key"] or "(no utm)"].append(r)
        for b in B:
            by_ad_b[b["ad_key"] or "(no utm)"].append(b)
        rows = sorted(by_ad_r.items(), key=lambda kv: -len(kv[1]))[:25]
        for k, lst in rows:
            nb = len(by_ad_b.get(k, []))
            ages = [x["age"] for x in lst if x["age"] is not None]
            teen = sum(1 for a in ages if a >= 15) / len(ages) if ages else None
            young = sum(1 for a in ages if a <= 8) / len(ages) if ages else None
            name = next((x["ad"] for x in lst if x["ad"]), "(no utm)")
            print(f"  {len(lst):>5} {nb:>4} {(f'{len(lst)/nb:.0f}' if nb else '∞'):>8} "
                  f"{(f'{teen:.0%}' if teen is not None else '-'):>7} "
                  f"{(f'{young:.0%}' if young is not None else '-'):>6}  {name[:48]}")
        # buyers whose ad had no registrant rows in the window (attribution mismatch check)
        orphan = [k for k in by_ad_b if k not in by_ad_r]
        if orphan:
            print(f"  (buyer ads with no registrant rows in window: {len(orphan)} — UTM naming differs between the two tabs?)")

    # ── age x buyer rate, all markets, to answer 'which age converts' ────────
    print("\n--- ALL MARKETS · buyer rate by child age ---")
    cr, cb = dist(regs, "age", bucket), dist(buyers, "age", bucket)
    for _, _, b in AGE_BUCKETS:
        r, bb = cr.get(b, 0), cb.get(b, 0)
        print(f"  {b:<6} regs {r:>5}  buyers {bb:>4}  rate {(bb / r if r else 0):.1%}")
    print("\nDONE (aggregates only — no personal data printed)")


if __name__ == "__main__":
    main()
