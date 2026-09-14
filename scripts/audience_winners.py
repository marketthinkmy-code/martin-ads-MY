"""Read-only: rank AUDIENCES (real targeting) by MY paid sales from the Paid Student List.

An ad-set NAME is not an audience. 240+ differently named ad sets have carried a sale, most of
them named after the video they ran, and the same targeting has been cloned under a dozen
labels. So this joins every non-SG sale to the MY account's ad sets by UTM ad-set name, reads
each matched ad set's real targeting, and pools sales + spend by targeting SIGNATURE — "which
audience actually buys" is answered by the audience, not by the label.

Windows: lifetime / 180d / 90d / 60d sales, lifetime / 90d / 60d spend → CPA per audience.
Aggregates only (no buyer rows). No writes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import _mkey, extract_results, result_action_type
from adbot.settings import load_settings

TOP_N = int(os.environ.get("ADBOT_TOP_N") or 8)
DETAIL_N = int(os.environ.get("ADBOT_DETAIL_N") or 3)
STATUSES_ALL = ["ACTIVE", "PAUSED", "DELETED", "PENDING_REVIEW", "DISAPPROVED", "PREAPPROVED",
                "PENDING_BILLING_INFO", "CAMPAIGN_PAUSED", "ARCHIVED", "ADSET_PAUSED",
                "IN_PROCESS", "WITH_ISSUES"]
UNKNOWN_KEY = "__unknown__"


def market_of(campaign: str) -> str:
    """MY / SG / ? from the sheet's campaign name. '?' = legacy untagged (mostly old MY)."""
    c = (campaign or "").casefold()
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if "[my]" in c or "martin-my" in c:
        return "MY"
    return "?"


def _names(items) -> list:
    out = []
    for it in items or []:
        out.append(str(it.get("name") or it.get("id") or "") if isinstance(it, dict) else str(it))
    return sorted(x for x in out if x)


def signature(t: dict):
    """(stable key, canonical dict) of the parts of a targeting spec that define WHO is reached."""
    t = t or {}
    geo = t.get("geo_locations") or {}
    canon = {
        "countries": sorted(geo.get("countries") or []),
        "geo_extra": {k: len(v) for k, v in geo.items()
                      if k not in ("countries", "location_types") and v},
        "age": f"{t.get('age_min', '?')}-{t.get('age_max', '?')}",
        "genders": sorted(t.get("genders") or []),
        "locales": sorted(t.get("locales") or []),
        "advantage_audience": int((t.get("targeting_automation") or {}).get("advantage_audience") or 0),
        "interests": _names(t.get("interests")),
        "flexible": [],
        "custom_audiences": _names(t.get("custom_audiences")),
        "excluded_custom_audiences": _names(t.get("excluded_custom_audiences")),
        "exclusions": {},
    }
    for spec in t.get("flexible_spec") or []:
        block = {k: _names(v) for k, v in sorted(spec.items()) if v}
        if block:
            canon["flexible"].append(block)
    canon["exclusions"] = {k: _names(v) for k, v in sorted((t.get("exclusions") or {}).items()) if v}
    return json.dumps(canon, ensure_ascii=False, sort_keys=True), canon


def label(c: dict) -> str:
    parts = ["/".join(c["countries"]) or "geo?", c["age"]]
    g = c["genders"]
    parts.append("男" if g == [1] else "女" if g == [2] else "全")
    if c["advantage_audience"]:
        parts.append("Advantage+ 受众")
    tags = []
    for blk in c["flexible"]:
        for v in blk.values():
            tags.extend(v)
    tags.extend(c["interests"])
    if tags:
        parts.append("兴趣: " + ", ".join(tags[:6]) + (f" +{len(tags) - 6}" if len(tags) > 6 else ""))
    if c["custom_audiences"]:
        parts.append("受众: " + ", ".join(c["custom_audiences"][:3]))
    if c["excluded_custom_audiences"]:
        parts.append(f"排除 {len(c['excluded_custom_audiences'])} 个受众")
    if c["exclusions"]:
        parts.append("排除兴趣/行为")
    if c["locales"]:
        parts.append(f"语言 {c['locales']}")
    if not tags and not c["custom_audiences"] and not c["advantage_audience"]:
        parts.insert(3, "Broad")
    return " · ".join(parts)


