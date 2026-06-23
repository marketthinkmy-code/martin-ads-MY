"""Whole-account CPL report for an explicit date window (default: since last Thursday).
Read-only. Flags campaigns over the KPI ceiling so a human can decide on pauses.
Usage: python scripts/weekly_report.py [SINCE_YYYY-MM-DD] [UNTIL_YYYY-MM-DD]
"""
from __future__ import annotations

import sys

from adbot.commands import graph_client
from adbot.monitor_cpl import result_action_type, extract_results
from adbot.settings import load_settings

since = sys.argv[1] if len(sys.argv) > 1 else "2026-06-18"  # last Thursday
until = sys.argv[2] if len(sys.argv) > 2 else "2026-06-21"

s = load_settings()
g = graph_client(s)  # signs with META_APP_SECRET when set (matches the monitor)
acct = s.meta.account_path
reg_token = result_action_type(s.meta.conversion_event)
ceiling = s.kpi.cpl_threshold_myr

camps = g._get_all(f"{acct}/campaigns",
                   {"fields": "name,effective_status", "limit": 200})
active = [c for c in camps if c.get("effective_status") == "ACTIVE"]

rows = []
for c in active:
    ins = g._get_all(f"{c['id']}/insights",
                     {"fields": "spend,actions",
                      "time_range": '{"since":"%s","until":"%s"}' % (since, until), "limit": 1})
    spend = float(ins[0].get("spend", 0)) if ins else 0.0
    regs = extract_results(ins[0].get("actions") if ins else None, reg_token)
    cpl = (spend / regs) if regs else float("inf")
    rows.append((c["name"], spend, regs, cpl))

rows.sort(key=lambda r: -r[3])
tot_spend = sum(r[1] for r in rows)
tot_reg = sum(r[2] for r in rows)
print(f"WHOLE-ACCOUNT CPL  {since} -> {until}   (ceiling RM{ceiling:.0f})")
print(f"{'campaign':36} {'spend':>9} {'reg':>5} {'CPL':>8}   flag")
print("-" * 70)
for name, spend, regs, cpl in rows:
    cpl_s = "  ∞" if cpl == float("inf") else f"RM{cpl:5.1f}"
    flag = "OVER" if cpl > ceiling else "ok"
    print(f"{name[:36]:36} {spend:9.0f} {regs:5.0f} {cpl_s:>8}   {flag}")
print("-" * 70)
blended = (tot_spend / tot_reg) if tot_reg else float("inf")
print(f"{'TOTAL':36} {tot_spend:9.0f} {tot_reg:5.0f} RM{blended:5.1f}")
overs = [r for r in rows if r[3] > ceiling]
print(f"\n{len(overs)} campaign(s) over RM{ceiling:.0f}: " +
      (", ".join(f"{n} (RM{c:.1f})" for n, _, _, c in overs) or "none"))
