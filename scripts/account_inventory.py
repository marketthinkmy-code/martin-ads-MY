"""Read-only whole-account inventory: list every campaign, its objective, 7d spend,
and which conversion events it actually produces. Shows how the STOCKBLOOM-tuned
CPL monitor would treat each one if its scope were widened to the whole account.
"""
from __future__ import annotations

from adbot.clients.graph import GraphClient
from adbot.monitor_cpl import result_action_type, extract_results, decide
from adbot.settings import load_settings

s = load_settings()
g = GraphClient(s.secrets.meta_token, "")  # skip appsecret_proof: local .env app secret is stale (you reset it)
acct = s.meta.account_path
reg_token = result_action_type(s.meta.conversion_event)  # offsite_conversion.fb_pixel_complete_registration

camps = g._get_all(f"{acct}/campaigns",
                   {"fields": "name,objective,effective_status,daily_budget", "limit": 200})
active = [c for c in camps if c.get("effective_status") == "ACTIVE"]
print(f"ACCOUNT {acct}: {len(camps)} campaigns total, {len(active)} ACTIVE\n")

would_pause = 0
for c in sorted(active, key=lambda c: c.get("name", "")):
    rows = g._get_all(f"{c['id']}/insights",
                      {"fields": "spend,actions", "date_preset": "last_7d", "limit": 1})
    spend = float(rows[0].get("spend", 0)) if rows else 0.0
    actions = rows[0].get("actions") if rows else None
    regs = extract_results(actions, reg_token)
    # top non-trivial conversion-ish actions this campaign actually gets
    tops = sorted([(a["action_type"], float(a.get("value", 0))) for a in (actions or [])
                   if float(a.get("value", 0)) > 0 and (
                       "purchase" in a["action_type"] or "lead" in a["action_type"]
                       or "registration" in a["action_type"] or "messaging" in a["action_type"]
                       or a["action_type"] == "link_click")],
                  key=lambda x: -x[1])[:3]
    pause, reason, cpl = decide(spend, regs, s.kpi)
    flag = "  <-- WOULD BE PAUSED" if pause else ""
    if pause:
        would_pause += 1
    obj = (c.get("objective") or "").replace("OUTCOME_", "")
    db = c.get("daily_budget")
    db_s = f"RM{int(db)/100:.0f}/d" if db else "adset-bud"
    tops_s = ", ".join(f"{t}={v:.0f}" for t, v in tops) or "(no purchase/lead/reg/msg actions)"
    print(f"[{obj:12}] {c.get('name','')[:34]:34} {db_s:9} 7d spend RM{spend:7.0f} "
          f"reg={regs:.0f} -> {reason}{flag}")
    print(f"               actually gets: {tops_s}")

print(f"\n==> Widening the monitor to the whole account as-is would PAUSE {would_pause} of "
      f"{len(active)} active campaigns' ads at the next hourly run.")
