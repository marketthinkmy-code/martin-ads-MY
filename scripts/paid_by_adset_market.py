"""Read-only: every Paid Student List sale split MY vs SG, and the MY ad sets that carried sales.

Market of a sale: phone dialling code first (60/0 -> MY, 65/8-digit -> SG); when no usable phone,
the UTM campaign tag ([SG]/MARTIN-SG -> SG, [MY]/MARTIN-MY -> MY); otherwise UNKNOWN.
MY sales are grouped by (UTM campaign, UTM ad set) and joined to the MY ad account's ad sets by
campaign + ad-set name (lifetime insights). Matched ad sets show lifetime spend, CPA, status and
a short targeting summary; unmatched ones are listed as UNMATCHED (spend N/A).

PRIVACY: phone cells are read only to tell the market; output is counts, Meta object names, ids.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter, defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.settings import load_settings

PHONE_HINTS = ("whatsapp", "號碼", "号码", "phone")


def market_of(phone: str, camp: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if d:
        if d.startswith("60") or d.startswith("0"):
            return "MY"
        if (d.startswith("65") and len(d) >= 9) or (len(d) == 8 and d[0] in "3689"):
            return "SG"
        return "OTHER"
    c = (camp or "").casefold()
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if "[my]" in c or "martin-my" in c:
        return "MY"
    return "UNKNOWN"


def tsum(t: dict) -> str:
    if not t:
        return "-"
    geo = ",".join((t.get("geo_locations") or {}).get("countries") or []) or "regions"
    parts = [geo, f"{t.get('age_min', '?')}-{t.get('age_max', '?')}"]
    for fs in t.get("flexible_spec") or []:
        for k in ("interests", "behaviors", "family_statuses", "life_events"):
            names = [x.get("name", "") for x in fs.get(k) or []]
            if names:
                parts.append(f"{k}:" + "/".join(names[:4]) + ("…" if len(names) > 4 else ""))
    if t.get("custom_audiences"):
        parts.append("CA:" + "/".join(x.get("name", "")[:30] for x in t["custom_audiences"][:3]))
    adv = (t.get("targeting_automation") or {}).get("advantage_audience")
    if adv:
        parts.append("Advantage+aud")
    return " · ".join(parts)


def main() -> None:
    s = load_settings()
    sheets = SheetsClient(s.secrets.google_sa_json)
    v = sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    hi = next((i for i, r in enumerate(v[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    header, rows = v[hi], v[hi + 1:]
    col = cpa.find_columns(header)
    hk = [cpa._hkey(h) for h in header]
    pc = [i for i, k in enumerate(hk) if any(n in k for n in PHONE_HINTS)]
    cell = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()

    sales = []
    for r in rows:
        if not any(c.strip() for c in r):
            continue
        camp = cell(r, col.get("campaign", -1))
        phone = next((cell(r, i) for i in pc if cell(r, i)), "")
        d = cpa.parse_date(cell(r, col.get("date", -1)))
        if d and d > today:
            d = None
        sales.append({"mk": market_of(phone, camp), "date": d, "camp": camp,
                      "adset": cell(r, col.get("adset", -1)), "ad": cell(r, col.get("ad", -1))})
    # row-level view of the latest sale dates (no personal data: market, dialling-code class, tags)
    rs = os.environ.get("ADBOT_ROWS_SINCE")
    if rs:
        rs_d = dt.date.fromisoformat(rs)
        print(f"===== rows dated on/after {rs_d} (incl. future-dated) =====")
        for i, r in enumerate(rows, start=hi + 2):
            d_raw = cell(r, col.get("date", -1))
            d = cpa.parse_date(d_raw)
            if not d or d < rs_d:
                continue
            camp = cell(r, col.get("campaign", -1))
            phone = next((cell(r, j) for j in pc if cell(r, j)), "")
            dg = re.sub(r"\D", "", phone)
            code = ("none" if not dg else "60/0x" if dg.startswith(("60", "0")) else "65/8-digit"
                    if (dg.startswith("65") and len(dg) >= 9) or (len(dg) == 8 and dg[0] in "3689") else f"+{dg[:2]}… ({len(dg)} digits)")
            print(f"  row {i}  date {d}  market {market_of(phone, camp):<7} phone-class {code:<16} "
                  f"amount '{cell(r, col.get('amount', -1))[:12]}'  campaign '{camp[:50]}'  adset '{cell(r, col.get('adset', -1))[:30]}'  ad '{cell(r, col.get('ad', -1))[:40]}'")
    print(f"===== PAID STUDENT LIST · sales by market · {today} MYT =====")
    print(f"sales rows {len(sales)} · tab '{s.cpa.sales_tab}'")
    by_mk = Counter(x["mk"] for x in sales)
    for k in ("MY", "SG", "OTHER", "UNKNOWN"):
        L = [x for x in sales if x["mk"] == k]
        yr = Counter(x["date"].year for x in L if x["date"])
        w = lambda days: sum(1 for x in L if x["date"] and x["date"] > today - dt.timedelta(days=days))
        print(f"  {k:<8} {by_mk[k]:>5} · 30d {w(30):>3} · 60d {w(60):>3} · 90d {w(90):>3} · 180d {w(180):>4} · "
              f"with UTM ad set {sum(1 for x in L if x['adset']):>4} · by year {dict(sorted(yr.items()))}")
    print("  monthly MY / SG (last 12 months):")
    for i in range(11, -1, -1):
        m = (today.replace(day=1) - dt.timedelta(days=30 * i)).strftime("%Y-%m")
        my_ = sum(1 for x in sales if x["mk"] == "MY" and x["date"] and x["date"].strftime("%Y-%m") == m)
        sg_ = sum(1 for x in sales if x["mk"] == "SG" and x["date"] and x["date"].strftime("%Y-%m") == m)
        print(f"    {m}  MY {my_:>3} · SG {sg_:>3}")

    # ── MY ad sets ───────────────────────────────────────────────────────────
    g = graph_client(s)
    acct = s.meta.account_path
    meta = {}
    for r in g.account_insights(acct, level="adset", date_preset="maximum",
                                fields="adset_id,adset_name,campaign_id,campaign_name,spend"):
        k = (cpa.norm(r.get("campaign_name") or ""), cpa.norm(r.get("adset_name") or ""))
        meta.setdefault(k, []).append(r)
    spend60 = defaultdict(float)
    for r in g.account_insights(acct, level="adset", fields="adset_id,spend",
                                time_range={"since": (today - dt.timedelta(days=60)).isoformat(),
                                            "until": today.isoformat()}):
        spend60[str(r.get("adset_id"))] += float(r.get("spend") or 0)

    grp = defaultdict(list)
    for x in sales:
        if x["mk"] == "MY" and x["adset"]:
            grp[(cpa.norm(x["camp"]), cpa.norm(x["adset"]))].append(x)
    def show(title, items):
        print(f"\n--- {title} ---")
        print("  life 180d  90d  60d  last-sale   life-spend   CPA   60d-spend  #adsets  campaign › ad set")
        for (cn, an), L in items:
            m = meta.get((cn, an)) or []
            w = lambda days: sum(1 for x in L if x["date"] and x["date"] > today - dt.timedelta(days=days))
            last = max((x["date"] for x in L if x["date"]), default=None)
            if m:
                sp = sum(float(r.get("spend") or 0) for r in m)
                s60 = sum(spend60.get(str(r["adset_id"]), 0) for r in m)
                sp_s, cpa_, s60_s, n_s = f"{sp:,.0f}", (f"{sp / len(L):,.0f}" if sp else "N/A"), f"{s60:,.0f}", str(len(m))
            else:
                sp_s, cpa_, s60_s, n_s = "N/A", "N/A", "N/A", "UNMATCHED"
            print(f"  {len(L):>4} {w(180):>4} {w(90):>4} {w(60):>4}  {str(last):<10} {sp_s:>10} {cpa_:>6} {s60_s:>10}  {n_s:<8} "
                  f"{L[0]['camp'][:62]} › {L[0]['adset'][:50]}")
            ads = Counter(x["ad"] for x in L if x["ad"]).most_common(4)
            print("        top ads: " + " · ".join(f"{a_[:40]} ({n})" for a_, n in ads))
            if m:
                try:
                    t = g.get_object(str(m[0]["adset_id"]), "targeting").get("targeting") or {}
                    print(f"        targeting (ad set {m[0]['adset_id']}): {tsum(t)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"        targeting: ? ({str(exc)[:60]})")

    last_of = lambda L: max((y["date"] or dt.date.min) for y in L).toordinal()
    show(f"MY ad sets by LIFETIME sales — {len(grp)} groups · top 12",
         sorted(grp.items(), key=lambda kv: (-len(kv[1]), -last_of(kv[1])))[:12])
    w180 = lambda L: sum(1 for x in L if x["date"] and x["date"] > today - dt.timedelta(days=180))
    show("MY ad sets by sales in the LAST 180 DAYS · top 12",
         sorted([kv for kv in grp.items() if w180(kv[1])], key=lambda kv: (-w180(kv[1]), -len(kv[1])))[:12])
    # same audience label across campaigns: pool by ad-set NAME only (informational)
    by_name = defaultdict(list)
    for x in sales:
        if x["mk"] == "MY" and x["adset"]:
            by_name[cpa.norm(x["adset"])].append(x)
    print("\n--- MY sales pooled by ad-set NAME across all campaigns (label, not verified audience) · top 12 ---")
    for n_, L in sorted(by_name.items(), key=lambda kv: -len(kv[1]))[:12]:
        print(f"  {len(L):>4} life · {w180(L):>3} in 180d · {sum(1 for x in L if x['date'] and x['date'] > today - dt.timedelta(days=60)):>3} in 60d · "
              f"{len({cpa.norm(x['camp']) for x in L}):>3} campaigns · {L[0]['adset'][:70]}")
    um = sum(len(L) for k, L in grp.items() if not meta.get(k))
    print(f"\n  MY sales whose (campaign, ad set) is not found in this ad account: {um} of "
          f"{sum(len(L) for L in grp.values())}")
    print("\nDONE (read-only · aggregates only)")


if __name__ == "__main__":
    main()
