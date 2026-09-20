"""Read-only: WHY is a batch of ads expensive — decompose CPL into the funnel it is made of.

CPL = CPM / 1000 ÷ link-CTR ÷ landing-page rate ÷ registration rate. An expensive lead can come
from the auction (CPM), the creative's hook (3-second view rate, thruplay), the click (CTR), the
landing page (LPV per click), or the form (registrations per LPV). This pulls those metrics per
ad since a date, groups the Sept 15-video rebuild against everything else that spent in the same
window, and prints the comparison per group, per audience and per creative, plus each new ad
set's learning-stage status. No writes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import defaultdict
from pathlib import Path

from adbot import cpa
from adbot.commands import graph_client
from adbot.monitor_cpl import extract_results, result_action_type
from adbot.settings import REPO_ROOT, load_settings

SPECS = ("sept15x15_fnr", "sept15x15_parents_engaged", "sept15x15_parents_kids")
SINCE = os.environ.get("ADBOT_SINCE") or "2026-09-15"
FIELDS = ("ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,impressions,reach,frequency,spend,"
          "inline_link_clicks,actions,video_play_actions,video_thruplay_watched_actions,"
          "video_p25_watched_actions,video_p50_watched_actions,video_p75_watched_actions,video_p100_watched_actions")


def _n(x) -> float:
    try:
        return float(str(x).replace(",", "") or 0)
    except ValueError:
        return 0.0


def _act(row, key):
    return _n((row.get(key) or [{}])[0].get("value")) if isinstance(row.get(key), list) and row.get(key) else 0.0


def _f(v, d=0) -> str:
    if v is None or v == math.inf or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:,.{d}f}"


def _pct(a, b) -> str:
    return f"{100 * a / b:.1f}%" if b else "—"


def agg():
    return defaultdict(float)


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    token = result_action_type(s.meta.conversion_event)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    new_camps = {}
    for name in SPECS:
        spec = json.loads((REPO_ROOT / "scripts/clone_specs" / f"{name}.json").read_text(encoding="utf-8"))
        new_camps[spec["_built"]["campaign_id"]] = spec.get("adset_name") or spec["adsets"][0]["name"]
    print(f"===== FUNNEL DIAGNOSIS · since {SINCE} · {today} MYT · new = the three 1-15-15 campaigns =====\n")

    rows = g.account_insights(s.meta.account_path, level="ad", fields=FIELDS,
                              time_range={"since": SINCE, "until": today.isoformat()})
    groups = {"NEW 15 支": agg(), "其他在跑的老素材": agg()}
    by_aud, by_creative, by_old = defaultdict(agg), defaultdict(agg), defaultdict(agg)
    ad_rows = []
    for r in rows:
        sp = _n(r.get("spend"))
        if sp <= 0:
            continue
        m = {
            "spend": sp, "impr": _n(r.get("impressions")), "reach": _n(r.get("reach")),
            "clicks": _n(r.get("inline_link_clicks")),
            "lpv": extract_results(r.get("actions"), "landing_page_view"),
            "regs": extract_results(r.get("actions"), token),
            "v3": extract_results(r.get("actions"), "video_view"),
            "play": _act(r, "video_play_actions"), "thru": _act(r, "video_thruplay_watched_actions"),
            "p25": _act(r, "video_p25_watched_actions"), "p50": _act(r, "video_p50_watched_actions"),
            "p75": _act(r, "video_p75_watched_actions"), "p100": _act(r, "video_p100_watched_actions"),
        }
        is_new = r.get("campaign_id") in new_camps
        grp = groups["NEW 15 支" if is_new else "其他在跑的老素材"]
        for k, v in m.items():
            grp[k] += v
        if is_new:
            for k, v in m.items():
                by_aud[new_camps[r["campaign_id"]]][k] += v
                by_creative[cpa.ad_key(r.get("ad_name") or "")][k] += v
            by_creative[cpa.ad_key(r.get("ad_name") or "")]["name"] = r.get("ad_name")
        else:
            for k, v in m.items():
                by_old[cpa.ad_key(r.get("ad_name") or "")][k] += v
            by_old[cpa.ad_key(r.get("ad_name") or "")]["name"] = r.get("ad_name")
        ad_rows.append((is_new, r, m))

    def line(label, a):
        impr, sp = a["impr"], a["spend"]
        cpm = 1000 * sp / impr if impr else math.inf
        ctr = 100 * a["clicks"] / impr if impr else 0
        hook = 100 * a["v3"] / impr if impr else 0
        hold = 100 * a["thru"] / impr if impr else 0
        p50 = 100 * a["p50"] / impr if impr else 0
        lpv_rate = 100 * a["lpv"] / a["clicks"] if a["clicks"] else 0
        reg_rate = 100 * a["regs"] / a["lpv"] if a["lpv"] else 0
        cplpv = sp / a["lpv"] if a["lpv"] else math.inf
        cpl = sp / a["regs"] if a["regs"] else math.inf
        cpc = sp / a["clicks"] if a["clicks"] else math.inf
        freq = a["impr"] / a["reach"] if a["reach"] else 0
        return (f"  {label[:44]:44} RM{sp:>6,.0f} {impr:>8,.0f} {_f(cpm, 2):>6} {freq:>4.1f} {hook:>5.1f}% {hold:>5.1f}% {p50:>5.1f}% "
                f"{ctr:>5.2f}% {_f(cpc, 2):>6} {lpv_rate:>5.0f}% {_f(cplpv, 1):>6} {a['regs']:>4.0f} {reg_rate:>5.1f}% {_f(cpl):>5}")

    hdr = (f"  {'group':44} {'spend':>8} {'impr':>8} {'CPM':>6} {'freq':>4} {'3s率':>6} {'看完率':>6} {'50%':>6} "
           f"{'CTR':>6} {'CPC':>6} {'LPV/点':>6} {'每LPV':>6} {'reg':>4} {'reg/LPV':>6} {'CPL':>5}")
    print("=== 1. 新 15 支 vs 同期其他老素材 ===")
    print(hdr)
    for label, a in groups.items():
        print(line(label, a))

    print("\n=== 2. 新 15 支 · 按受众 ===")
    print(hdr)
    for aud, a in sorted(by_aud.items(), key=lambda kv: -kv[1]["spend"]):
        print(line(aud, a))

    print("\n=== 3. 新 15 支 · 按素材（三个受众合并）===")
    print(hdr)
    for k, a in sorted(by_creative.items(), key=lambda kv: (kv[1]["regs"] == 0, (kv[1]["spend"] / kv[1]["regs"]) if kv[1]["regs"] else 0, -kv[1]["spend"])):
        print(line(str(a.get("name", k)), a))

    print("\n=== 4. 老素材（同期）· 按素材 ===")
    print(hdr)
    for k, a in sorted(by_old.items(), key=lambda kv: -kv[1]["spend"])[:10]:
        print(line(str(a.get("name", k)), a))

    # learning stage of the new ad sets
    print("\n=== 5. 新 45 个 ad set 的 learning 状态 ===")
    stages = defaultdict(int)
    for cid in new_camps:
        for a in g._get_all(f"{cid}/adsets", {"fields": "id,name,effective_status,daily_budget,learning_stage_info", "limit": 100}):
            info = a.get("learning_stage_info") or {}
            stages[f"{info.get('status', '?')} (conv {info.get('conversions', '?')})" if info else "no info"] += 1
    for k, n in sorted(stages.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3} ad sets · {k}")
    print("\nDONE (no writes)")


if __name__ == "__main__":
    main()
