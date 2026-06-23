"""Read-only dig into one named campaign: per-ad-set spend/results/CPL for the
18-Jun window AND lifetime, so a pause decision isn't fooled by a bad few days.
No writes."""
from __future__ import annotations

import datetime as dt

from adbot.commands import graph_client
from adbot.settings import load_settings

KL = dt.timezone(dt.timedelta(hours=8))
SINCE = "2026-06-18"
UNTIL = dt.datetime.now(KL).date().isoformat()
NAME_MATCH = "Officer"
BUCKET = "offsite_conversion.fb_pixel_complete_registration"


def results(actions):
    return sum(float(a.get("value", 0) or 0)
               for a in (actions or []) if a.get("action_type") == BUCKET)


def main():
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path

    camps = g._get_all(f"{acct}/campaigns", {"fields": "id,name,effective_status", "limit": 500})
    matches = [c for c in camps if NAME_MATCH.lower() in c["name"].lower()]
    if not matches:
        print(f"No campaign matching {NAME_MATCH!r}")
        return
    print(f"All campaigns matching {NAME_MATCH!r}:")
    for c in matches:
        print(f"  [{c['effective_status']:8}] {c['name']}  id={c['id']}")
    # target = the ACTIVE one (the live RM829 / CPL63.81 campaign), else first
    camp = next((c for c in matches if c["effective_status"] == "ACTIVE"), matches[0])
    cid = camp["id"]
    print(f"\n>>> DIGGING: {camp['name']}  ({camp['effective_status']})  id={cid}\n")

    adsets = g._get_all(f"{cid}/adsets",
                        {"fields": "id,name,effective_status,daily_budget", "limit": 500})

    def insights(date_params):
        rows = g._get_all(f"{cid}/insights",
                          {"level": "adset", "fields": "adset_id,adset_name,spend,actions",
                           "limit": 500, **date_params})
        return {r["adset_id"]: (float(r.get("spend", 0) or 0), results(r.get("actions")))
                for r in rows}

    win = insights({"time_range": f'{{"since":"{SINCE}","until":"{UNTIL}"}}'})
    life = insights({"date_preset": "maximum"})

    print(f"{'STATUS':8} {'AD SET':36} | {'18Jun->now (sp/res/CPL)':28} | LIFETIME (sp/res/CPL)")
    for a in sorted(adsets, key=lambda x: win.get(x["id"], (0, 0))[0], reverse=True):
        ws, wr = win.get(a["id"], (0.0, 0.0))
        ls, lr = life.get(a["id"], (0.0, 0.0))
        wcpl = f"RM{ws/wr:6.2f}" if wr else "  --   "
        lcpl = f"RM{ls/lr:6.2f}" if lr else "  --   "
        print(f"[{a['effective_status']:6}] {a['name'][:36]:36} | "
              f"RM{ws:8.2f} {wr:3.0f} {wcpl} | RM{ls:9.2f} {lr:4.0f} {lcpl}   id={a['id']}")


if __name__ == "__main__":
    main()
