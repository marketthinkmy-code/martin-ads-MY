"""Audit the Monday reviewer's flags (read-only, 5 Oct).

The weekly Winner 换血 report flagged three paused Martin ads as "possibly
closed too early" by 30d CPL vs account median: Video 7 / Video 13 / 15岁以上.
For every ad on the MY, HK and SG accounts whose name matches, print status,
effective_status, last status change, 30d and since-Saturday spend/leads/CPL —
so each pause can be traced to the rule or decision that made it.

Second ask from the same report: before scaling the milk/bread series, confirm
the copy does not tie milk/bread to ADHD. Pull the message text of the series'
page posts and search for ADHD/过动/注意力/专注 wording.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from adbot.commands import graph_client
from adbot.logging import final_summary, get_logger
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

ACCOUNTS = [("MY", "act_1011719073600566"),
            ("HK", "act_1179668409969241"),
            ("SG老", "act_1024930575770087")]
NAME_PAT = re.compile(r"video\s*7|video\s*13|15\s*[歲岁]", re.IGNORECASE)
P = "341825319024143_"
MILK_POSTS = {
    "准备早餐面包": P + "122140406480485585",
    "我不会买牛奶": P + "122140405604485585",
    "牛奶+面包":   P + "122184069494485585",
}
HOOK2_AD = "120250093196030335"   # HK 麵包當早餐 — story id fetched live
ADHD_PAT = re.compile(r"adhd|过动|過動|多动|多動|注意力|专注|專注", re.IGNORECASE)


def main() -> None:
    log = get_logger()
    s = load_settings()
    g = graph_client(s)
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    d30 = today - dt.timedelta(days=30)
    sat = dt.date(2026, 10, 3)

    def pull(acct, since):
        sp, ld = {}, {}
        for r in g._get_all(f"{acct}/insights",
                            {"level": "ad", "limit": 500,
                             "fields": "ad_id,spend,actions",
                             "time_range": json.dumps({"since": since.isoformat(),
                                                       "until": today.isoformat()})}):
            k = r.get("ad_id")
            try:
                sp[k] = sp.get(k, 0.0) + float(r.get("spend") or 0)
            except (TypeError, ValueError):
                pass
            ld[k] = ld.get(k, 0.0) + extract_results(r.get("actions"), token)
        return sp, ld

    hits = 0
    for label, acct in ACCOUNTS:
        sp30, ld30 = pull(acct, d30)
        spw, ldw = pull(acct, sat)
        for a in g._get_all(f"{acct}/ads",
                            {"fields": "id,name,status,effective_status,updated_time,"
                                       "campaign{name}", "limit": 500}):
            nm = (a.get("name") or "").strip()
            if not NAME_PAT.search(nm):
                continue
            hits += 1
            aid = a["id"]
            s30, l30 = sp30.get(aid, 0.0), ld30.get(aid, 0.0)
            sw, lw = spw.get(aid, 0.0), ldw.get(aid, 0.0)
            c30 = f"CPL {s30 / l30:,.0f}" if l30 else "零L"
            cw = f"CPL {sw / lw:,.0f}" if lw else "零L"
            log.info("▸ 【%s】%-44s %s/%s · 最后改动 %s", label, nm[:44],
                     a.get("status"), a.get("effective_status"),
                     (a.get("updated_time") or "")[:10])
            log.info("    campaign %r · 30天 RM%.0f/%dL %s · 周六起 RM%.0f/%dL %s",
                     (a.get("campaign") or {}).get("name", "")[:46],
                     s30, int(l30), c30, sw, int(lw), cw)

    log.info("═" * 90)
    log.info("面包牛奶系列文案 ADHD 检查：")
    posts = dict(MILK_POSTS)
    try:
        st2 = g.get_object(HOOK2_AD, "creative{effective_object_story_id}")
        sid = ((st2.get("creative") or {}).get("effective_object_story_id"))
        if sid:
            posts["麵包當早餐 (Hook 2)"] = sid
    except Exception as exc:        # noqa: BLE001 — read-only best effort
        log.info("  (麵包當早餐 story id 拿不到: %s)", exc)
    flagged = []
    for name, pid in posts.items():
        try:
            msg = (g.get_object(pid, "message").get("message") or "")
        except Exception as exc:    # noqa: BLE001
            log.info("  ▸ %-18s 帖 %s 读取失败: %s", name, pid, exc)
            continue
        m = ADHD_PAT.findall(msg)
        if m:
            flagged.append(name)
            idx = ADHD_PAT.search(msg).start()
            log.info("  ⚠️ %-18s 命中 %s → …%s…", name, sorted(set(x.lower() for x in m)),
                     msg[max(0, idx - 60):idx + 80].replace("\n", " "))
        else:
            log.info("  ✅ %-18s 无 ADHD/过动/注意力/专注 字眼（全文 %d 字）", name, len(msg))

    final_summary(log, f"audit: 匹配广告 {hits} 支 · 文案命中 {flagged or '无'}")


if __name__ == "__main__":
    main()
