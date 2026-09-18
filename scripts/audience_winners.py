"""Read-only: rank AUDIENCES (real targeting) by MY paid sales from the Paid Student List.

An ad-set NAME is not an audience. 240+ differently named ad sets have carried a sale, most of
them named after the video they ran, and the same targeting has been cloned under a dozen
labels. So this joins every non-SG sale to the MY account's ad sets by UTM ad-set name, reads
each matched ad set's real targeting, and pools sales + spend by targeting SIGNATURE — "which
audience actually buys" is answered by the audience, not by the label.

Grouping is COARSE on purpose: geo country / age / gender / interests + behaviors + family
statuses / custom audiences / Advantage+ flag define WHO is reached. Exclusion lists, locale
lists and a regions-instead-of-country split are hygiene variants of the same audience and are
counted as variants, not separate audiences.

Windows: lifetime / 180d / 90d / 60d sales, lifetime / 90d / 60d spend → CPA per audience.
Sales whose ad-set name exists nowhere in this account (older ad account, deleted, renamed) are
listed as unmatched with their year split, so the reader sees how much history the ranking
actually rests on. Aggregates only (no buyer rows). No writes.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter, defaultdict

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.monitor_cpl import _mkey, extract_results, result_action_type
from adbot.settings import load_settings

TOP_N = int(os.environ.get("ADBOT_TOP_N") or 12)
DETAIL_N = int(os.environ.get("ADBOT_DETAIL_N") or 6)
STATUSES_ALL = ["ACTIVE", "PAUSED", "DELETED", "PENDING_REVIEW", "DISAPPROVED", "PREAPPROVED",
                "PENDING_BILLING_INFO", "CAMPAIGN_PAUSED", "ARCHIVED", "ADSET_PAUSED",
                "IN_PROCESS", "WITH_ISSUES"]
UNKNOWN_KEY = "__unknown__"
CORE = ("countries", "age", "genders", "advantage_audience", "interests", "flexible",
        "custom_audiences", "exclusions")


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
        out.append(str(it.get("name") or it.get("id") or "").strip() if isinstance(it, dict) else str(it))
    return sorted(x for x in out if x)


def canonical(t: dict) -> dict:
    """The parts of a targeting spec that say WHO is reached, normalised for comparison."""
    t = t or {}
    geo = t.get("geo_locations") or {}
    countries = sorted(geo.get("countries") or [])
    geo_extra = {k: len(v) for k, v in geo.items() if k not in ("countries", "location_types") and v}
    if not countries and geo_extra:
        countries = ["MY"]            # this is the MY account: regions/cities here are MY regions
    canon = {
        "countries": countries,
        "geo_extra": geo_extra,
        "age": f"{t.get('age_min', '?')}-{t.get('age_max', '?')}",
        "genders": sorted(t.get("genders") or []),
        "locales": sorted(t.get("locales") or []),
        "advantage_audience": int((t.get("targeting_automation") or {}).get("advantage_audience") or 0),
        "interests": _names(t.get("interests")),
        "flexible": [],
        "custom_audiences": _names(t.get("custom_audiences")),
        "excluded_custom_audiences": _names(t.get("excluded_custom_audiences")),
        "exclusions": {k: _names(v) for k, v in sorted((t.get("exclusions") or {}).items()) if v},
    }
    for spec in t.get("flexible_spec") or []:
        block = {k: _names(v) for k, v in sorted(spec.items()) if v}
        if block:
            canon["flexible"].append(block)
    return canon


def coarse_key(c: dict) -> str:
    return json.dumps({k: c[k] for k in CORE}, ensure_ascii=False, sort_keys=True)


def full_key(c: dict) -> str:
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def label(c: dict) -> str:
    parts = ["/".join(c["countries"]) or "geo?", c["age"]]
    g = c["genders"]
    parts.append("男" if g == [1] else "女" if g == [2] else "全")
    if c["advantage_audience"]:
        parts.append("A+受众")
    tags = []
    for blk in c["flexible"]:
        for v in blk.values():
            tags.extend(v)
    tags.extend(c["interests"])
    if tags:
        parts.append("兴趣/行为: " + ", ".join(tags))
    if c["custom_audiences"]:
        parts.append("受众: " + ", ".join(c["custom_audiences"]))
    if c["exclusions"]:
        parts.append("排除兴趣/行为")
    if not tags and not c["custom_audiences"]:
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
    return f"{spend / n:,.0f}" if n else ("—" if spend <= 0 else "∞")


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
          f"non-SG kept: {len(my_sales)} (dated {sum(1 for x in my_sales if x.date)})")

    # ── Meta: every ad set in the MY account + spend per window ────────────────
    adsets = list_adsets(g, s.meta.account_path)
    life = spend_map(g, s.meta.account_path, token, date_preset="maximum")
    d90 = spend_map(g, s.meta.account_path, token,
                    time_range={"since": cut[90].isoformat(), "until": today.isoformat()})
    d60 = spend_map(g, s.meta.account_path, token,
                    time_range={"since": cut[60].isoformat(), "until": today.isoformat()})
    print(f"{len(adsets)} ad sets listed in {s.meta.account_path} · {len(life)} with lifetime spend rows")

    meta, coarse_of, full_of, rep_canon = {}, {}, {}, {}
    for a in adsets:
        c = canonical(a.get("targeting") or {})
        ck, fk = coarse_key(c), full_key(c)
        meta[a["id"]] = a
        coarse_of[a["id"]], full_of[a["id"]] = ck, fk
        rep_canon.setdefault(ck, c)
    for aid, (sp, _r, an, cn) in life.items():          # spend rows for ad sets we could not list
        if aid not in meta:
            meta[aid] = {"id": aid, "name": an, "campaign": {"name": cn},
                         "effective_status": "UNLISTED", "created_time": ""}
            coarse_of[aid] = full_of[aid] = UNKNOWN_KEY
    rep_canon[UNKNOWN_KEY] = None

    by_ck, by_name = defaultdict(list), defaultdict(list)
    for aid, a in meta.items():
        n = cpa.norm(a.get("name", ""))
        by_ck[(_mkey((a.get("campaign") or {}).get("name", "")), n)].append(aid)
        by_name[n].append(aid)

    def life_spend(aid):
        return life.get(aid, (0.0,))[0]

    # ── join sales → ad set → audience ─────────────────────────────────────────
    route = Counter()
    unmatched, ambiguous = Counter(), Counter()
    unmatched_year, matched_year = Counter(), Counter()
    sales_by_grp, sales_by_adset = defaultdict(list), defaultdict(list)
    for x in my_sales:
        ids = by_ck.get((_mkey(x.campaign), x.adset))
        how = "exact"
        if not ids:
            ids, how = by_name.get(x.adset), "name-only"
        yr = str(x.date.year) if x.date else "undated"
        if not ids:
            unmatched[x.adset or "(空 ad set)"] += 1
            unmatched_year[yr] += 1
            route["unmatched"] += 1
            continue
        matched_year[yr] += 1
        grps = {coarse_of[i] for i in ids}
        best = max(ids, key=life_spend)
        if len(grps) > 1:
            ambiguous[x.adset] += 1
            how = "ambiguous→top-spend twin"
        route[how] += 1
        sales_by_grp[coarse_of[best]].append(x)
        sales_by_adset[best].append(x)
    print(f"join routes: {dict(route)}")
    print(f"matched sales by year: {dict(sorted(matched_year.items()))} · "
          f"UNMATCHED by year: {dict(sorted(unmatched_year.items()))}")

    def n_in(rows, w):
        return sum(1 for r in rows if r.date and r.date > cut[w])

    # ── per-audience aggregate ────────────────────────────────────────────────
    groups = []
    for ck, canon in rep_canon.items():
        ids = [aid for aid, k in coarse_of.items() if k == ck]
        rows = sales_by_grp.get(ck, [])
        sp_life = sum(life_spend(i) for i in ids)
        sp90 = sum(d90.get(i, (0.0, 0.0))[0] for i in ids)
        rg90 = sum(d90.get(i, (0.0, 0.0))[1] for i in ids)
        sp60 = sum(d60.get(i, (0.0, 0.0))[0] for i in ids)
        rg60 = sum(d60.get(i, (0.0, 0.0))[1] for i in ids)
        if not rows and sp_life <= 0:
            continue
        groups.append({
            "key": ck, "label": label(canon) if canon else "（读不到定向：已删除或不在列表）",
            "ids": ids, "active": [i for i in ids if meta[i].get("effective_status") == "ACTIVE"],
            "variants": len({full_of[i] for i in ids}),
            "life": len(rows), "d180": n_in(rows, 180), "d90": n_in(rows, 90), "d60": n_in(rows, 60),
            "sp_life": sp_life, "sp90": sp90, "rg90": rg90, "sp60": sp60, "rg60": rg60,
            "last": max((r.date for r in rows if r.date), default=None),
        })
    groups.sort(key=lambda d: (-d["life"], -d["d90"], d["sp_life"]))

    print(f"\n=== TOP {TOP_N} AUDIENCES · by lifetime MY sales in THIS account ===")
    hdr = (f"{'#':>2} {'life':>4} {'180d':>4} {'90d':>4} {'60d':>4} {'spendLife':>9} {'CPAlife':>7} "
           f"{'spend90':>8} {'CPA90':>6} {'CPL90':>6} {'sets':>4} {'on':>3} {'var':>3} {'last':>10}  audience")
    print(hdr)
    print("-" * len(hdr))
    for i, d in enumerate(groups[:TOP_N], 1):
        cpl90 = f"{d['sp90'] / d['rg90']:.0f}" if d["rg90"] else "—"
        print(f"{i:>2} {d['life']:>4} {d['d180']:>4} {d['d90']:>4} {d['d60']:>4} {d['sp_life']:>9,.0f} "
              f"{_cpa(d['sp_life'], d['life']):>7} {d['sp90']:>8,.0f} {_cpa(d['sp90'], d['d90']):>6} "
              f"{cpl90:>6} {len(d['ids']):>4} {len(d['active']):>3} {d['variants']:>3} "
              f"{str(d['last'] or '-'):>10}  {d['label'][:150]}")

    for w, key_sales, key_spend, key_regs in ((90, "d90", "sp90", "rg90"), (60, "d60", "sp60", "rg60")):
        recent = sorted(groups, key=lambda d: (-d[key_sales], -d["d180"], d[key_spend]))
        print(f"\n=== AUDIENCES · by {w}-day MY sales (spend{w} = Meta spend in the same window) ===")
        for i, d in enumerate(recent[:TOP_N], 1):
            if d[key_sales] == 0:
                break
            cpl = f"{d[key_spend] / d[key_regs]:.0f}" if d[key_regs] else "—"
            print(f"{i:>2} {w}d {d[key_sales]:>2} · 180d {d['d180']:>2} · life {d['life']:>3} · "
                  f"spend{w} RM{d[key_spend]:>7,.0f} · CPA{w} {_cpa(d[key_spend], d[key_sales]):>5} · "
                  f"CPL{w} {cpl:>3} · on {len(d['active'])} · {d['label'][:120]}")
        print(f"   (spend in window with 0 sales, biggest first: "
              + "; ".join(f"RM{d[key_spend]:,.0f} {d['label'][:40]}"
                          for d in sorted(groups, key=lambda d: -d[key_spend])
                          if d[key_sales] == 0 and d[key_spend] >= 1000)[:600] + ")")

    # ── detail for the top audiences: member ad sets, active ones, targeting ─────
    for i, d in enumerate(groups[:DETAIL_N], 1):
        print(f"\n--- #{i} {d['label']}")
        print(f"   ACTIVE now: {', '.join(d['active']) or 'none'}")
        members = sorted(d["ids"], key=lambda a: (-len(sales_by_adset.get(a, [])), -life_spend(a)))
        for aid in members[:10]:
            a = meta[aid]
            rows = sales_by_adset.get(aid, [])
            print(f"   {aid} {a.get('effective_status', '?')[:9]:9} {str(a.get('created_time', ''))[:10]:10} "
                  f"sales life {len(rows):>3} / 90d {n_in(rows, 90):>2} · spend life RM{life_spend(aid):>7,.0f} "
                  f"/ 90d RM{d90.get(aid, (0.0,))[0]:>6,.0f} · {a.get('name', '')[:46]}")
        if len(members) > 10:
            print(f"   … +{len(members) - 10} more ad sets")
        canon = rep_canon.get(d["key"])
        if canon:
            print("   targeting: " + json.dumps({k: canon[k] for k in CORE}, ensure_ascii=False, sort_keys=True)[:700])

    # ── what could not be attributed ─────────────────────────────────────────────
    if unmatched:
        print(f"\n=== UNMATCHED sheet ad-set names (non-SG sales, no MY ad set of that name in this account) · "
              f"{sum(unmatched.values())} sales ===")
        for name, n in unmatched.most_common(12):
            print(f"   {n:>4}  {name[:70]}")
    if ambiguous:
        print(f"\n=== AMBIGUOUS names (same name, different audiences → credited to top-spend twin) ===")
        for name, n in ambiguous.most_common(8):
            print(f"   {n:>4}  {name[:70]}")


if __name__ == "__main__":
    main()
