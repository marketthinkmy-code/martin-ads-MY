"""Read-only: how old are the kids of PAYING Malaysian parents, versus the kids of everyone who registered?

Operator 11/21: the US team says most paying parents have kids aged 10+; check MY. Reads the Paid
Student List and the Register tab, keeps Malaysian rows (phone prefix 60 / leading 0, else the
[MY] campaign tag), buckets the kid-age answers and prints counts only. Personal data (names,
e-mails, phones) is used in memory to tell market and to drop blank template rows; nothing
personal is printed.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

BUCKETS = ["3-5", "6-8", "9-11", "12-14", "15-17", "18+"]


def market(phone: str, campaign: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("60") or (d.startswith("0") and len(d) >= 9):
        return "MY"
    if (d.startswith("65") and len(d) >= 10) or (len(d) == 8 and d[0] in "3689"):
        return "SG"
    c = (campaign or "").lower()
    if "[sg]" in c or "martin-sg" in c:
        return "SG"
    if "[my]" in c or "martin-my" in c:
        return "MY"
    return "?"


def bucket(raw: str) -> str | None:
    """Map an age answer ('12-14 岁', '9-11 歲', '10', '13岁', '16 岁以上') to a bucket."""
    t = (raw or "").strip()
    if not t:
        return None
    nums = [int(n) for n in re.findall(r"\d+", t)]
    if not nums:
        return None
    lo = nums[0]
    if "以上" in t and lo >= 16:
        return "18+" if lo >= 18 else "15-17"
    for b in BUCKETS[:-1]:
        a, z = (int(x) for x in b.split("-"))
        if a <= lo <= z:
            return b
    return "18+" if lo >= 18 else ("3-5" if lo < 3 else None)


def col(header, *needles):
    for i, h in enumerate(header):
        k = (h or "").strip().lower()
        if any(n.lower() in k for n in needles):
            return i
    return -1


def table(title, cnt: Counter, total_rows: int) -> None:
    n = sum(cnt.values())
    print(f"\n-- {title}: {n} with an age answer (of {total_rows} rows)")
    if not n:
        return
    for b in BUCKETS:
        v = cnt.get(b, 0)
        print(f"   {b:>6} 岁  {v:4d}  {100 * v / n:5.1f}%  {'█' * round(40 * v / n)}")
    ten = cnt.get("12-14", 0) + cnt.get("15-17", 0) + cnt.get("18+", 0)
    nine = cnt.get("9-11", 0)
    print(f"   12 岁以上: {ten} ({100 * ten / n:.0f}%) · 9-11 岁: {nine} ({100 * nine / n:.0f}%) · "
          f"8 岁以下: {n - ten - nine} ({100 * (n - ten - nine) / n:.0f}%)")


def main() -> None:
    s = load_settings()
    sh = SheetsClient(s.secrets.google_sa_json)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    sid = s.cpa.spreadsheet_id

    # ── paid parents ────────────────────────────────────────────────────────
    v = sh.read_tab(sid, s.cpa.sales_tab)
    hi = next((i for i, r in enumerate(v[:10]) if sum(1 for c in r if c.strip()) >= 3), 0)
    hdr = v[hi]
    c_date = cpa.find_columns(hdr).get("date", -1)
    c_camp = cpa.find_columns(hdr).get("campaign", -1)
    c_kids = col(hdr, "kids age")
    c_surv = col(hdr, "孩子几岁", "孩子幾歲")
    c_sex = col(hdr, "男孩还是女孩", "男孩還是女孩")
    c_worry = col(hdr, "最担心", "最擔心")
    c_mail = col(hdr, "email")
    c_phone = col(hdr, "whatsapp", "號碼", "号码", "phone")
    print(f"=== PAID PARENTS · KID AGE · today MYT {today} (read-only, counts only) ===")
    print(f"paid tab '{s.cpa.sales_tab}': columns date={c_date} campaign={c_camp} kids_age={c_kids} "
          f"survey_age={c_surv} gender={c_sex} worry={c_worry}")
    pv = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()

    rows = []
    for r in v[hi + 1:]:
        mail, phone = pv(r, c_mail), pv(r, c_phone)
        if "@" not in mail and len(re.sub(r"\D", "", phone)) < 8:
            continue                                   # blank / template row
        age = bucket(pv(r, c_surv)) or bucket(pv(r, c_kids))
        rows.append({"d": cpa.parse_date(pv(r, c_date)), "mk": market(phone, pv(r, c_camp)), "age": age,
                     "sex": pv(r, c_sex), "worry": pv(r, c_worry)})
    mk = Counter(x["mk"] for x in rows)
    print(f"paid rows with a buyer: {len(rows)} · market {dict(mk)}")

    my = [x for x in rows if x["mk"] == "MY"]
    dated = [x for x in my if x["d"]]
    first_age = min((x["d"] for x in dated if x["age"]), default=None)
    print(f"MY paid rows: {len(my)} · first MY row with an age answer: {first_age}")
    table("MY 付费家长 · 全部", Counter(x["age"] for x in my if x["age"]), len(my))
    for days in (180, 90):
        cut = today - dt.timedelta(days=days)
        sub = [x for x in dated if x["d"] > cut]
        table(f"MY 付费家长 · 最近 {days} 天", Counter(x["age"] for x in sub if x["age"]), len(sub))
    sg = [x for x in rows if x["mk"] == "SG"]
    table("SG 付费家长 · 全部（对照）", Counter(x["age"] for x in sg if x["age"]), len(sg))

    print("\n-- MY 付费家长 · 孩子性别")
    for k, n in Counter(x["sex"] for x in my if x["sex"]).most_common(4):
        print(f"   {n:4d}  {k[:20]}")
    print("-- MY 付费家长 · 最担心")
    for k, n in Counter(x["worry"] for x in my if x["worry"]).most_common(6):
        print(f"   {n:4d}  {k[:30]}")
    print("-- MY 付费家长 · 年龄 × 最担心（有两题都答的）")
    cross = Counter((x["age"], x["worry"][:14]) for x in my if x["age"] and x["worry"])
    for (a, w), n in sorted(cross.items(), key=lambda kv: (BUCKETS.index(kv[0][0]), -kv[1])):
        print(f"   {a:>6} 岁 · {w:<16} {n}")

    # ── registrants (the denominator) ───────────────────────────────────────
    reg = sh.read_tab(sid, "Register")
    rh = reg[0] if reg else []
    print(f"\n=== REGISTER header ({len(rh)} cols): " + " | ".join(f"{i}:{(h or '')[:18]}" for i, h in enumerate(rh)))
    r_age = col(rh, "孩子几岁", "孩子幾歲", "kids age", "年龄", "年齡")
    r_phone, r_date, r_camp = 1, 6, 9
    if r_age < 0:
        print("Register has no kid-age column — conversion by age cannot be computed.")
        return
    regs = []
    for r in reg[1:]:
        regs.append({"d": cpa.parse_date(pv(r, r_date)), "mk": market(pv(r, r_phone), pv(r, r_camp)),
                     "age": bucket(pv(r, r_age))})
    my_r = [x for x in regs if x["mk"] == "MY"]
    first_r = min((x["d"] for x in my_r if x["d"] and x["age"]), default=None)
    print(f"Register age column {r_age} · MY registrants {len(my_r)} · first MY row with an age answer {first_r}")
    start = max(d for d in (first_r, first_age) if d) if (first_r and first_age) else None
    if not start:
        return
    reg_c = Counter(x["age"] for x in my_r if x["d"] and x["d"] >= start and x["age"])
    pay_c = Counter(x["age"] for x in dated if x["d"] >= start and x["age"])
    table(f"MY 报名家长 · 自 {start} 起", reg_c, sum(1 for x in my_r if x["d"] and x["d"] >= start))
    table(f"MY 付费家长 · 自 {start} 起", pay_c, sum(1 for x in dated if x["d"] >= start))
    print(f"\n-- MY 每个年龄的成交率（自 {start} 起，付费 ÷ 有答年龄的报名）")
    for b in BUCKETS:
        rn, pn = reg_c.get(b, 0), pay_c.get(b, 0)
        rate = f"{100 * pn / rn:.1f}%" if rn else "—"
        print(f"   {b:>6} 岁  报名 {rn:5d}  付费 {pn:4d}  成交率 {rate}")


if __name__ == "__main__":
    main()
