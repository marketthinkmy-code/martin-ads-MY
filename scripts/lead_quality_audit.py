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
import os
import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Dict, List, Optional

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.commands import graph_client
from adbot.settings import load_settings

COL = {"date": 6, "source": 7, "campaign": 9, "adset": 10, "ad": 11,
       "q1": 15, "q2": 16, "q3": 17, "q4": 18, "q5": 19, "score": 20}
PII_COLS = (0, 1, 2)   # Name, Phone Number, Email — read only for the test check

Q2_MAP = [(("身高记录", "成长变慢", "成長變慢", "身高記錄", "记录孩子身高", "記錄孩子身高",
            "长高速度明显变慢", "長高速度明顯變慢"), 25), (("成长评估", "成長評估"), 25),
          (("比同龄", "比同齡"), 20), (("不确定", "不確定"), 12), (("提前学习", "提前學習"), 5)]
Q5_MAP = [(("专业指导", "持续跟进", "專業指導", "持續跟進"), 20), (("系统性", "按步骤", "系統性", "按步驟"), 16),
          (("先了解", "先瞭解"), 10), (("免费资讯", "免費資訊", "免费的成长知识", "免費的成長知識", "免费", "免費"), 4)]


def map_score(ans: str, table) -> Optional[int]:
    a = (ans or "").strip()
    if not a:
        return None
    for keys, pts in table:
        if any(k in a for k in keys):
            return pts
    return -1   # answered, but the text matches no known option


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
    for c in g.list_campaigns(s.meta.account_path):
        my_names.add(cpa.norm(c.get("name") or ""))
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
        else:
            mk = market_tag(camp)
        answers = [cell(r, q) for q in ("q1", "q2", "q3", "q4", "q5")]
        sc = num(cell(r, "score"))
        recs.append({"row": ri, "pid": person_key(r) or f"row:{len(recs)}", "date": d, "mk": mk, "camp": camp, "adset": cell(r, "adset"), "ad": cell(r, "ad"),
                     "source": cell(r, "source"), "answers": answers,
                     "surveyed": any(answers), "complete": all(answers), "score": sc,
                     "q2": map_score(answers[1], Q2_MAP), "q5": map_score(answers[4], Q5_MAP)})

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
        icp = sum(1 for x in qlL if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16)
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
    core = [x for x in hs if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16]
    done_my = sum(x["complete"] for x in my)
    print(f"  High Score / Qualified (≥70): {len(hs)} · QL ÷ valid regs {pct(len(hs), len(my))} · "
          f"QL ÷ completed surveys {pct(len(hs), done_my)} ({done_my} completed) — different denominators")
    print(f"  Score Coverage Rate (scored ÷ valid regs): {pct(len(sc_my), len(my))}")
    print(f"  Core ICP (≥70 + Q2≥20 + Q5≥16 from raw answers): {len(core)} ({pct(len(core), len(my))} of eligible regs)")
    print(f"  ≥70 but NOT Core ICP: {len(hs) - len(core)}  (Q2<20: {sum(1 for x in hs if (x['q2'] or 0) < 20)} · "
          f"Q5<16: {sum(1 for x in hs if (x['q5'] or 0) < 16)})")
    if sc_my:
        print(f"  mean score {mean(x['score'] for x in sc_my):.1f}")

    print("\n  Q2 / Q5 answer categories seen in MY (raw text → mapped score; ? = no known option):")
    for q, key, idx in (("Q2", "q2", 1), ("Q5", "q5", 4)):
        cats = Counter((x["answers"][idx], x[key]) for x in my if x["answers"][idx])
        for (ans, pts), n in cats.most_common(12):
            if safe_cat(ans):
                print(f"    {q} {n:>4} × [{'?' if pts == -1 else pts}] {ans[:60]}")
        unk = sum(n for (a, p), n in cats.items() if p == -1)
        print(f"    {q} answers matching no known option: {unk}")

    # ── C/D. by campaign / ad set / ad with spend ──────────────────────────────
    try:
        cur = g.get_object(s.meta.account_path, "currency,name").get("currency")
    except Exception as exc:  # noqa: BLE001
        cur = f"? ({str(exc)[:60]})"
    print(f"\nMeta spend currency — MY account {s.meta.account_path}: {cur} (all spend figures below are in it; SG never mixed)")
    print(f"spend window = eligible registration window: {max(since, launch or since)}..{today}")
    spend, mleads = defaultdict(float), defaultdict(float)
    reg_token = "offsite_conversion.fb_pixel_" + (s.meta.conversion_event or "").lower()
    for r in g.account_insights(s.meta.account_path, level="ad",
                                fields="campaign_name,adset_name,ad_name,spend,actions",
                                time_range={"since": max(since, launch or since).isoformat(), "until": today.isoformat()}):
        k = (cpa.norm(r.get("campaign_name") or ""), cpa.ad_key(r.get("ad_name") or ""),
             cpa.norm(r.get("adset_name") or ""))
        spend[k] += float(r.get("spend") or 0)
        mleads[k] += sum(float(a.get("value") or 0) for a in (r.get("actions") or [])
                         if a.get("action_type") == reg_token)
    print(f"Meta 'Leads' = platform {reg_token} (Ads Manager Results) — NOT the same as Register rows attributed by UTM")

    # ── S. every scored MY row, row-level (sheet row number only — no personal data) ──
    def ad_verified(x):
        return any(kk[0] == cpa.norm(x["camp"]) and kk[1] == cpa.ad_key(x["ad"]) for kk in spend)
    scored_rows = [x for x in my if x["score"] is not None]
    print(f"\n--- S. scored MY rows in window ({len(scored_rows)}) — score as read from U (GHL), not recomputed ---")
    print("  row    reg date    score grade Q2pts Q5pts ICP  all5 UTM(J/K/L) ad-in-Meta  campaign › ad set › ad")
    for x in sorted(scored_rows, key=lambda x: x["row"]):
        icp = x["score"] >= 70 and (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16
        utm = "complete" if x["camp"] and x["adset"] and x["ad"] else "MISSING"
        q2 = "?" if x["q2"] == -1 else x["q2"]
        q5 = "?" if x["q5"] == -1 else x["q5"]
        print(f"  {x['row']:<6} {x['date']}  {x['score']:>5.0f} {grade(x['score']):>5} {str(q2):>5} {str(q5):>5} "
              f"{'yes' if icp else 'no':>3}  {'yes' if x['complete'] else 'no':>4} {utm:<10} "
              f"{'yes' if ad_verified(x) else 'NO':<10}  {x['camp'][-40:]} › {x['adset']} › {x['ad']}")
    dup = Counter(x["pid"] for x in my)
    print(f"  duplicate registrations among eligible MY rows: {sum(n - 1 for n in dup.values() if n > 1)} extra rows "
          f"across {sum(1 for n in dup.values() if n > 1)} persons · scored rows that are duplicates: "
          f"{sum(1 for x in scored_rows if dup[x['pid']] > 1)}")

    def table(title, keyf, label):
        grp = defaultdict(list)
        for x in my:
            grp[keyf(x)].append(x)
        print(f"\n--- {title} ---")
        print("  CPQL = Preliminary CPQL (matched spend ÷ QL, same scope & dates); N/A when QL = 0. "
              "Reliability: INSUFFICIENT DATA / PRELIMINARY / WATCH / OK — informational only.")
        print("  regs/pers = Register rows / distinct persons attributed by UTM · Mleads = Meta platform registrations · "
              "CPL = spend ÷ Mleads · done = all 5 answered")
        print(f"  {'reliability':<17} {'spend':>6} {'Mleads':>6} {'CPL':>4} {'regs':>4} {'pers':>4} {'done':>4} {'pend':>4} {'scored':>6} {'avg':>4} {'A':>2} {'B':>2} {'C':>2} {'D':>2} {'QL':>3} {'ICP':>3} {'CPQL':>6} {'RM/ICP':>6}  {label}")
        for k, L in sorted(grp.items(), key=lambda kv: -len(kv[1])):
            S = [x for x in L if x["score"] is not None]
            gcn = Counter(grade(x["score"]) for x in S)
            h = [x for x in S if x["score"] >= 70]
            c = [x for x in h if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16]
            match = lambda kk: (kk[0] == cpa.norm(L[0]["camp"]) and
                                (label != "ad" or kk[1] == cpa.ad_key(L[0]["ad"])) and
                                (label != "ad set" or kk[2] == cpa.norm(L[0]["adset"])))
            sp = sum(v for kk, v in spend.items() if match(kk))
            ml = sum(v for kk, v in mleads.items() if match(kk))
            cpl = f"{sp / ml:.0f}" if ml else "—"
            avg = f"{mean(x['score'] for x in S):.0f}" if S else "—"
            rhs = f"{sp / len(h):,.0f}" if h and sp else "N/A"
            ric = f"{sp / len(c):,.0f}" if c and sp else "N/A"
            thin = len(L) < MIN_ELIGIBLE or len(S) < MIN_SCORED
            st = (("PRELIMINARY" if h else "INSUFFICIENT DATA") if thin
                  else "WATCH" if not h else "OK")
            print(f"  {st:<17} {sp:>6,.0f} {ml:>6.0f} {cpl:>4} {len(L):>4} {len({x['pid'] for x in L}):>4} "
                  f"{sum(x['complete'] for x in L):>4} {len(L) - len(S):>4} {len(S):>6} {avg:>4} {gcn['A']:>2} {gcn['B']:>2} {gcn['C']:>2} {gcn['D']:>2} "
                  f"{len(h):>3} {len(c):>3} {rhs:>6} {ric:>6}  {' · '.join(k)[:110]}")

    table("C/D-1. by MY campaign", lambda x: (x["camp"],), "campaign")
    table("C/D-2. by MY campaign › ad set", lambda x: (x["camp"][-45:], x["adset"]), "ad set")
    table("C/D-3. by MY campaign › ad", lambda x: (x["camp"][-45:], x["ad"]), "ad")

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
