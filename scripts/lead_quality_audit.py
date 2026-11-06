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

Env:
  ADBOT_SINCE   first registration date to analyse (default: first date with a scored row)
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

Q2_MAP = [(("身高记录", "成长变慢", "成長變慢", "身高記錄"), 25), (("成长评估", "成長評估"), 25),
          (("比同龄", "比同齡"), 20), (("不确定", "不確定"), 12), (("提前学习", "提前學習"), 5)]
Q5_MAP = [(("专业指导", "持续跟进", "專業指導", "持續跟進"), 20), (("系统性", "按步骤", "系統性", "按步驟"), 16),
          (("先了解", "先瞭解"), 10), (("免费资讯", "免費資訊", "免费资讯"), 4)]


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


def pct(a, b) -> str:
    return f"{a / b:.0%}" if b else "—"


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

    cell = lambda r, k: (r[COL[k]] if COL[k] < len(r) else "").strip()
    recs, tests, undated = [], 0, 0
    test_by = Counter()
    for r in rows:
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
        else:
            mk = market_tag(camp)
        answers = [cell(r, q) for q in ("q1", "q2", "q3", "q4", "q5")]
        sc = num(cell(r, "score"))
        recs.append({"date": d, "mk": mk, "camp": camp, "adset": cell(r, "adset"), "ad": cell(r, "ad"),
                     "source": cell(r, "source"), "answers": answers,
                     "surveyed": any(answers), "complete": all(answers), "score": sc,
                     "q2": map_score(answers[1], Q2_MAP), "q5": map_score(answers[4], Q5_MAP)})

    scored_dates = Counter(x["date"] for x in recs if x["score"] is not None)
    first = min(scored_dates) if scored_dates else None
    print(f"rows with a GHL score (U), all markets: {sum(scored_dates.values())} · "
          f"with any survey answer (P–T): {sum(1 for x in recs if x['surveyed'])}")
    print("  scored rows per registration date: " + ", ".join(f"{d:%m-%d} {n}" for d, n in sorted(scored_dates.items())))
    since = dt.date.fromisoformat(os.environ["ADBOT_SINCE"]) if os.environ.get("ADBOT_SINCE") else first
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    print(f"excluded internal tests: {tests} {dict(test_by)} · undated rows: {undated}")
    print(f"first registration date with a GHL score: {first} → analysis window {since}..{today}\n")
    if not since:
        print("no scored rows found — stop")
        return
    win = [x for x in recs if x["date"] >= since]

    # ── A. by market + daily cohort ────────────────────────────────────────────
    print("--- A. registrations in window by market (MY = campaign verified in the MY ad account) ---")
    mkc = Counter(x["mk"] for x in win)
    for k in ("MY", "MY-tag", "SG", "US", "other", "no-UTM"):
        sub = [x for x in win if x["mk"] == k]
        print(f"  {k:<7} regs {len(sub):>5} · survey any {sum(x['surveyed'] for x in sub):>4} · all 5 answered "
              f"{sum(x['complete'] for x in sub):>4} · score present {sum(x['score'] is not None for x in sub):>4}")
    my = [x for x in win if x["mk"] == "MY"]
    print("\n  MY daily cohort (same registration date in numerator and denominator):")
    print(f"  {'date':<11} {'regs':>5} {'survey':>6} {'rate':>5} {'score':>6} {'rate':>5}")
    byd = defaultdict(list)
    for x in my:
        byd[x["date"]].append(x)
    for d in sorted(byd):
        L = byd[d]
        sv, sc = sum(x["surveyed"] for x in L), sum(x["score"] is not None for x in L)
        print(f"  {d}  {len(L):>5} {sv:>6} {pct(sv, len(L)):>5} {sc:>6} {pct(sc, len(L)):>5}")
    miss = [x for x in my if x["surveyed"] and x["score"] is None]
    print(f"\n  MY rows with survey answers but NO score (U empty): {len(miss)}")
    print(f"  MY rows with a score but no survey answers: {sum(1 for x in my if not x['surveyed'] and x['score'] is not None)}")
    bad = [x for x in my if x["score"] is not None and not (0 <= x["score"] <= 100)]
    print(f"  MY scores outside 0–100: {len(bad)}")

    # ── B. distribution ────────────────────────────────────────────────────────
    sc_my = [x for x in my if x["score"] is not None]
    print(f"\n--- B. MY score distribution (n={len(sc_my)} scored) ---")
    gc = Counter(grade(x["score"]) for x in sc_my)
    for gname in "ABCD":
        print(f"  {gname}: {gc[gname]:>4}  {pct(gc[gname], len(sc_my))}")
    hs = [x for x in sc_my if x["score"] >= 70]
    core = [x for x in hs if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16]
    print(f"  High Score (≥70): {len(hs)} ({pct(len(hs), len(sc_my))})")
    print(f"  Core ICP (≥70 + Q2≥20 + Q5≥16 from raw answers): {len(core)} ({pct(len(core), len(sc_my))})")
    print(f"  ≥70 but NOT Core ICP: {len(hs) - len(core)}  (Q2<20: {sum(1 for x in hs if (x['q2'] or 0) < 20)} · "
          f"Q5<16: {sum(1 for x in hs if (x['q5'] or 0) < 16)})")
    if sc_my:
        print(f"  mean score {mean(x['score'] for x in sc_my):.1f}")

    print("\n  Q2 / Q5 answer categories seen in MY (raw text → mapped score; ? = no known option):")
    for q, key, idx in (("Q2", "q2", 1), ("Q5", "q5", 4)):
        cats = Counter((x["answers"][idx], x[key]) for x in my if x["answers"][idx])
        for (ans, pts), n in cats.most_common(12):
            if n >= 2 and safe_cat(ans):
                print(f"    {q} {n:>4} × [{'?' if pts == -1 else pts}] {ans[:60]}")
        unk = sum(n for (a, p), n in cats.items() if p == -1)
        print(f"    {q} answers matching no known option: {unk}")

    # ── C/D. by campaign / ad set / ad with spend ──────────────────────────────
    spend = defaultdict(float)
    for r in g.account_insights(s.meta.account_path, level="ad",
                                fields="campaign_name,adset_name,ad_name,spend",
                                time_range={"since": since.isoformat(), "until": today.isoformat()}):
        k = (cpa.norm(r.get("campaign_name") or ""), cpa.ad_key(r.get("ad_name") or ""))
        spend[k] += float(r.get("spend") or 0)

    def table(title, keyf, label):
        grp = defaultdict(list)
        for x in my:
            grp[keyf(x)].append(x)
        print(f"\n--- {title} ---")
        print(f"  {'regs':>4} {'scored':>6} {'avg':>5} {'A':>3} {'B':>3} {'C':>3} {'D':>3} {'HS':>3} {'HS%':>4} {'ICP':>3} {'ICP%':>4} {'spend':>7} {'RM/HS':>6} {'RM/ICP':>6}  {label}")
        for k, L in sorted(grp.items(), key=lambda kv: -len(kv[1])):
            S = [x for x in L if x["score"] is not None]
            gcn = Counter(grade(x["score"]) for x in S)
            h = [x for x in S if x["score"] >= 70]
            c = [x for x in h if (x["q2"] or 0) >= 20 and (x["q5"] or 0) >= 16]
            sp = sum(v for kk, v in spend.items() if kk[0] == cpa.norm(L[0]["camp"]) and
                     (label != "ad" or kk[1] == cpa.ad_key(L[0]["ad"]))) if label != "ad set" else None
            avg = f"{mean(x['score'] for x in S):.0f}" if S else "—"
            rhs = f"{sp / len(h):,.0f}" if sp and h else "—"
            ric = f"{sp / len(c):,.0f}" if sp and c else "—"
            print(f"  {len(L):>4} {len(S):>6} {avg:>5} {gcn['A']:>3} {gcn['B']:>3} {gcn['C']:>3} {gcn['D']:>3} "
                  f"{len(h):>3} {pct(len(h), len(S)):>4} {len(c):>3} {pct(len(c), len(S)):>4} "
                  f"{(f'{sp:,.0f}' if sp is not None else '—'):>7} {rhs:>6} {ric:>6}  {' · '.join(k)[:110]}")

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
    print("\nDONE (read-only · aggregates only)")


if __name__ == "__main__":
    main()