def list_adsets(g, account_path: str):
    fields = "id,name,campaign_id,campaign{name},created_time,effective_status,targeting"
    for statuses in (STATUSES_ALL, [x for x in STATUSES_ALL if x != "DELETED"]):
        try:
            return g._get_all(f"{account_path}/adsets", {
                "fields": fields, "limit": 100,
                "filtering": json.dumps([{"field": "effective_status", "operator": "IN",
                                          "value": statuses}])})
        except Exception as exc:  # noqa: BLE001 - fall back to a narrower listing
            print(f"[warn] ad-set listing with {len(statuses)} statuses failed: {str(exc)[:160]}")
    return g._get_all(f"{account_path}/adsets", {"fields": fields, "limit": 100})


def spend_map(g, account_path: str, token: str, **window):
    rows = g.account_insights(account_path, level="adset",
                              fields="adset_id,adset_name,campaign_name,spend,actions", **window)
    out = {}
    for r in rows:
        try:
            spend = float(r.get("spend") or 0)
        except (TypeError, ValueError):
            spend = 0.0
        out[r.get("adset_id")] = (spend, extract_results(r.get("actions"), token),
                                  r.get("adset_name", ""), r.get("campaign_name", ""))
    return out


def _cpa(spend: float, n: int) -> str:
    return f"{spend / n:.0f}" if n else ("—" if spend <= 0 else "∞")


