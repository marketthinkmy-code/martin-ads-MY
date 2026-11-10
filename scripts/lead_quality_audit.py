"""Read-only: Martin MY lead-quality audit from the Register tab's GHL survey score.

Register columns used (letters per the operator's spec, confirmed against the header row at run time):
  G registration date · H source · J UTM campaign · K UTM ad set · L UTM ad name
  P–T survey Q1–Q5 (raw answers) · U Martin Lead Quality Score (GHL total, not recomputed)

Market: a row counts as MY only when its UTM campaign is a campaign of the verified MY ad account
(name or id, read live from Meta); a [MY]/MARTIN-MY tag that is not in the account is reported apart.
[SG]/MARTIN-SG/US rows, other UTMs and rows with no UTM are kept out of every MY figure and counted
separately. Internal tests (test/测试 in the name, e-mail or source) are excluded and counted.

Grades: A 85–100 · B 70–84 · C 50–69 · D 0–49. High Score = total ≥ 70. Core ICP = total ≥ 70 AND
Q2 score ≥ 20 AND Q5 score ≥ 16, with Q2/Q5 scored from the raw answer text (never from the total).

PRIVACY: name / e-mail / phone cells are read only to spot test rows. Output is counts, averages,
survey answer categories (multiple-choice text) and Meta object names — never a person.

Date rules (operator, final, 2026-11-06):
  * Every figure is dated by column G (registration date). There is no survey timestamp and none is
    added: a parent who finishes the survey days later is counted in the original registration cohort,
    with the score as it reads today.
  * Survey Completion Rate = cohort parents with all of Q1–Q5 answered ÷ the cohort's valid registrations.
  * Qualified Lead Rate   = cohort parents with Score ≥ 70 ÷ the cohort's valid registrations.
  * Preliminary CPQL = matched MY Meta spend (same campaign / ad set / ad, same registration-date range)
    ÷ qualified leads, computed whenever QL > 0. QL = 0 -> CPQL N/A (never RM0, never "poor").
    Calculable is not the same as reliable: thin samples are labelled PRELIMINARY / INSUFFICIENT DATA.
  * The survey score never drives SCALE / REDUCE / PAUSE; the Ads Agent's CPL / CPA / ROAS rules stand.
  * A registrant with no survey yet is Pending — never Low Quality (D).
  * MY and SG are never mixed (budgets, currency, rows).

  * Eligible vs Pre-launch: only parents who registered on/after the survey launch date are eligible
    for the new survey; earlier registrants are "Pre-launch Registration / Invitation Unknown" (no send
    record exists, so never "Not Invited" or "refused") and stay out of every rate denominator. An eligible parent with no answers yet is Eligible Pending.
  * Missing UTM on a surveyed row is reported as a fact, never as a tracking error: the check that
    matters is whether parents who registered WITH a real MY ad UTM still carry J/K/L after the survey.
  * Thin samples read PRELIMINARY / INSUFFICIENT DATA — the audit never recommends REDUCE or PAUSE.
  * Duplicates: raw registration rows AND distinct persons (phone last 9 digits / e-mail, in memory)
    are both printed. Both Ads Agents (MY, SG) count registrations as the Meta pixel
    COMPLETE_REGISTRATION result — that is not changed here.
  * Cohorts younger than IMMATURE_DAYS are flagged immature (survey answers still arriving).

Env:
  ADBOT_SINCE          first registration date to analyse (default: survey launch date)
  ADBOT_SURVEY_LAUNCH  survey launch date (default: provisional 2026-11-06, operator-confirmed)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.settings import load_settings

COL = {"date": 6, "source": 7, "campaign": 9, "adset": 10, "ad": 11,
       "q1": 15, "q2": 16, "q3": 17, "q4": 18, "q5": 19, "score": 20}
PII_COLS = (0, 1, 2)   # Name, Phone Number, Email — read only for the test check

# Exact answer text -> points, per the operator's GHL option scores (2026-11-10):
#   Q1 age 3/8/12/15/15/8/3 · Q2 25/25/20/12/5 · Q3 20/15/8/3 · Q4 20/15/10/3 · Q5 20/16/10/4.
# Keys are the FULL answer texts exactly as they appear in the Register sheet. Q2/Q5 texts are tied
# to the operator's own option labels; Q1/Q3/Q4 are tied by option wording and checked against the
# GHL total in column U on every scored row (section S). Anything not listed is UNMAPPED.
SCORE_MAP = {
    "q1": {"3–6 岁": 8, "10–12 岁": 15, "13–15 岁": 15},
    "q2": {"有记录孩子身高，发现最近长高速度明显变慢": 25,
           "曾经带孩子做成长评估，希望进一步了解或改善": 25,
           "孩子明显比同龄人矮，担心差距越来越大": 20,
           "不确定孩子目前的成长情况是否正常": 12},
    "q3": {"希望未来 1 个月内开始": 20, "希望未来 1–3 个月内开始": 15, "暂时没有计划，想先了解资讯": 3},
    "q4": {"愿意长期配合，并定期记录孩子的成长变化": 20, "愿意在多数日子安排时间，协助孩子执行": 15,
           "想先了解方法，再尝试适合的部分": 10, "目前较难安排时间，主要想先听建议": 3},
    "q5": {"希望有专业人士了解孩子的情况，给予适合的指导，并持续跟进": 20,
           "希望有一套清楚的方法，可以跟着步骤帮助孩子改善生活习惯": 16,
           "希望先了解孩子成长的关键因素，再决定下一步": 10,
           "主要想获得一些免费的成长知识和建议": 4},
}


def map_score(ans: str, q: str) -> Optional[int]:
    """Exact-text lookup. None = not answered, -1 = UNMAPPED (needs review)."""
    a = " ".join((ans or "").split())
    if not a:
        return None
    return SCORE_MAP[q].get(a, -1)


def icp_state(x) -> str:
    """Core ICP = score ≥70 AND Q2 ≥20 AND Q5 ≥16. An UNMAPPED Q2/Q5 is REVIEW, never an automatic no."""
    if x["score"] is None or x["score"] < 70:
        return "no"
    if x["q2"] == -1 or x["q5"] == -1:
        return "REVIEW"
    return "yes" if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16 else "no"


def grade(s: float) -> str:
    return "A" if s >= 85 else "B" if s >= 70 else "C" if s >= 50 else "D"


def safe_cat(v: str) -> bool:
    return "@" not in v and len(re.sub(r"\D", "", v)) < 8 and len(v) <= 80


def num(v: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", (v or "").replace(",", ""))
    return float(m.group()) if m else None


def market_tag(camp: str) -> str:
    c = camp.casefold()
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if any(t in c for t in ("[us]", "北美", "加州", "california", "温哥华", "vancouver")):
        return "US"
    if "[my]" in c or "martin-my" in c:
        return "MY-tag"
    return "other"


MIN_ELIGIBLE = 20   # per campaign / ad: fewer eligible registrations -> INSUFFICIENT DATA
MIN_SCORED = 10     # per campaign / ad: fewer scored leads -> INSUFFICIENT DATA
CPQL_MIN_SCORED = 30     # account-wide scored MY leads before CPQL is decision-reliable (not a calc gate)
PROVISIONAL_LAUNCH = "2026-11-06"   # operator-confirmed provisional survey start (no send records)
IMMATURE_DAYS = 7        # cohorts registered within this many days are still collecting answers
CPQL_MIN_UTM_CHECK = 5   # surveyed MY rows with a real ad UTM needed to confirm UTMs survive the survey


def pct(a, b) -> str:
    return f"{a / b:.0%}" if b else "—"


def person_ids(r) -> set:
    """Every normalised join key of a row (phone last 9 digits, e-mail) — in memory only, never printed."""
    out = set()
    d = re.sub(r"\D", "", r[1] if len(r) > 1 else "")
    if len(d) >= 8:
        out.add("p:" + d[-9:])
    e = (r[2] if len(r) > 2 else "").strip().lower()
    if "@" in e:
        out.add("e:" + e)
    return out


def person_key(r) -> str:
    """Join key for de-duplication only (phone last 9 digits, else e-mail); never printed."""
    d = re.sub(r"\D", "", r[1] if len(r) > 1 else "")
    if len(d) >= 8:
        return "p:" + d[-9:]
    e = (r[2] if len(r) > 2 else "").strip().lower()
    return "e:" + e if "@" in e else ""


def main() -> None:
    s = load_settings()
    sheets = SheetsClient(s.secrets.google_sa_json)
    values = sheets.read_tab(s.cpa.spreadsheet_id, "Register")
    header = values[0]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    print("===== MARTIN MY — LEAD QUALITY AUDIT (read-only) =====\n")
    print("Register header A–U (checked against the spec):")
    for i in range(min(21, len(header))):
        h = header[i]
        print(f"  {letters[i]}: {h if safe_cat(h) else '(looks like data — not printed)'}")
    rows = values[1:]
    # fill check: how many rows (last 14 days by column G) have each column A..Z filled — counts only
    cutoff = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date() - dt.timedelta(days=14)
    recent = [r for r in rows if (lambda d: d and d >= cutoff)(cpa.parse_date(r[6] if len(r) > 6 else ""))]
    width = max((len(r) for r in rows), default=0)
    print(f"\nrows {len(rows)} · widest row {width} cols · rows dated in the last 14 days {len(recent)}")
    print("  filled per column, last 14 days: " + "  ".join(
        f"{letters[i]}={sum(1 for r in recent if i < len(r) and r[i].strip())}" for i in range(min(width, 26))))
    print("  filled per column, all rows:     " + "  ".join(
        f"{letters[i]}={sum(1 for r in rows if i < len(r) and r[i].strip())}" for i in range(min(width, 26))))

    # ── verified MY campaigns from the live MY ad account ──────────────────────
    g = graph_client(s)
    my_names, my_ids = set(), set()
    canon, camp_ids = {}, defaultdict(set)
    for c in g.list_campaigns(s.meta.account_path):
        camp_ids[cpa.norm(c.get("name") or "")].add(str(c.get("id")))
        my_names.add(cpa.norm(c.get("name") or ""))
        canon[" ".join(cpa.norm(c.get("name") or "").replace("+", " ").split())] = c.get("name") or ""
        my_ids.add(str(c.get("id")))
    print(f"\nMY ad account {s.meta.account_path}: {len(my_names)} campaigns read for verification")

    my_names_plus = {" ".join(n.replace("+", " ").split()) for n in my_names}
    plus_fixed = 0
    cell = lambda r, k: (r[COL[k]] if COL[k] < len(r) else "").strip()
    recs, tests, undated = [], 0, 0
    test_by = Counter()
    for ri, r in enumerate(rows, start=2):   # sheet row number (header is row 1)
        if not any(c.strip() for c in r):
            continue
        name = (r[0] if len(r) > 0 else "").casefold()
        email = (r[2] if len(r) > 2 else "").casefold()
        src = cell(r, "source").casefold()
        hit = ("name" if re.search(r"\btest\b|测试|測試", name) else
               "email" if re.match(r"test[\d._+-]*@|[^@]*\+test@", email) else
               "source" if "test" in src else None)
        if hit:
            tests += 1
            test_by[hit] += 1
            continue
        d = cpa.parse_date(cell(r, "date"))
        if not d:
            undated += 1
            continue
        camp = cell(r, "campaign")
        cn = cpa.norm(camp)
        if not camp:
            mk = "no-UTM"
        elif cn in my_names or camp in my_ids:
            mk = "MY"
        elif " ".join(cn.split()) in my_names_plus:
            mk = "MY"            # same campaign; the UTM decoded '+' as a space
            plus_fixed += 1
            camp = canon.get(" ".join(cn.split()), camp)
        else:
            mk = market_tag(camp)
        answers = [cell(r, q) for q in ("q1", "q2", "q3", "q4", "q5")]
        sc = num(cell(r, "score"))
        adset = cell(r, "adset")
        if plus_fixed and camp != cell(r, "campaign"):
            adset = adset.replace("   ", " + ")   # same '+'-decoding damage in the ad set name
        recs.append({"row": ri, "pid": person_key(r) or f"row:{len(recs)}", "date": d, "mk": mk, "camp": camp, "adset": adset, "ad": cell(r, "ad"),
                     "source": cell(r, "source"), "answers": answers,
                     "surveyed": any(answers), "complete": all(answers), "score": sc,
                     "pts": [map_score(answers[i], f"q{i + 1}") for i in range(5)],
                     "ids": person_ids(r),
                     "q2": map_score(answers[1], "q2"), "q5": map_score(answers[4], "q5")})

    scored_dates = Counter(x["date"] for x in recs if x["score"] is not None)
    first = min(scored_dates) if scored_dates else None
    print(f"rows with a GHL score (U), all markets: {sum(scored_dates.values())} · "
          f"with any survey answer (P–T): {sum(1 for x in recs if x['surveyed'])}")
    print("  scored rows per registration date: " + ", ".join(f"{d:%m-%d} {n}" for d, n in sorted(scored_dates.items())))
    surveyed_dates = [x["date"] for x in recs if x["surveyed"]]
    launch = dt.date.fromisoformat(os.environ.get("ADBOT_SURVEY_LAUNCH") or PROVISIONAL_LAUNCH)
    print(f"survey launch date used: {launch} "
          f"({'set by ADBOT_SURVEY_LAUNCH' if os.environ.get('ADBOT_SURVEY_LAUNCH') else 'provisional operator date'})"
          f" · first registration date carrying any answer: {min(surveyed_dates) if surveyed_dates else 'none yet'}")
    since = dt.date.fromisoformat(os.environ["ADBOT_SINCE"]) if os.environ.get("ADBOT_SINCE") else (launch or first)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    print(f"excluded internal tests: {tests} {dict(test_by)} · undated rows: {undated} · "
          f"MY rows matched after '+'→space UTM decoding: {plus_fixed}")
    print(f"first registration date with a GHL score: {first} → analysis window {since}..{today}\n")
    if not since:
        since = today - dt.timedelta(days=14)
        print(f"NO GHL survey answers or scores in P–U — score sections skipped; registration and "
              f"attribution audited for {since}..{today}\n")
    win = [x for x in recs if x["date"] >= since]
    for x in win:
        x["eligible"] = bool(launch) and x["date"] >= launch

    # ── A. by market + daily cohort ────────────────────────────────────────────
    print("--- A. registrations in window by market (MY = campaign verified in the MY ad account) ---")
    mkc = Counter(x["mk"] for x in win)
    for k in ("MY", "MY-tag", "SG", "US", "other", "no-UTM"):
        sub = [x for x in win if x["mk"] == k]
        print(f"  {k:<7} regs {len(sub):>5} · survey any {sum(x['surveyed'] for x in sub):>4} · all 5 answered "
              f"{sum(x['complete'] for x in sub):>4} · score present {sum(x['score'] is not None for x in sub):>4}")
    my = [x for x in win if x["mk"] == "MY"]
    pre = [x for x in win if x["mk"] == "MY" and not x["eligible"]]
    print(f"\n  MY Pre-launch Registration / Invitation Unknown (registered before {launch}): {len(pre)} rows · "
          f"{len({x['pid'] for x in pre})} persons — excluded from every rate (not 'Not Invited', not 'refused')")
    elig = [x for x in my if x["eligible"]]
    print(f"  MY eligible: raw registration rows {len(elig)} · distinct persons {len({x['pid'] for x in elig})} "
          f"(phone last 9 / e-mail, in memory)")
    print("  MY cohort by registration date (G), eligible only. Two denominators, never mixed:")
    print("    compl% / cover% / QL/regs = ÷ valid registrations (raw rows) · QL/done = ÷ completed surveys")
    print("    pending = no score yet (not Low Quality) · ICP = Core ICP · imm = immature cohort (<"
          f"{IMMATURE_DAYS}d, answers still arriving)")
    print(f"  {'date':<11} {'regs':>4} {'pers':>4} {'done':>4} {'compl%':>6} {'scored':>6} {'cover%':>6} {'pend':>4} "
          f"{'QL':>3} {'QL/regs':>7} {'QL/done':>7} {'ICP':>3}")
    byd = defaultdict(list)
    for x in elig:
        byd[x["date"]].append(x)
    for d in sorted(byd):
        L = byd[d]
        done = sum(x["complete"] for x in L)
        sc = sum(x["score"] is not None for x in L)
        qlL = [x for x in L if x["score"] is not None and x["score"] >= 70]
        icp = sum(1 for x in qlL if icp_state(x) == "yes")
        pend = sum(1 for x in L if x["score"] is None)
        imm = " imm" if (today - d).days < IMMATURE_DAYS else ""
        print(f"  {d}  {len(L):>4} {len({x['pid'] for x in L}):>4} {done:>4} {pct(done, len(L)):>6} {sc:>6} "
              f"{pct(sc, len(L)):>6} {pend:>4} {len(qlL):>3} {pct(len(qlL), len(L)):>7} {pct(len(qlL), done):>7} "
              f"{icp:>3}{imm}")
    miss = [x for x in my if x["surveyed"] and x["score"] is None]
    print(f"\n  MY rows with survey answers but NO score (U empty): {len(miss)}")
    print(f"  MY rows with a score but no survey answers: {sum(1 for x in my if not x['surveyed'] and x['score'] is not None)}")
    bad = [x for x in my if x["score"] is not None and not (0 <= x["score"] <= 100)]
    print(f"  MY scores outside 0–100: {len(bad)}")

    # ── B. distribution ────────────────────────────────────────────────────────
    my_all = my
    my = [x for x in my if x["eligible"]]
    sc_my = [x for x in my if x["score"] is not None]
    pending = [x for x in my if x["score"] is None]
    print(f"\n--- B. MY score distribution (eligible regs {len(my)} · scored {len(sc_my)} · Eligible Pending {len(pending)}) ---")
    print("  (Eligible Pending = registered after launch, no score yet — not Low Quality; % of eligible regs, then % of scored)")
    gc = Counter(grade(x["score"]) for x in sc_my)
    for gname in "ABCD":
        print(f"  {gname}:       {gc[gname]:>4}  {pct(gc[gname], len(my)):>5}  {pct(gc[gname], len(sc_my)):>5}")
    print(f"  Pending: {len(pending):>4}  {pct(len(pending), len(my)):>5}   (+ {len(pre)} pre-launch, not counted)")
    hs = [x for x in sc_my if x["score"] >= 70]
    core = [x for x in hs if icp_state(x) == "yes"]
    core_review = [x for x in hs if icp_state(x) == "REVIEW"]
    done_my = sum(x["complete"] for x in my)
    print(f"  High Score / Qualified (≥70): {len(hs)} · QL ÷ valid regs {pct(len(hs), len(my))} · "
          f"QL ÷ completed surveys {pct(len(hs), done_my)} ({done_my} completed) — different denominators")
    print(f"  Score Coverage Rate (scored ÷ valid regs): {pct(len(sc_my), len(my))}")
    print(f"  Core ICP (≥70 + Q2≥20 + Q5≥16 from exact answer text): {len(core)} ({pct(len(core), len(my))} of eligible regs)"
          f" · NEEDS REVIEW (≥70 but Q2/Q5 UNMAPPED): {len(core_review)}")
    print(f"  ≥70 but NOT Core ICP: {len(hs) - len(core) - len(core_review)}")
    if sc_my:
        print(f"  mean score {mean(x['score'] for x in sc_my):.1f}")

    print("\n  Q1–Q5 answer texts seen, all markets — exact text → points (UNMAPPED = needs review):")
    for qi in range(5):
        for ans, n in Counter(x["answers"][qi] for x in recs if x["answers"][qi]).most_common():
            if safe_cat(ans):
                pts = map_score(ans, f"q{qi + 1}")
                print(f"    Q{qi + 1} {n:>3} × [{'UNMAPPED' if pts == -1 else pts}] {ans}")

    # ── Meta objects (by ID) for the campaigns the MY rows point at ───────────────
    try:
        cur = g.get_object(s.meta.account_path, "currency,name").get("currency")
    except Exception as exc:  # noqa: BLE001
        cur = f"? ({str(exc)[:60]})"
    w0 = max(since, launch or since)
    print(f"\nMeta spend currency — MY account {s.meta.account_path}: {cur} · window {w0}..{today} (same as the registration dates)")
    ads = {}
    for cn in {cpa.norm(x["camp"]) for x in my}:
        for cid in camp_ids.get(cn, ()):
            for a_ in g._get_all(f"{cid}/ads", {"fields": "id,name,adset_id,adset{name},campaign{name},creative{id}",
                                               "limit": 200}):
                ads[a_["id"]] = {"name": a_.get("name") or "", "adset_id": str(a_.get("adset_id") or ""),
                                 "adset": (a_.get("adset") or {}).get("name") or "", "cid": cid,
                                 "camp": (a_.get("campaign") or {}).get("name") or "",
                                 "creative": str((a_.get("creative") or {}).get("id") or "")}
    # mint-time creative names from our own mint specs: a UTM {{ad.name}} can still carry them
    mint_alias = defaultdict(set)
    for f in Path("scripts/mint_specs").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        minted = d.get("_minted") or {}
        for v in d.get("videos", []):
            m = minted.get(v.get("key"))
            cr = m.get("creative_id") if isinstance(m, dict) else m
            if v.get("ad_name") and cr:
                mint_alias[cpa.ad_key(v["ad_name"])].add(str(cr))

    def resolve(x):
        cids = camp_ids.get(cpa.norm(x["camp"]), set())
        cand, how = [i for i, a_ in ads.items() if a_["cid"] in cids and cpa.ad_key(a_["name"]) == cpa.ad_key(x["ad"])], "ad name"
        if not cand:
            crs = mint_alias.get(cpa.ad_key(x["ad"]), set())
            cand, how = [i for i, a_ in ads.items() if a_["cid"] in cids and a_["creative"] in crs], "mint creative ID"
        if len(cand) > 1:
            c2 = [i for i in cand if cpa.norm(ads[i]["adset"]) == cpa.norm(x["adset"])]
            if len(c2) == 1:
                return c2[0], how + " + ad set"
            return None, f"AMBIGUOUS ({len(cand)} ads)"
        return (cand[0], how) if cand else (None, "UNMATCHED")

    for x in my:
        x["ad_id"], x["how"] = resolve(x)
        cids = camp_ids.get(cpa.norm(x["camp"]), set())
        x["cid"] = ads[x["ad_id"]]["cid"] if x["ad_id"] else (next(iter(cids)) if len(cids) == 1 else None)
        x["adset_id"] = ads[x["ad_id"]]["adset_id"] if x["ad_id"] else None

    spend_ad, leads_ad, parent, names = defaultdict(float), defaultdict(float), {}, {}
    reg_token = "offsite_conversion.fb_pixel_" + (s.meta.conversion_event or "").lower()
    for r in g.account_insights(s.meta.account_path, level="ad",
                                fields="ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,actions",
                                time_range={"since": w0.isoformat(), "until": today.isoformat()}):
        aid = str(r.get("ad_id"))
        spend_ad[aid] += float(r.get("spend") or 0)
        leads_ad[aid] += sum(float(a_.get("value") or 0) for a_ in (r.get("actions") or [])
                             if a_.get("action_type") == reg_token)
        parent[aid] = (str(r.get("campaign_id")), str(r.get("adset_id")))
        names[str(r.get("campaign_id"))] = r.get("campaign_name") or ""
        names[str(r.get("adset_id"))] = r.get("adset_name") or ""
        names[aid] = r.get("ad_name") or ""
    for i, a_ in ads.items():
        names.setdefault(i, a_["name"]); names.setdefault(a_["adset_id"], a_["adset"]); names.setdefault(a_["cid"], a_["camp"])
    print(f"Meta Leads = platform {reg_token} (Ads Manager Results). Register Leads = sheet rows attributed by "
          f"verified Campaign/Ad (by ID). The two are different counts.")

    # ── First-time vs Returning (normalised phone last 9 / e-mail, all earlier Register rows) ──
    hist = defaultdict(list)
    for x in recs:
        for k in x["ids"]:
            hist[k].append(x["date"])
    for x in my:
        x["fr"] = ("Unknown" if not x["ids"] else
                   "Returning" if any(d < x["date"] for k in x["ids"] for d in hist[k]) else "First-time")
    frc = Counter(x["fr"] for x in my)
    print(f"\n--- F. First-time vs Returning (eligible MY {len(my)}) — First-time {frc['First-time']} · "
          f"Returning {frc['Returning']} · Unknown {frc['Unknown']} (no usable phone / e-mail) ---")
    print("  (Returning = same phone or e-mail registered on an earlier date; names never used. "
          "Attribution and the valid-registration count are unchanged.)")
    for d in sorted({x["date"] for x in my}):
        L = [x for x in my if x["date"] == d]
        c_ = Counter(x["fr"] for x in L)
        print(f"  {d}  regs {len(L):>3} · first-time {c_['First-time']:>3} · returning {c_['Returning']:>3} · unknown {c_['Unknown']:>2}")

    # ── S. every scored MY row (sheet row number only — no personal data) ──────────
    scored_rows = [x for x in my if x["score"] is not None]
    print(f"\n--- S. scored MY rows ({len(scored_rows)}) — U (GHL) is the official score; recomputed only to flag anomalies ---")
    print("  row    reg date    U  recalc check      Q1 Q2 Q3 Q4 Q5  ICP     lead        UTM(J/K/L)  attribution (how) · Meta ad")
    for x in sorted(scored_rows, key=lambda x: x["row"]):
        pts = x["pts"]
        unm = [f"Q{i + 1}" for i, p_ in enumerate(pts) if p_ == -1]
        rec = None if unm or any(p_ is None for p_ in pts) else sum(pts)
        chk = ("UNMAPPED " + ",".join(unm)) if unm else ("match" if rec == x["score"] else f"DIFF {rec - x['score']:+.0f}")
        utm = "complete" if x["camp"] and x["adset"] and x["ad"] else "MISSING"
        att = f"{x['how']} · {names.get(x['ad_id'], '')} ({x['ad_id']})" if x["ad_id"] else x["how"]
        aset_note = ""
        if x["ad_id"] and cpa.norm(ads[x["ad_id"]]["adset"]) != cpa.norm(x["adset"]):
            aset_note = f" · UTM ad set '{x['adset']}' ≠ Meta '{ads[x['ad_id']]['adset']}'"
        print(f"  {x['row']:<6} {x['date']}  {x['score']:>3.0f} {('—' if rec is None else f'{rec:.0f}'):>6} {chk:<12} "
              + " ".join(f"{('?' if p_ == -1 else '-' if p_ is None else p_):>2}" for p_ in pts)
              + f"  {icp_state(x):<6}  {x['fr']:<10}  {utm:<10}  {att}{aset_note}")
    dup = Counter(x["pid"] for x in my)
    print(f"  duplicate registrations among eligible MY rows: {sum(n - 1 for n in dup.values() if n > 1)} extra rows · "
          f"scored rows that are duplicates: {sum(1 for x in scored_rows if dup[x['pid']] > 1)}")

    def table(title, level):
        grp = defaultdict(list)
        for x in my:
            if level == "campaign":
                k = x["cid"] or ("UNMATCHED", x["camp"])
            elif level == "ad set":
                k = x["adset_id"] or ("UNMATCHED", x["camp"][-40:], x["adset"])
            else:
                k = x["ad_id"] or ("UNMATCHED", x["camp"][-40:], x["ad"])
            grp[k].append(x)
        print(f"\n--- {title} (matched by Meta ID) ---")
        print("  MetaCPL = spend ÷ Meta Leads · RegCPL = spend ÷ matched Register Leads · CPQL = spend ÷ Qualified "
              "Register Leads (≥70) · UNMATCHED = N/A, never RM0 · reliability is informational only")
        print(f"  {'reliability':<17} {'spend':>6} {'Mlead':>5} {'MetaCPL':>7} {'Reg':>3} {'RegCPL':>6} {'new':>3} {'ret':>3} "
              f"{'done':>4} {'pend':>4} {'A':>2} {'B':>2} {'C':>2} {'D':>2} {'QL':>2} {'ICP':>3} {'rev':>3} {'CPQL':>6}  {level}")
        for k, L in sorted(grp.items(), key=lambda kv: (isinstance(kv[0], tuple), -len(kv[1]))):
            S = [x for x in L if x["score"] is not None]
            gcn = Counter(grade(x["score"]) for x in S)
            h = [x for x in S if x["score"] >= 70]
            icp = sum(1 for x in h if icp_state(x) == "yes")
            rev = sum(1 for x in h if icp_state(x) == "REVIEW")
            if isinstance(k, tuple):
                sp = ml = None
                label = "UNMATCHED · " + " · ".join(k[1:])
            else:
                aids = [a_ for a_, (c_, s_) in parent.items()
                        if (c_ if level == "campaign" else s_ if level == "ad set" else a_) == k]
                sp = sum(spend_ad[a_] for a_ in aids)
                ml = sum(leads_ad[a_] for a_ in aids)
                label = f"{names.get(k, '')} ({k})"
            f0 = lambda v: "N/A" if v is None else f"{v:,.0f}"
            mcpl = "N/A" if sp is None else (f"{sp / ml:.0f}" if ml else "—")
            rcpl = "N/A" if sp is None else f"{sp / len(L):.0f}"
            cpql = "N/A" if sp is None or not h else f"{sp / len(h):,.0f}"
            thin = len(L) < MIN_ELIGIBLE or len(S) < MIN_SCORED
            st = (("PRELIMINARY" if h else "INSUFFICIENT DATA") if thin else "WATCH" if not h else "OK")
            if sp is None:
                st = "UNMATCHED"
            fr_ = Counter(x["fr"] for x in L)
            print(f"  {st:<17} {f0(sp):>6} {f0(ml):>5} {mcpl:>7} {len(L):>3} {rcpl:>6} {fr_['First-time']:>3} {fr_['Returning']:>3} "
                  f"{sum(x['complete'] for x in L):>4} {len(L) - len(S):>4} {gcn['A']:>2} {gcn['B']:>2} {gcn['C']:>2} {gcn['D']:>2} "
                  f"{len(h):>2} {icp:>3} {rev:>3} {cpql:>6}  {label[:120]}")

    table("C/D-1. by MY campaign", "campaign")
    table("C/D-2. by MY ad set", "ad set")
    table("C/D-3. by MY ad", "ad")
    um = [x for x in my if not x["ad_id"]]
    print(f"\n  Register rows whose ad could not be matched to a Meta ad ID: {len(um)}")
    for (c_, a_, how), n in Counter((x["camp"][-45:], x["ad"], x["how"]) for x in um).most_common():
        print(f"    {n:>3}  {how:<14} {c_} › {a_}")

    # ── E. attribution ─────────────────────────────────────────────────────────
    print("\n--- E. attribution in window ---")
    tot = len(win)
    for k, n in mkc.most_common():
        print(f"  {k:<7} {n:>5}  {pct(n, tot)}")
    print("  MY-tag (has [MY]/MARTIN-MY but campaign not found in the MY ad account) — campaigns:")
    for c, n in Counter(x["camp"] for x in win if x["mk"] == "MY-tag").most_common(10):
        print(f"    {n:>4}  {c[:90]}")
    print("  'other' UTM campaigns (not MY-verified, not SG/US) — top:")
    for c, n in Counter(x["camp"] for x in win if x["mk"] == "other").most_common(10):
        print(f"    {n:>4}  {c[:90] if safe_cat(c) else '(unprintable)'}")
    print("  no-UTM rows by Source (H):")
    for c, n in Counter(x["source"] for x in win if x["mk"] == "no-UTM").most_common(8):
        print(f"    {n:>4}  {c[:60] if safe_cat(c) else '(unprintable)'}")
    my_noad = sum(1 for x in my if not x["ad"])
    my_noadset = sum(1 for x in my if not x["adset"])
    print(f"  MY rows missing ad set (K): {my_noadset} · missing ad name (L): {my_noad}")
    # ── UTM retention after the survey (rows that registered WITH a real MY ad UTM) ──
    srv = [x for x in my_all if x["surveyed"]]
    keep = [x for x in srv if x["camp"] and x["adset"] and x["ad"]]
    print(f"\n--- UTM retention: MY-UTM registrants who answered the survey: {len(srv)} · still carry "
          f"campaign + ad set + ad: {len(keep)} ---")
    nou = [x for x in win if x["mk"] == "no-UTM" and x["surveyed"]]
    print(f"  surveyed rows with no UTM: {len(nou)} — reported as a fact; they may simply be non-ad / "
          f"group sign-ups. Not evidence that the survey drops UTMs.")

    # ── CPQL: calculable vs decision-reliable (kept apart) ────────────────────
    calc = [
        ("survey launch date known", bool(launch)),
        (f"spend currency confirmed ({cur})", isinstance(cur, str) and len(cur) == 3),
        (f"at least one MY qualified lead (≥70) with matched spend (QL {len(hs)})", bool(hs)),
    ]
    rel = [
        (f"≥{CPQL_MIN_SCORED} scored eligible MY leads (have {len(sc_my)})", len(sc_my) >= CPQL_MIN_SCORED),
        (f"≥{CPQL_MIN_UTM_CHECK} surveyed MY-UTM rows confirm UTMs survive (have {len(srv)}, kept {len(keep)})",
         len(srv) >= CPQL_MIN_UTM_CHECK and len(keep) == len(srv)),
        (f"no cohort in the window still immature (<{IMMATURE_DAYS}d)",
         bool(byd) and all((today - d).days >= IMMATURE_DAYS for d in byd)),
    ]
    print("\n--- CPQL calculable? ---")
    for label, ok in calc:
        print(f"  [{'x' if ok else ' '}] {label}")
    print(f"  => {'CALCULABLE — Preliminary CPQL shown where QL > 0' if all(ok for _, ok in calc) else 'NOT YET CALCULABLE — CPQL N/A'}")
    print("--- CPQL decision-reliable? ---")
    for label, ok in rel:
        print(f"  [{'x' if ok else ' '}] {label}")
    print(f"  => {'RELIABLE enough to read' if all(ok for _, ok in rel) else 'PRELIMINARY / INSUFFICIENT DATA'}")
    print("  Survey score never triggers SCALE / REDUCE / PAUSE; the Ads Agent's CPL / CPA / ROAS rules are unchanged.")
    print("\nDONE (read-only · aggregates only)")


if __name__ == "__main__":
    main()
