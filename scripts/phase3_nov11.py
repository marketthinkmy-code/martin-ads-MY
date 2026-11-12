"""Read-only Phase 3: 2026-11-11 webinar — Attendance × Survey × Sales × MY Meta spend.

Cohort  : Register rows whose column H (source) is webinar_nov11 (webinar_nov18 and others excluded).
Market  : MY = UTM campaign (J) is a campaign of the verified MY ad account (live from Meta, '+'→space
          recovery); the operator workbook's tag method ([MY]/MARTIN-MY/[SG]) is printed beside it.
Persons : rows joined by phone last 9 digits / e-mail (in memory) so a parent with 2 rows counts once.
Show-up : Register rows the operator workbook matched to Zoom 89007046098 by exact e-mail
          (scripts/phase3/nov11_zoom_rows.json: row numbers + minutes only). Re-checked here: each
          row must exist and be webinar_nov11; duplicates collapse to persons.
Sales   : Paid Student List rows dated 2026-11-11..12 with a phone or e-mail; blank template rows are
          listed and excluded. Buyers are matched to Register by e-mail / phone last 9 (in memory).
Spend   : MY ad account, ad-level insights over the nov11 registration window (first nov11 row date ..
          2026-11-11). Register Leads are attributed to Meta ads by ID (ad name in the verified
          campaign, else mint creative/post ID); UNMATCHED / AMBIGUOUS are never forced.

PRIVACY: names, e-mails and phones are read in memory only. Output = counts, sheet row numbers,
Meta object names/ids, survey categories.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lead_quality_audit import icp_state, map_score, market_tag, num, person_ids  # noqa: E402

from adbot import cpa  # noqa: E402
from adbot.clients.sheets import SheetsClient  # noqa: E402
from adbot.commands import graph_client  # noqa: E402
from adbot.settings import load_settings  # noqa: E402

COHORT = "webinar_nov11"
EVENT = dt.date(2026, 11, 11)
SALE_DAYS = (dt.date(2026, 11, 11), dt.date(2026, 11, 12))
COL = {"date": 6, "source": 7, "campaign": 9, "adset": 10, "ad": 11,
       "q1": 15, "q2": 16, "q3": 17, "q4": 18, "q5": 19, "score": 20}
MIN_N = 20   # persons per group below this -> INSUFFICIENT SAMPLE


def pct(a, b):
    return f"{a / b:.0%}" if b else "—"


def rm(v):
    return f"{v:,.0f}" if v is not None else "N/A"


def roas(rev, spend):
    return f"{rev / spend:.2f}" if spend else "N/A"


def div(a, b):
    return a / b if b else None


class UF:
    def __init__(self):
        self.p = {}

    def f(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def main() -> None:
    s = load_settings()
    sheets = SheetsClient(s.secrets.google_sa_json)
    reg = sheets.read_tab(s.cpa.spreadsheet_id, "Register")
    rows = reg[1:]
    cell = lambda r, k: (r[COL[k]] if COL[k] < len(r) else "").strip()
    print("===== MARTIN MY — PHASE 3: 11/11 WEBINAR · ATTENDANCE × SURVEY × SALES (read-only) =====\n")

    src_c = Counter(cell(r, "source") for r in rows if "nov1" in cell(r, "source").casefold())
    print("Register H values containing 'nov1' (rows):", dict(src_c))

    g = graph_client(s)
    my_names, my_ids, canon, camp_ids = set(), set(), {}, defaultdict(set)
    for c in g.list_campaigns(s.meta.account_path):
        n = cpa.norm(c.get("name") or "")
        my_names.add(n); my_ids.add(str(c["id"])); camp_ids[n].add(str(c["id"]))
        canon[" ".join(n.replace("+", " ").split())] = c.get("name") or ""
    plus_names = {" ".join(n.replace("+", " ").split()) for n in my_names}

    allrecs, nov = [], []
    for ri, r in enumerate(rows, start=2):
        if not any(c.strip() for c in r):
            continue
        x = {"row": ri, "ids": person_ids(r), "date": cpa.parse_date(cell(r, "date")),
             "source": cell(r, "source")}
        allrecs.append(x)
        if x["source"].casefold() != COHORT:
            continue
        camp = cell(r, "campaign"); cn = cpa.norm(camp); adset = cell(r, "adset")
        if not camp:
            mk = "no-UTM"
        elif cn in my_names or camp in my_ids:
            mk = "MY"
        elif " ".join(cn.split()) in plus_names:
            mk = "MY"; camp = canon[" ".join(cn.split())]; adset = adset.replace("   ", " + ")
        else:
            mk = market_tag(camp)
        tag = "MY" if mk == "MY" or mk == "MY-tag" else ("SG" if mk == "SG" else "UNKNOWN")
        d = re.sub(r"\D", "", r[1] if len(r) > 1 else "")
        phone_mk = ("MY" if d.startswith("60") or d.startswith("0") else
                    "SG" if d.startswith("65") or (len(d) == 8 and d[0] in "3689") else
                    "none" if not d else "other")
        ans = [cell(r, q) for q in ("q1", "q2", "q3", "q4", "q5")]
        x.update({"mk": mk, "tag": tag, "phone_mk": phone_mk, "camp": camp, "adset": adset, "ad": cell(r, "ad"),
                  "answers": ans, "complete": all(ans), "score": num(cell(r, "score")),
                  "q2": map_score(ans[1], "q2"), "q5": map_score(ans[4], "q5")})
        nov.append(x)
    print(f"{COHORT} rows: {len(nov)} · dates {min(x['date'] for x in nov if x['date'])}..{max(x['date'] for x in nov if x['date'])}")
    print("  market (verified MY account) rows:", dict(Counter(x["mk"] for x in nov)))
    print("  market (workbook tag method) rows:", dict(Counter(x["tag"] for x in nov)))
    print("  verified × phone dialling code:", dict(Counter((x["mk"], x["phone_mk"]) for x in nov)))

    # persons inside the cohort
    uf = UF()
    for x in nov:
        uf.f(("row", x["row"]))
        for k in x["ids"]:
            uf.u(("row", x["row"]), k)
    for x in nov:
        x["pid"] = uf.f(("row", x["row"]))
    persons = defaultdict(list)
    for x in nov:
        persons[x["pid"]].append(x)
    dup = {p: L for p, L in persons.items() if len(L) > 1}
    print(f"  distinct persons {len(persons)} · persons with >1 row {len(dup)} (rows {sum(len(L) for L in dup.values())})")
    for p, L in dup.items():
        print(f"    dup person rows {[x['row'] for x in L]} markets {[x['mk'] for x in L]}")

    # ── attendance ────────────────────────────────────────────────────────────
    z = json.loads(Path("scripts/phase3/nov11_zoom_rows.json").read_text())["attendees"]
    byrow = {x["row"]: x for x in nov}
    allrow = {x["row"]: x for x in allrecs}
    att_rows, att_min = set(), {}
    print(f"\nZoom attendees in workbook: {len(z)} unique e-mails · with a Register row {sum(1 for a in z if a['register_row'])}")
    for a in z:
        rr = a["register_row"]
        if not rr:
            continue
        if rr not in byrow:
            src = allrow.get(rr, {}).get("source", "ROW NOT FOUND")
            print(f"  ! attendee row {rr} is not {COHORT} (source: {src}) — not counted")
            continue
        if a["market"] not in ("NOT_MATCHED",) and a["market"] != byrow[rr]["tag"]:
            print(f"  · row {rr}: workbook market {a['market']} vs tag method {byrow[rr]['tag']} (verified {byrow[rr]['mk']})")
        att_rows.add(rr); att_min[rr] = max(att_min.get(rr, 0), a["minutes"])
    unm = [a for a in z if not a["register_row"]]
    print(f"  Zoom e-mails with NO {COHORT} Register match: {len(unm)} (minutes ≥30: {sum(1 for a in unm if a['minutes'] >= 30)}) — not attributed")

    # ── sales ─────────────────────────────────────────────────────────────────
    v = sheets.read_tab(s.cpa.spreadsheet_id, s.cpa.sales_tab)
    hi = next((i for i, r in enumerate(v[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    header, prow = v[hi], v[hi + 1:]
    col = cpa.find_columns(header)
    hk = [cpa._hkey(h) for h in header]
    ec = [i for i, k in enumerate(hk) if "email" in k or "邮箱" in k or "電郵" in k]
    pc = [i for i, k in enumerate(hk) if any(n in k for n in ("whatsapp", "號碼", "号码", "phone"))]
    pv = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()
    reg_index = defaultdict(set)
    for x in allrecs:
        for k in x["ids"]:
            reg_index[k].add(x["row"])
    buyers = []
    print(f"\nPaid Student List: header row {hi + 1} · email cols {len(ec)} · phone cols {len(pc)}")
    for k, r in enumerate(prow):
        sr = hi + 2 + k
        d = cpa.parse_date(pv(r, col.get("date", -1)))
        if not (d and SALE_DAYS[0] <= d <= SALE_DAYS[1]) and sr < 1320:
            continue
        if sr >= 1320 and not (d and SALE_DAYS[0] <= d <= SALE_DAYS[1]) and not any(c.strip() for c in r):
            continue
        email = next((pv(r, i).lower() for i in ec if "@" in pv(r, i)), "")
        ph = re.sub(r"\D", "", next((pv(r, i) for i in pc if pv(r, i)), ""))
        ids = ({"e:" + email} if email else set()) | ({"p:" + ph[-9:]} if len(ph) >= 8 else set())
        amt = num(pv(r, col.get("amount", -1)))
        if not ids:
            print(f"  row {sr}: date {d} · NO e-mail/phone (blank template) — excluded")
            continue
        if not (d and SALE_DAYS[0] <= d <= SALE_DAYS[1]):
            continue
        hits = sorted({rr for i in ids for rr in reg_index.get(i, ())})
        nrow = [rr for rr in hits if rr in byrow]
        mk = ("MY" if ph.startswith("60") or ph.startswith("0") else "SG" if ph.startswith("65") or
              (len(ph) == 8 and ph[0] in "3689") else "none" if not ph else "other")
        b = {"paid_row": sr, "date": d, "amount": amt, "phone_mk": mk, "reg_rows": hits, "nov_rows": nrow,
             "via": "+".join(sorted({i[0] for i in ids for rr in nrow if rr in reg_index.get(i, ())}))}
        buyers.append(b)
        print(f"  row {sr}: date {d} · amount {amt} · phone market {mk} · Register rows matched {len(hits)} "
              f"· {COHORT} rows {nrow} (via {b['via'] or '-'})")
    buyer_pids = {}
    for b in buyers:
        for rr in b["nov_rows"]:
            buyer_pids[byrow[rr]["pid"]] = b

    # ── person-level table ───────────────────────────────────────────────────
    hist_first = {}
    for x in allrecs:
        for k in x["ids"]:
            hist_first.setdefault(k, []).append(x)
    P = []
    for p, L in persons.items():
        rows_ = sorted(x["row"] for x in L)
        first_row = rows_[0]
        ids = set().union(*(x["ids"] for x in L))
        earlier = {y["row"]: y for k in ids for y in hist_first.get(k, []) if y["row"] < first_row and y["row"] not in rows_}
        best = max(L, key=lambda x: (x["score"] is not None, x["complete"], x["row"]))
        mk = "MY" if any(x["mk"] == "MY" for x in L) else best["mk"]
        attended = any(x["row"] in att_rows for x in L)
        b = buyer_pids.get(p)
        P.append({"pid": p, "rows": rows_, "mk": mk, "tag": best["tag"], "x": best, "camp": best["camp"],
                  "adset": best["adset"], "ad": best["ad"], "date": min(x["date"] for x in L if x["date"]),
                  "complete": best["complete"] and best["score"] is not None, "score": best["score"],
                  "ql": best["score"] is not None and best["score"] >= 70, "icp": icp_state(best),
                  "att": attended, "mins": max((att_min.get(x["row"], 0) for x in L), default=0),
                  "buyer": b, "rev": (b["amount"] or 0) if b else 0,
                  "returning": bool(earlier), "prior": len(earlier),
                  "prior_src": sorted({y["source"] for y in earlier.values()})})

    def seg(title, L):
        n = len(L); a = sum(p["att"] for p in L); bu = sum(1 for p in L if p["buyer"])
        flag = " INSUFFICIENT SAMPLE" if n < MIN_N else ""
        print(f"  {title:<34} persons {n:>3} · show-up {a:>3} {pct(a, n):>4} · buyers {bu} {pct(bu, n):>4} "
              f"· buyers/show-up {pct(bu, a):>4}{flag}")

    for market in ("MY", "SG"):
        M = [p for p in P if (p["mk"] == "MY") == (market == "MY") and (market == "MY" or p["tag"] == "SG")]
        print(f"\n--- {market} persons (MY = verified MY ad account; SG = [SG] tag) ---")
        seg("ALL", M)
        seg("Survey Completed (Q1–Q5 + score)", [p for p in M if p["complete"]])
        seg("Pending (no score)", [p for p in M if not p["complete"]])
        seg("Qualified ≥70", [p for p in M if p["ql"]])
        seg("Core ICP", [p for p in M if p["icp"] == "yes"])
        seg("First-time", [p for p in M if not p["returning"]])
        seg("Returning (earlier Register row)", [p for p in M if p["returning"]])
        if market == "MY":
            print("  scored MY persons (row · score · ICP · attended mins · buyer · first/returning · ad):")
            for p in sorted((p for p in M if p["score"] is not None), key=lambda p: p["rows"][0]):
                print(f"    {p['rows']} · {p['score']:.0f} · {p['icp']} · {p['mins']:.0f}m · "
                      f"{'BUYER' if p['buyer'] else '-'} · {'ret' if p['returning'] else 'new'} · {p['ad']}")
    other = [p for p in P if p["mk"] != "MY" and p["tag"] != "SG"]
    print(f"\n--- not MY / not SG persons: {len(other)} · show-up {sum(p['att'] for p in other)} · "
          f"buyers {sum(1 for p in other if p['buyer'])} · markets {dict(Counter(p['mk'] for p in other))}")
    for p in other:
        if p["att"] or p["buyer"]:
            print(f"    rows {p['rows']} · mk {p['mk']} · UTM campaign '{p['camp'][:60]}' · source ok · "
                  f"attended {p['att']} · buyer {bool(p['buyer'])}")

    # ── buyers ────────────────────────────────────────────────────────────────
    print("\n--- buyers (all markets) ---")
    for b in buyers:
        p = next((p for p in P if p["buyer"] is b), None)
        if not p:
            print(f"  paid row {b['paid_row']}: NOT in {COHORT} cohort (Register rows matched {len(b['reg_rows'])}) — not attributed")
            continue
        print(f"  paid row {b['paid_row']} → Register {p['rows']} · market {p['mk']} (phone {b['phone_mk']}) · reg date {p['date']} "
              f"({(EVENT - p['date']).days}d before webinar) · survey {'completed' if p['complete'] else 'NOT completed'} "
              f"(answers {sum(1 for a in p['x']['answers'] if a)}/5) · attended {p['mins']:.0f} min · "
              f"{'Returning' if p['returning'] else 'First-time'} (earlier rows {p['prior']}, sources {p['prior_src']}) · "
              f"{p['camp']} › {p['adset']} › {p['ad']}")

    # ── Meta attribution + spend ─────────────────────────────────────────────
    MY = [p for p in P if p["mk"] == "MY"]
    w0 = min(p["date"] for p in P)
    ads = {}
    for cn in {cpa.norm(p["camp"]) for p in MY}:
        for cid in camp_ids.get(cn, ()):
            for a_ in g._get_all(f"{cid}/ads", {"fields": "id,name,adset_id,adset{name},campaign{name},creative{id,effective_object_story_id}", "limit": 200}):
                ads[a_["id"]] = {"name": a_.get("name") or "", "adset_id": str(a_.get("adset_id") or ""),
                                 "adset": (a_.get("adset") or {}).get("name") or "", "cid": cid,
                                 "creative": str((a_.get("creative") or {}).get("id") or ""),
                                 "post": str((a_.get("creative") or {}).get("effective_object_story_id") or "")}
    mint = defaultdict(set)
    for f in Path("scripts/mint_specs").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        for vv in d.get("videos", []):
            m = (d.get("_minted") or {}).get(vv.get("key"))
            for k in ((m.get("creative_id"), m.get("post_id")) if isinstance(m, dict) else (m,)):
                if vv.get("ad_name") and k:
                    mint[cpa.ad_key(vv["ad_name"])].add(str(k))

    def resolve(p):
        cids = camp_ids.get(cpa.norm(p["camp"]), set())
        cand, how = [i for i, a in ads.items() if a["cid"] in cids and cpa.ad_key(a["name"]) == cpa.ad_key(p["ad"])], "name"
        if not cand:
            crs = mint.get(cpa.ad_key(p["ad"]), set())
            cand, how = [i for i, a in ads.items() if a["cid"] in cids and (a["creative"] in crs or a["post"] in crs)], "mint id"
        if len(cand) > 1:
            c2 = [i for i in cand if cpa.norm(ads[i]["adset"]) == cpa.norm(p["adset"])]
            if len(c2) == 1:
                return c2[0], how + "+adset"
            return None, f"AMBIGUOUS({len(cand)})"
        return (cand[0], how) if cand else (None, "UNMATCHED")

    for p in MY:
        p["ad_id"], p["how"] = resolve(p)
        cids = camp_ids.get(cpa.norm(p["camp"]), set())
        p["cid"] = ads[p["ad_id"]]["cid"] if p["ad_id"] else (next(iter(cids)) if len(cids) == 1 else None)
        p["adset_id"] = ads[p["ad_id"]]["adset_id"] if p["ad_id"] else None
    print(f"\n--- MY attribution to Meta ads by ID: {dict(Counter(p['how'] for p in MY))}")
    for p in MY:
        if not p["ad_id"]:
            print(f"    {p['how']}: rows {p['rows']} · {p['camp'][:50]} › {p['adset'][:40]} › {p['ad'][:40]}")

    reg_tok = "offsite_conversion.fb_pixel_" + (s.meta.conversion_event or "").lower()
    sp, ml, par, nm = defaultdict(float), defaultdict(float), {}, {}
    for r in g.account_insights(s.meta.account_path, level="ad",
                                fields="ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,actions",
                                time_range={"since": w0.isoformat(), "until": EVENT.isoformat()}):
        a = str(r["ad_id"])
        sp[a] += float(r.get("spend") or 0)
        ml[a] += sum(float(x.get("value") or 0) for x in r.get("actions") or [] if x.get("action_type") == reg_tok)
        par[a] = (str(r["campaign_id"]), str(r["adset_id"]))
        nm[str(r["campaign_id"])] = r.get("campaign_name") or ""; nm[str(r["adset_id"])] = r.get("adset_name") or ""
        nm[a] = r.get("ad_name") or ""
    tot = sum(sp.values())
    print(f"\nMY Meta spend {w0}..{EVENT}: RM{tot:,.0f} · Meta leads {sum(ml.values()):.0f} · MY {COHORT} persons {len(MY)}")
    print("  CPL = spend ÷ Register persons · CPS = spend ÷ show-ups · CPA = spend ÷ buyers · ROAS = revenue ÷ spend · "
          "CPQL (aux) = spend ÷ qualified ≥70")

    def table(level):
        key = {"campaign": lambda a: par[a][0], "adset": lambda a: par[a][1], "ad": lambda a: a}[level]
        pkey = {"campaign": lambda p: p["cid"], "adset": lambda p: p["adset_id"], "ad": lambda p: p["ad_id"]}[level]
        grp = defaultdict(lambda: {"sp": 0.0, "ml": 0.0})
        for a in sp:
            grp[key(a)]["sp"] += sp[a]; grp[key(a)]["ml"] += ml[a]
        pp = defaultdict(list)
        for p in MY:
            pp[pkey(p)].append(p)
        ks = sorted(set(grp) | {k for k in pp if k}, key=lambda k: -grp[k]["sp"])
        print(f"\n  by {level}:")
        print(f"  {'spend':>6} {'Mlead':>5} {'Reg':>3} {'new':>3} {'ret':>3} {'Show':>4} {'Buy':>3} {'Rev':>6} {'CPL':>5} "
              f"{'CPS':>5} {'CPA':>6} {'ROAS':>5} {'QL':>2} {'CPQL':>5}  name")
        for k in ks:
            L = pp.get(k, []); g_ = grp[k]
            if g_["sp"] < 1 and not L:
                continue
            n = len(L); sh = sum(p["att"] for p in L); bu = sum(1 for p in L if p["buyer"]); rv = sum(p["rev"] for p in L)
            ql = sum(p["ql"] for p in L)
            flag = "" if n >= MIN_N else " ⚑"
            label = nm.get(k) or (ads.get(k, {}).get("name") if level == "ad" else "") or str(k)
            print(f"  {g_['sp']:>6,.0f} {g_['ml']:>5.0f} {n:>3} {sum(not p['returning'] for p in L):>3} "
                  f"{sum(p['returning'] for p in L):>3} {sh:>4} {bu:>3} {rv:>6,.0f} {rm(div(g_['sp'], n)):>5} "
                  f"{rm(div(g_['sp'], sh)):>5} {rm(div(g_['sp'], bu)):>6} "
                  f"{roas(rv, g_['sp']):>5} {ql:>2} {rm(div(g_['sp'], ql)):>5}  "
                  f"{label[:70]}{flag}")
        un = [p for p in MY if not pkey(p)]
        if un:
            print(f"  (+ {len(un)} MY persons with no {level} ID: show-up {sum(p['att'] for p in un)}, buyers "
                  f"{sum(1 for p in un if p['buyer'])} — not forced)")

    for lv in ("campaign", "adset", "ad"):
        table(lv)
    n = len(MY); sh = sum(p["att"] for p in MY); bu = sum(1 for p in MY if p["buyer"]); rv = sum(p["rev"] for p in MY)
    ql = sum(p["ql"] for p in MY); new = [p for p in MY if not p["returning"]]
    print(f"\nMY TOTAL: spend RM{tot:,.0f} · persons {n} · show-up {sh} · buyers {bu} · revenue RM{rv:,.0f} · "
          f"CPL {rm(div(tot, n))} · cost/show-up {rm(div(tot, sh))} · CPA {rm(div(tot, bu))} · "
          f"ROAS {rv / tot if tot else 0:.2f} · QL {ql} · CPQL {rm(div(tot, ql))} (aux, INSUFFICIENT SAMPLE)")
    print(f"  first-time persons {len(new)} (show-up {sum(p['att'] for p in new)}, buyers {sum(1 for p in new if p['buyer'])}) · "
          f"returning {n - len(new)} (show-up {sum(p['att'] for p in MY if p['returning'])}, buyers "
          f"{sum(1 for p in MY if p['returning'] and p['buyer'])})")


if __name__ == "__main__":
    main()
