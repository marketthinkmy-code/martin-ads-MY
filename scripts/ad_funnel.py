"""Read-only: every MY ad judged down the whole funnel — Ad -> Lead -> Show-up -> Purchase -> Revenue.

Three gates, cheapest signal first (thresholds in config/evaluation.yaml):
  1. CPL                 spend / Meta registrations
  2. Cost per Attendee   spend / registrants of this ad who actually showed up to a webinar
  3. CPA / ROAS          spend / MY paid sales, revenue / spend

Joins, per creative (ad name key, spend pooled over every copy in this account):
  * Meta insights            spend + registrations in the window, and lifetime spend
  * Register tab             registrants by UTM ad name, with phone/email join keys
  * attendance workbook      webinar show-up lists (phone/email) — optional; '—' when unreadable
  * Paid Student List        MY sales + revenue by UTM ad name, in the window and lifetime

A 0-sale ad is only called once it has spent its patience (one hard-stop CPA, or more for a creative
with a proven lifetime MY CPA) — short runs are 样本不足, not failures.

PRIVACY: phone / email cells are read only to build in-memory join keys. Nothing row-level is printed.

Env:
  ADBOT_WINDOW   days of spend / registrations / sales to judge (default 60)
  ADBOT_ONLY_ACTIVE   "1" to list only ads ACTIVE now (default "0": every ad with spend in window)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import REPO_ROOT, load_settings

PHONE_HINTS = ("whatsapp", "號碼", "号码", "phone", "电话", "電話", "手机", "手機", "mobile")
EMAIL_HINTS = ("email", "邮箱", "郵箱", "電郵", "电邮")
DATE_HINTS = ("date", "日期", "time", "时间", "時間", "jointime", "createddate")
LIFETIME_SINCE = "2025-01-01"


def _f(v, d=0) -> str:
    return "—" if v is None else ("∞" if v == float("inf") else f"{v:,.{d}f}")


def _money(x) -> float:
    try:
        return float(str(x).replace(",", "") or 0)
    except ValueError:
        return 0.0


def market_of(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).casefold()
    if "[sg]" in blob or "martin-sg" in blob:
        return "SG"
    if "[my]" in blob or "martin-my" in blob:
        return "MY"
    return "?"


def _phone(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 8 else ""


def _email(s: str) -> str:
    s = (s or "").strip().lower()
    return s if "@" in s else ""


def _cols(header: List[str], hints) -> List[int]:
    hk = [cpa._hkey(h) for h in header]
    return [i for i, k in enumerate(hk) if any(h in k for h in hints)]


def _ids(row: List[str], phone_cols, email_cols) -> set:
    out = set()
    for i in phone_cols:
        p = _phone(row[i] if i < len(row) else "")
        if p:
            out.add("p:" + p)
    for i in email_cols:
        e = _email(row[i] if i < len(row) else "")
        if e:
            out.add("e:" + e)
    return out


def _header(values: List[List[str]]):
    i = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 2), 0)
    return i, (values[i] if values else [])


def load_attendance(sheets: SheetsClient, cfg: dict) -> Optional[Dict[str, List[Optional[dt.date]]]]:
    """join key -> dates attended (None when the tab has no date column). None if unreadable."""
    sid = (cfg or {}).get("sheet_id") or ""
    if not sid:
        print("attendance: no sheet configured — show-up column will read —")
        return None
    try:
        tabs = [t for t, _g in sheets.tabs(sid)]
    except Exception as exc:  # noqa: BLE001 - attendance is optional; the rest of the report still stands
        print(f"attendance: cannot open {sid[:12]}… ({str(exc)[:90]}) — share it with the service account")
        return None
    wanted = cfg.get("tabs") or tabs
    seen: Dict[str, List[Optional[dt.date]]] = defaultdict(list)
    for tab in wanted:
        values = sheets.read_tab(sid, tab)
        hi, header = _header(values)
        pc, ec, dc = _cols(header, PHONE_HINTS), _cols(header, EMAIL_HINTS), _cols(header, DATE_HINTS)
        n = 0
        for r in values[hi + 1:]:
            ids = _ids(r, pc, ec)
            if not ids:
                continue
            d = next((x for x in (cpa.parse_date(r[i] if i < len(r) else "") for i in dc) if x), None)
            for k in ids:
                seen[k].append(d)
            n += 1
        print(f"attendance tab {tab!r}: {n} rows with phone/email "
              f"(phone cols {[header[i] for i in pc]}, email cols {[header[i] for i in ec]}, "
              f"date cols {[header[i] for i in dc]})")
    print(f"attendance: {len(seen)} distinct people\n")
    return seen


def verdict(ev: dict, spend, days, cpl, c_att, n_att, sales, cpa_v, life_cpa) -> str:
    smp, g1, g2, g3 = ev["sample"], ev["cpl"], ev["cost_per_attendee"], ev["cpa"]
    if spend < smp["min_spend_myr"] or (days is not None and days < smp["min_days"]):
        return "样本不足 · 继续"
    if sales:
        if cpa_v <= g3["line_myr"]:
            return "✅ 保留/加码"
        if cpa_v <= g3["stop_myr"]:
            return "观察 (CPA 超线未到硬顶)"
        return "关 (CPA 超硬顶)"
    proven = life_cpa is not None and life_cpa <= g3["line_myr"]
    patience = smp["proven_patience_myr"] if proven else smp["zero_sale_patience_myr"]
    if spend >= patience:
        return f"关 (0 单, 已花 ≥ RM{patience:,.0f})"
    if n_att is not None and n_att >= g2["min_attendees"] and c_att > g2["stop_myr"]:
        return "关 (出席成本过高)"
    if cpl is not None and cpl > g1["stop_myr"]:
        return "关 (CPL 过高)"
    if cpl is None and spend >= smp["min_spend_myr"]:
        return "关 (0 报名)"
    return f"继续 (0 单, 门槛 RM{patience:,.0f}{' · 历史赢家' if proven else ''})"


def main() -> None:
    s = load_settings()
    ev = yaml.safe_load((Path(REPO_ROOT) / "config" / "evaluation.yaml").read_text(encoding="utf-8"))
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    days = int(os.environ.get("ADBOT_WINDOW") or 60)
    only_active = os.environ.get("ADBOT_ONLY_ACTIVE", "0") == "1"
    since = today - dt.timedelta(days=days)
    sheets = SheetsClient(s.secrets.google_sa_json)
    print(f"===== AD FUNNEL · MY · {since}..{today} ({days}d) =====")
    print(f"gates: CPL ≤ RM{ev['cpl']['line_myr']} (关 > {ev['cpl']['stop_myr']}) · "
          f"cost/attendee ≤ RM{ev['cost_per_attendee']['line_myr']} (关 > {ev['cost_per_attendee']['stop_myr']}) · "
          f"CPA ≤ RM{ev['cpa']['line_myr']} (硬顶 {ev['cpa']['stop_myr']}) · ROAS ≥ {ev['cpa']['roas_line']}\n")

    # ── Meta: window + lifetime spend, pooled per creative key ───────────────
    names: Dict[str, str] = {}
    win = defaultdict(lambda: [0.0, 0.0])          # key -> [spend, meta regs]
    first_day: Dict[str, dt.date] = {}
    for r in g.account_insights(acct, level="ad", fields="ad_name,campaign_name,spend,actions,date_start",
                                time_range={"since": since.isoformat(), "until": today.isoformat()}):
        if market_of(r.get("campaign_name")) == "SG":
            continue
        k = cpa.ad_key(r.get("ad_name") or "")
        names.setdefault(k, r.get("ad_name") or k)
        win[k][0] += _money(r.get("spend"))
        win[k][1] += extract_results(r.get("actions"), token)
    life_spend = defaultdict(float)
    for r in g.account_insights(acct, level="ad", fields="ad_name,campaign_name,spend",
                                time_range={"since": LIFETIME_SINCE, "until": today.isoformat()}):
        if market_of(r.get("campaign_name")) == "SG":
            continue
        life_spend[cpa.ad_key(r.get("ad_name") or "")] += _money(r.get("spend"))
    active = set()
    for a in g._get_all(f"{acct}/ads", {"fields": "name,created_time",
                                         "effective_status": '["ACTIVE"]', "limit": 200}):
        k = cpa.ad_key(a.get("name") or "")
        active.add(k)
        try:
            c = dt.date.fromisoformat((a.get("created_time") or "")[:10])
            first_day[k] = min(first_day.get(k, c), c)
        except ValueError:
            pass

    # ── Register tab: registrants per creative, with join keys ───────────────
    reg_values = sheets.read_tab(s.cpa.spreadsheet_id, "Register")
    hi, rh = _header(reg_values)
    rc = cpa.find_columns(rh)
    pc, ec = _cols(rh, PHONE_HINTS), _cols(rh, EMAIL_HINTS)
    regs = defaultdict(list)                        # key -> [(date, ids)]
    for r in reg_values[hi + 1:]:
        cell = lambda key: (r[rc[key]] if 0 <= rc.get(key, -1) < len(r) else "")
        d = cpa.parse_date(cell("date"))
        if not d or d < since or market_of(cell("campaign")) != "MY":
            continue
        regs[cpa.ad_key(cell("ad"))].append((d, _ids(r, pc, ec)))

    att = load_attendance(sheets, ev.get("attendance") or {})

    # ── Paid list: MY sales + revenue, window and lifetime ───────────────────
    paid, _c, _h = cpa.parse_sales(sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab), s.cpa.price_myr)
    w_sales, w_rev, l_sales = defaultdict(int), defaultdict(float), defaultdict(int)
    for x in paid:
        if market_of(x.campaign, x.adset) != "MY" or not x.ad:
            continue
        k = cpa.ad_key(x.ad)
        l_sales[k] += 1
        if x.date and x.date >= since:
            w_sales[k] += 1
            w_rev[k] += x.amount

    keys = [k for k in set(win) | set(w_sales) if (k in active or not only_active)]
    keys = [k for k in keys if win[k][0] > 0 or w_sales[k]]
    rows, tot = [], defaultdict(float)
    for k in keys:
        spend, leads = win[k]
        rr = regs.get(k, [])
        n_att = None
        if att is not None:
            n_att = sum(1 for d, ids in rr
                        if any(any(ad is None or ad >= d for ad in att.get(i, [])) for i in ids))
        sales, rev = w_sales[k], w_rev[k]
        cpl = spend / leads if leads else None
        c_att = (spend / n_att if n_att else float("inf")) if n_att is not None else None
        cpa_v = spend / sales if sales else float("inf")
        roas = rev / spend if spend else None
        life_cpa = life_spend[k] / l_sales[k] if l_sales[k] and life_spend[k] else None
        age = (today - first_day[k]).days if k in first_day else None
        v = verdict(ev, spend, age, cpl, c_att, n_att, sales, cpa_v, life_cpa)
        rows.append((k in active, spend, k, leads, cpl, len(rr), n_att, c_att, sales, rev, cpa_v, roas,
                     l_sales[k], life_cpa, v))
        for name, val in (("spend", spend), ("leads", leads), ("regs", len(rr)), ("att", n_att or 0),
                          ("sales", sales), ("rev", rev)):
            tot[name] += val
    rows.sort(key=lambda t: (not t[0], -t[1]))

    print(f"{'on':2} {'spend':>7} {'lead':>4} {'CPL':>5} {'reg':>4} {'到场':>4} {'到场%':>5} {'C/Att':>6} "
          f"{'单':>3} {'营业额':>7} {'CPA':>6} {'ROAS':>5} {'life单':>6} {'lifeCPA':>7}  判定  ·  广告")
    for on, spend, k, leads, cpl, nreg, n_att, c_att, sales, rev, cpa_v, roas, ls, lcpa, v in rows:
        rate = f"{n_att / nreg:.0%}" if n_att is not None and nreg else "—"
        print(f"{'●' if on else ' ':2} {spend:>7,.0f} {leads:>4.0f} {_f(cpl):>5} {nreg:>4} {_f(n_att):>4} {rate:>5} "
              f"{_f(c_att):>6} {sales:>3} {rev:>7,.0f} {_f(cpa_v):>6} {_f(roas, 2):>5} {ls:>6} {_f(lcpa):>7}  "
              f"{v}  ·  {names.get(k, k)}")
    sp = tot["spend"]
    print(f"\nTOTAL  spend RM{sp:,.0f} · leads {tot['leads']:.0f} (CPL {_f(sp / tot['leads'] if tot['leads'] else None)}) · "
          f"registrants {tot['regs']:.0f} · show-ups {_f(tot['att']) if att is not None else '—'} · "
          f"MY sales {tot['sales']:.0f} · revenue RM{tot['rev']:,.0f} · "
          f"CPA {_f(sp / tot['sales'] if tot['sales'] else None)} · ROAS {_f(tot['rev'] / sp if sp else None, 2)}")
    print("\nDONE (no writes · aggregates only)")


if __name__ == "__main__":
    main()
