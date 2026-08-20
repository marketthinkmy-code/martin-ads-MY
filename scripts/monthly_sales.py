"""Read-only: month-by-month MY paid-sales analysis straight from the Paid Student List.

The workbook is SHARED between the MY and SG ad accounts, so the whole exercise turns on getting
the market of each row right — and the obvious signals are the weak ones:

  · Campaign-name tags ([my]/[sg]) are missing on most historical rows.
  · Matching the ad NAME against the MY account is actively misleading: the same creatives are
    uploaded to BOTH accounts, so an SG sale whose ad name also exists in MY looks MY.

The one signal that identifies the customer's market directly is the phone number: +60 Malaysia,
+65 Singapore. That is the buyer, not a naming convention, so it leads. Resolution order:

  1. PHONE  — whatsapp/phone column dial code            -> MY / SG / other, strongest.
  2. TAG    — [my]/martin-my (or [sg]/martin-sg) appearing in campaign, ad set OR ad name.
  3. MATCH  — untagged, but campaign/ad set name (NOT the ad name — creatives are shared) is a
              real entity in THIS account -> MY.
  4. UNKNOWN— printed in full rather than guessed.

Rows where phone and campaign tag disagree are reported separately: phone wins, but a silent
override would hide a mis-tagged campaign. No writes.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from collections import defaultdict

from adbot import cpa
from adbot.clients.graph import GraphClient
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

MONTHS = [(2026, 6), (2026, 7), (2026, 8)]
PHONE_HEADERS = ("whatsapp", "號碼", "号码", "phone", "电话", "電話")


def phone_market(raw: str) -> str | None:
    """MY / SG / OTHER from a dial code, or None when the cell has no usable number.

    Malaysian mobiles are +60 1x (9-10 national digits); Singapore is +65 with 8 digits. Local
    formats appear too: a bare 01x... is Malaysian, a bare 8/9xxxxxxx is Singaporean.
    """
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return None
    if d.startswith("60"):
        return "MY"
    if d.startswith("65") and len(d) >= 9:
        return "SG"
    if d.startswith("1") and len(d) == 11:
        return "US"
    if d.startswith("0"):                       # local MY form: 012-345 6789
        return "MY"
    if len(d) == 8 and d[0] in "3689":          # local SG form: 9123 4567
        return "SG"
    return "OTHER"


def tagged_market(*names: str) -> str | None:
    """MY / SG when ANY of campaign / ad set / ad says so outright."""
    blob = " ".join(n or "" for n in names)
    if "[sg]" in blob or "martin-sg" in blob:
        return "SG"
    if "[my]" in blob or "martin-my" in blob:
        return "MY"
    return None


def my_name_keys(graph: GraphClient, account: str) -> tuple[set, set]:
    """ad_key sets for campaign and ad set names in the MY account.

    Deliberately NOT ads: creative names are duplicated across the MY and SG accounts, so an ad
    name is evidence of nothing.
    """
    def keys(edge: str) -> set:
        rows = graph._get_all(f"{account}/{edge}", {"fields": "name", "limit": 500})
        return {cpa.ad_key(r.get("name", "")) for r in rows if r.get("name")}
    camps, adsets = keys("campaigns"), keys("adsets")
    print(f"MY account inventory: {len(camps)} campaigns · {len(adsets)} ad sets")
    return camps, adsets


class Row:
    __slots__ = ("date", "campaign", "adset", "ad", "amount", "phone", "market", "how")


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    graph = GraphClient(s.secrets.meta_token, s.secrets.meta_app_secret or "")

    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)

    # Header discovery mirrors cpa.parse_sales, plus the phone column it does not need.
    header_idx, cols = 0, cpa.find_columns(values[0])
    for i, row in enumerate(values[:8]):
        cand = cpa.find_columns(row)
        if cand.get("campaign", -1) >= 0 and cand.get("ad", -1) >= 0:
            header_idx, cols = i, cand
            break
    header = values[header_idx]
    phone_idx = next((i for i, h in enumerate(header)
                      if any(t in (h or "").casefold() for t in PHONE_HEADERS)), -1)

    print(f"=== MONTHLY MY SALES · today MYT={today} ===")
    print(f"sheet '{s.cpa.sales_tab}' · {len(values)} rows · header row {header_idx}")
    print(f"columns: {cols} · phone col: {phone_idx} "
          f"({header[phone_idx] if phone_idx >= 0 else 'NOT FOUND'})")
    if phone_idx < 0:
        raise SystemExit("no phone column — refusing to report a market split without it")

    my_camps, my_adsets = my_name_keys(graph, s.meta.account_path)

    rows: list[Row] = []
    for raw in values[header_idx + 1:]:
        def cell(i: int) -> str:
            return raw[i] if 0 <= i < len(raw) else ""
        campaign = cpa.norm(cell(cols.get("campaign", -1)))
        ad = cpa.norm(cell(cols.get("ad", -1)))
        if not campaign and not ad:
            continue
        r = Row()
        r.date = cpa.parse_date(cell(cols.get("date", -1)))
        r.campaign, r.ad = campaign, ad
        r.adset = cpa.norm(cell(cols.get("adset", -1)))
        r.amount = cpa._money(cell(cols.get("amount", -1)), s.cpa.price_myr)
        r.phone = cell(phone_idx)
        rows.append(r)

    conflicts = []
    how_counts = defaultdict(int)
    for r in rows:
        pm = phone_market(r.phone)
        tm = tagged_market(r.campaign, r.adset, r.ad)
        if pm in ("MY", "SG", "US", "OTHER"):
            r.market, r.how = pm, "phone"
            if tm and tm != pm:
                conflicts.append((r, pm, tm))
        elif tm:
            r.market, r.how = tm, "tag"
        elif cpa.ad_key(r.campaign) in my_camps or (r.adset and cpa.ad_key(r.adset) in my_adsets):
            r.market, r.how = "MY", "match:campaign/adset"
        else:
            r.market, r.how = "UNKNOWN", "-"
        how_counts[f"{r.market}/{r.how}"] += 1

    dated = [r for r in rows if r.date]
    print(f"{len(rows)} sales parsed · {len(dated)} dated · "
          f"undated excluded from monthly figures: {len(rows) - len(dated)}")
    if dated:
        print(f"date range {min(r.date for r in dated)} .. {max(r.date for r in dated)}")
    print("\nmarket resolution over ALL rows:")
    for k, v in sorted(how_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>5}  {k}")
    print(f"\nphone-vs-tag conflicts (phone wins): {len(conflicts)}")
    for r, pm, tm in conflicts[:15]:
        print(f"    phone={pm} but name says {tm}  {r.date}  camp='{r.campaign[:40]}'")

    def table(rs, key_fn, label):
        groups = defaultdict(list)
        for r in rs:
            groups[(key_fn(r) or "").strip() or "(空白)"].append(r)
        print(f"  --- {label} ---")
        for name, g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"    {len(g):>3} 单  RM{sum(x.amount for x in g):>9,.0f}   {name[:66]}")

    for (yr, mo) in MONTHS:
        last = calendar.monthrange(yr, mo)[1]
        rs = [r for r in dated if r.market == "MY" and (r.date.year, r.date.month) == (yr, mo)]
        rev = sum(r.amount for r in rs)
        print(f"\n\n########## {yr}-{mo:02d} · MY · {len(rs)} 单 · RM{rev:,.0f} "
              f"({yr}-{mo:02d}-01..{yr}-{mo:02d}-{last:02d}) ##########")
        if not rs:
            print("  (no MY sales dated in this month)")
            continue
        table(rs, lambda r: r.ad, "转化的广告 (ad)")
        table(rs, lambda r: r.adset, "广告组 (ad set)")
        table(rs, lambda r: r.campaign, "campaign")

    # ── lifetime ranking per creative ────────────────────────────────────────────────────────
    # Ranked on every sale the ad ever made, MY and SG together: the creatives are uploaded to
    # both accounts, so a hook that converts in one market is evidence about the hook itself. The
    # MY column is broken out beside it because a MY campaign still wants MY proof.
    cutoff60 = today - dt.timedelta(days=60)
    by_ad: dict[str, list] = defaultdict(list)
    for r in rows:                      # rows, not `dated` — an undated sale still happened
        by_ad[(r.ad or "").strip() or "(空白)"].append(r)

    def tally(rs):
        my = [x for x in rs if x.market == "MY"]
        d60 = [x for x in rs if x.date and x.date > cutoff60]
        my60 = [x for x in my if x.date and x.date > cutoff60]
        return len(rs), len(my), len(d60), len(my60)

    ranked = sorted(by_ad.items(), key=lambda kv: -len(kv[1]))
    print("\n\n########## TOP ADS BY LIFETIME CONVERSIONS ##########")
    print(f"  {'#':>3} {'life':>5} {'MY':>5} {'60d':>5} {'MY60':>5}   ad")
    for i, (name, rs) in enumerate(ranked[:15], 1):
        life, my, d60, my60 = tally(rs)
        print(f"  {i:>3} {life:>5} {my:>5} {d60:>5} {my60:>5}   {name[:60]}")

    print("\n\n########## 同期其他市场 (已排除) ##########")
    for mkt in ("SG", "US", "OTHER", "UNKNOWN"):
        rs = [r for r in dated if r.market == mkt and (r.date.year, r.date.month) in MONTHS]
        print(f"  {mkt:8} {len(rs):>3} 单")
    unk = [r for r in dated if r.market == "UNKNOWN" and (r.date.year, r.date.month) in MONTHS]
    for r in unk:
        print(f"    {r.date}  phone='{r.phone[:18]}'  ad='{r.ad[:38]}'  camp='{r.campaign[:38]}'")


if __name__ == "__main__":
    main()
