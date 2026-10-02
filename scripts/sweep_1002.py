"""MY sweep (operator, 2 Oct): 30 Sep–2 Oct 窗口，超过 70 CPL 的、没有 lead 的，都关掉。

Every ACTIVE ad on the MY account, judged on the 9/30→10/2 window:
    CLOSE  — regs > 0 and CPL > RM70
    CLOSE  — regs = 0 and spend ≥ RM30 (真没给机会的小样本不杀：spend < RM30 的 0-reg
             ads are listed but left alone — say the word to kill those too)
    KEEP   — regs > 0 and CPL ≤ 70  ← 有成绩的（搬去 SG 的候选），each logged with its
             post id (effective_object_story_id) and ad set id for the SG build.
Ad level only; ids recorded in state/sweep_1002.json for undo. Idempotent.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from adbot.commands import graph_client
from adbot.logging import final_summary, get_logger
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import load_settings

SINCE, UNTIL = dt.date(2026, 9, 30), dt.date(2026, 10, 2)
CPL_LINE, ZERO_FLOOR = 70.0, 70.0      # operator correction: 0-lead kill needs >= RM70 spend
STATE_PATH = Path("state") / "sweep_1002.json"


def main() -> None:
    log = get_logger()
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    token = result_action_type(s.meta.conversion_event)
    log.info("MY sweep on %s · 窗口 %s→%s · 关：CPL>%.0f 或 (0 lead 且花≥%.0f)",
             acct, SINCE, UNTIL, CPL_LINE, ZERO_FLOOR)

    perf = {}
    for r in g._get_all(f"{acct}/insights",
                        {"level": "ad", "limit": 500,
                         "fields": "ad_id,spend,actions",
                         "time_range": json.dumps({"since": SINCE.isoformat(),
                                                   "until": UNTIL.isoformat()})}):
        d = perf.setdefault(r.get("ad_id"), {"sp": 0.0, "ld": 0.0})
        try:
            d["sp"] += float(r.get("spend") or 0)
        except (TypeError, ValueError):
            pass
        d["ld"] += extract_results(r.get("actions"), token)

    st = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"closed": {}}

    # reconcile: run #1 used a RM30 zero-lead floor — reopen anything it closed that
    # the corrected rule (CPL>70, or 0 lead on >= RM70) would NOT close.
    for ad_id in list(st["closed"]):
        p = perf.get(ad_id, {"sp": 0.0, "ld": 0.0})
        sp, ld = p["sp"], p["ld"]
        should_close = (ld and sp / ld > CPL_LINE) or (not ld and sp >= ZERO_FLOOR)
        if not should_close:
            g._request("POST", ad_id, data={"status": "ACTIVE"})
            log.info("↩ 重开（修正线 RM70）: %s · %s", ad_id, st["closed"].pop(ad_id))
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2))
            time.sleep(0.5)

    keep, small = [], []
    for a in g._get_all(f"{acct}/ads",
                        {"fields": "id,name,status,effective_status,adset_id,"
                                   "creative{effective_object_story_id}", "limit": 500}):
        if a.get("status") != "ACTIVE" or a.get("effective_status") not in (
                "ACTIVE", "LEARNING"):
            continue
        p = perf.get(a["id"], {"sp": 0.0, "ld": 0.0})
        sp, ld = p["sp"], p["ld"]
        cpl = sp / ld if ld else None
        name = (a.get("name") or "").strip()
        if (ld and cpl > CPL_LINE) or (not ld and sp >= ZERO_FLOOR):
            g._request("POST", a["id"], data={"status": "PAUSED"})
            st["closed"][a["id"]] = f"{name} · 窗口 RM{sp:.0f}/{int(ld)}L"
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2))
            log.info("⏸ CLOSE %-46s 窗口 花 RM%-7.2f %dL %s", name[:46], sp, int(ld),
                     f"CPL RM{cpl:,.0f}" if ld else "零lead")
            time.sleep(0.5)
        elif ld:
            post = (a.get("creative") or {}).get("effective_object_story_id")
            keep.append((cpl, name, sp, int(ld), post, a.get("adset_id")))
        else:
            small.append((name, sp))

    log.info("═" * 108)
    log.info("✅ 有成绩的（CPL ≤ %.0f · 搬 SG 候选）", CPL_LINE)
    for cpl, name, sp, ld, post, aset in sorted(keep):
        log.info("  ▸ %-46s 窗口 花 RM%-7.2f %dL CPL RM%-5.0f · post %s · adset %s",
                 name[:46], sp, ld, cpl, post, aset)
    if small:
        log.info("⏳ 0 lead 但花 < RM%.0f 未动（%d 支）: %s", ZERO_FLOOR, len(small),
                 ", ".join(f"{n[:22]}(RM{sp:.0f})" for n, sp in small))
    final_summary(log, f"MY sweep: closed {len(st['closed'])}, keepers {len(keep)}, "
                       f"small-untouched {len(small)}.")


if __name__ == "__main__":
    main()