def main() -> None:
    s = load_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    cut = {w: today - dt.timedelta(days=w) for w in (60, 90, 180)}
    token = result_action_type(s.meta.conversion_event)
    g = graph_client(s)

    # ── sheet: every sale, SG rows dropped ───────────────────────────────────────
    values = SheetsClient(s.secrets.google_sa_json).read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    sales, _cols, _hdr = cpa.parse_sales(values, s.cpa.price_myr)
    mk = Counter(market_of(x.campaign) for x in sales)
    my_sales = [x for x in sales if market_of(x.campaign) != "SG"]
    print(f"=== AUDIENCE WINNERS · MY · today MYT {today} · tab '{s.cpa.sales_tab}' ===")
    print(f"{len(sales)} sales on sheet · by market {dict(sorted(mk.items()))} · "
          f"non-SG kept: {len(my_sales)} (of which dated {sum(1 for x in my_sales if x.date)})")

    # ── Meta: every ad set in the MY account + spend per window ────────────────
    adsets = list_adsets(g, s.meta.account_path)
    life = spend_map(g, s.meta.account_path, token, date_preset="maximum")
    d90 = spend_map(g, s.meta.account_path, token,
                    time_range={"since": cut[90].isoformat(), "until": today.isoformat()})
    d60 = spend_map(g, s.meta.account_path, token,
                    time_range={"since": cut[60].isoformat(), "until": today.isoformat()})
    print(f"{len(adsets)} ad sets listed · {len(life)} ad sets with lifetime spend rows")

    sig_of, canon_of, meta = {}, {}, {}
    for a in adsets:
        key, canon = signature(a.get("targeting") or {})
        sig_of[a["id"]] = key
        canon_of.setdefault(key, canon)
        meta[a["id"]] = a
    for aid, (sp, _r, an, cn) in life.items():          # spend rows for ad sets we could not list
        if aid not in meta:
            meta[aid] = {"id": aid, "name": an, "campaign": {"name": cn},
                         "effective_status": "UNLISTED", "created_time": ""}
            sig_of[aid] = UNKNOWN_KEY
    canon_of[UNKNOWN_KEY] = None

    by_ck, by_name = defaultdict(list), defaultdict(list)
    for aid, a in meta.items():
        n = cpa.norm(a.get("name", ""))
        by_ck[(_mkey((a.get("campaign") or {}).get("name", "")), n)].append(aid)
        by_name[n].append(aid)

    def life_spend(aid):
        return life.get(aid, (0.0,))[0]

    # ── join sales → ad set → signature ────────────────────────────────────────
    route = Counter()
    unmatched, ambiguous = Counter(), Counter()
    sales_by_sig, sales_by_adset = defaultdict(list), defaultdict(list)
    for x in my_sales:
        ids = by_ck.get((_mkey(x.campaign), x.adset))
        how = "exact"
        if not ids:
            ids, how = by_name.get(x.adset), "name-only"
        if not ids:
            unmatched[x.adset or "(空 ad set)"] += 1
            route["unmatched"] += 1
            continue
        sigs = {sig_of[i] for i in ids}
        best = max(ids, key=life_spend)
        if len(sigs) > 1:
            ambiguous[x.adset] += 1
            how = "ambiguous→top-spend"
        route[how] += 1
        sales_by_sig[sig_of[best]].append(x)
        sales_by_adset[best].append(x)
    print(f"join routes: {dict(route)}")

    def n_in(rows, w):
        return sum(1 for r in rows if r.date and r.date > cut[w])

    # ── per-signature aggregate ────────────────────────────────────────────────
    groups = []
    for key, canon in canon_of.items():
        ids = [aid for aid, k in sig_of.items() if k == key]
        rows = sales_by_sig.get(key, [])
        sp_life = sum(life_spend(i) for i in ids)
        sp90 = sum(d90.get(i, (0.0, 0.0))[0] for i in ids)
        rg90 = sum(d90.get(i, (0.0, 0.0))[1] for i in ids)
        sp60 = sum(d60.get(i, (0.0, 0.0))[0] for i in ids)
        if not rows and sp_life <= 0:
            continue
        active = [i for i in ids if (meta[i].get("effective_status") == "ACTIVE")]
        groups.append({
            "key": key, "label": label(canon) if canon else "（读不到定向：已删除或不在列表）",
            "ids": ids, "active": active, "life": len(rows), "d180": n_in(rows, 180),
            "d90": n_in(rows, 90), "d60": n_in(rows, 60), "sp_life": sp_life, "sp90": sp90,
            "rg90": rg90, "sp60": sp60,
            "last": max((r.date for r in rows if r.date), default=None),
        })
    groups.sort(key=lambda d: (-d["life"], -d["d90"], d["sp_life"]))

    print(f"\n=== TOP {TOP_N} AUDIENCES · by lifetime MY sales (定向签名合并) ===")
    hdr = (f"{'#':>2} {'life':>4} {'180d':>4} {'90d':>4} {'60d':>4} {'spendLife':>9} {'CPAlife':>7} "
           f"{'spend90':>8} {'CPA90':>6} {'CPL90':>6} {'sets':>4} {'on':>3} {'last':>10}  audience")
    print(hdr)
    print("-" * len(hdr))
    for i, d in enumerate(groups[:TOP_N], 1):
        cpl90 = f"{d['sp90'] / d['rg90']:.0f}" if d["rg90"] else "—"
        print(f"{i:>2} {d['life']:>4} {d['d180']:>4} {d['d90']:>4} {d['d60']:>4} {d['sp_life']:>9,.0f} "
              f"{_cpa(d['sp_life'], d['life']):>7} {d['sp90']:>8,.0f} {_cpa(d['sp90'], d['d90']):>6} "
              f"{cpl90:>6} {len(d['ids']):>4} {len(d['active']):>3} {str(d['last'] or '-'):>10}  {d['label'][:90]}")

    recent = sorted(groups, key=lambda d: (-d["d90"], -d["d180"], d["sp90"]))[:TOP_N]
    print(f"\n=== TOP {TOP_N} AUDIENCES · by 90-day MY sales ===")
    for i, d in enumerate(recent, 1):
        if d["d90"] == 0:
            break
        print(f"{i:>2} 90d {d['d90']:>2} · 180d {d['d180']:>2} · life {d['life']:>3} · spend90 RM{d['sp90']:,.0f} "
              f"· CPA90 {_cpa(d['sp90'], d['d90'])} · {d['label'][:80]}")

    # ── detail for the top audiences: which ad sets, which one to clone ───────
    for i, d in enumerate(groups[:DETAIL_N], 1):
        print(f"\n--- #{i} {d['label']}")
        members = sorted(d["ids"], key=lambda a: (-len(sales_by_adset.get(a, [])), -life_spend(a)))
        for aid in members[:12]:
            a = meta[aid]
            rows = sales_by_adset.get(aid, [])
            print(f"   {aid} {a.get('effective_status', '?')[:9]:9} {str(a.get('created_time', ''))[:10]:10} "
                  f"sales life {len(rows):>3} / 90d {n_in(rows, 90):>2} · spend life RM{life_spend(aid):>7,.0f} "
                  f"/ 90d RM{d90.get(aid, (0.0,))[0]:>6,.0f} · {a.get('name', '')[:48]}")
        if len(members) > 12:
            print(f"   … +{len(members) - 12} more ad sets")
        canon = canon_of.get(d["key"])
        if canon:
            print("   targeting: " + json.dumps(canon, ensure_ascii=False, sort_keys=True)[:900])

    # ── what could not be attributed ───────────────────────────────────────────
    if unmatched:
        print(f"\n=== UNMATCHED sheet ad-set names (non-SG sales with no MY ad set of that name) · "
              f"{sum(unmatched.values())} sales ===")
        for name, n in unmatched.most_common(15):
            print(f"   {n:>4}  {name[:70]}")
    if ambiguous:
        print(f"\n=== AMBIGUOUS names (same name, different targeting → credited to top-spend twin) ===")
        for name, n in ambiguous.most_common(10):
            print(f"   {n:>4}  {name[:70]}")


if __name__ == "__main__":
    main()
