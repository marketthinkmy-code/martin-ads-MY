"""Daily whole-account performance report (read-only), emitted as clean Markdown.

Plain text, minimal colour (one ⚠️ flag for breaches only), and every section states its
time window explicitly:
  • CPL  — this ad-week, from Thursday (the weekly ON/reset) to now — matches how the owner
           reviews and the monitor's judgement window.
  • CPA  — last 60 days, from the Paid Student List.
render_report() is a pure function so the layout can be previewed without hitting the API.
"""
from __future__ import annotations

import datetime as dt
import math

from adbot import cpa
from adbot.commands import graph_client
from adbot.monitor_cpl import (MANUAL_HOLD, _mkey, _week_start_thursday, evaluate_account,
                               extract_results, result_action_type)
from adbot.settings import load_settings


def _cpl(spend: float, reg: float):
    if reg > 0:
        return spend / reg
    return math.inf if spend > 0 else None


def _cpl_text(cpl) -> str:
    if cpl is None:
        return "—"
    return "∞" if cpl == math.inf else f"RM{cpl:.1f}"


def _cpa_text(c) -> str:
    if c is None:
        return "—"
    return "∞" if c == math.inf else f"RM{c:,.0f}"


def _short(name: str) -> str:
    """Trim a trailing ' - d/m/yyyy' date so campaign names read cleanly."""
    import re
    return re.sub(r"\s*-\s*\d+/\d+/\d+\s*$", "", name).strip()


def cpa_money_map(graph, settings, today):
    """Per-campaign 60-day CPA (display, spend60, sales60, cpa) + blended, joined by UTM name."""
    from adbot.clients.sheets import SheetsClient
    values = SheetsClient(settings.secrets.google_sa_json).read_tab(
        settings.cpa.spreadsheet_id, settings.cpa.sales_tab)
    sales, _cols, _hdr = cpa.parse_sales(values, settings.cpa.price_myr)
    _by_ad, _by_adset, by_campaign = cpa.attribute(sales, today)
    since, until = (today - dt.timedelta(days=60)).isoformat(), today.isoformat()
    spend = {}
    for r in graph.account_insights(settings.meta.account_path, level="campaign",
                                    fields="campaign_name,spend",
                                    time_range={"since": since, "until": until}):
        try:
            spend[_mkey(r.get("campaign_name", ""))] = (r.get("campaign_name", ""), float(r.get("spend") or 0))
        except (TypeError, ValueError):
            continue
    rows = []
    for k in set(by_campaign) | set(spend):
        disp = spend.get(k, (k, 0))[0] or k
        sp = spend.get(k, (None, 0.0))[1]
        sa = by_campaign.get(k, {}).get("60d", 0)
        if sp <= 0 and sa == 0:
            continue
        rows.append((disp, sp, sa, cpa.cpa(sp, sa)))
    rows.sort(key=lambda r: -(r[1] or 0))
    tot_sp, tot_sa = sum(r[1] for r in rows), sum(r[2] for r in rows)
    return rows, cpa.cpa(tot_sp, tot_sa)


def _action_reason(d, cpa_tiers) -> str:
    if d.reason == cpa.HARD_STOP:
        return f"CPA {_cpa_text(d.cpa)} on {d.cpa_sales} sale — over hard-stop RM{cpa_tiers.hard_stop:.0f}" \
            if cpa_tiers else f"CPA {_cpa_text(d.cpa)} on {d.cpa_sales} sale — over hard-stop"
    if d.cpl == math.inf:
        return f"CPL ∞ — spend with 0 regs"
    if d.cpl is not None:
        return f"CPL {_cpl_text(d.cpl)} — over ceiling"
    return d.reason


def render_report(now_myt, week_start, rows, decisions, ceiling,
                  cpa_rows=None, blended_cpa=None, cpa_tiers=None) -> str:
    tot_spend = sum(r[1] for r in rows)
    tot_reg = sum(r[2] for r in rows)
    blended = _cpl(tot_spend, tot_reg)
    over = blended is not None and (blended == math.inf or blended > ceiling)

    out = [
        "# 📊 Daily Ads Report",
        f"{now_myt:%a %d %b %Y, %H:%M} MYT · whole account (MTC + STOCKBLOOM)",
        "",
        f"## 📅 CPL · this ad-week ({week_start:%a %d %b} → now)",
        f"Spend **RM{tot_spend:,.0f}** · **{tot_reg:.0f}** registrations · "
        f"blended CPL **{_cpl_text(blended)}** · ceiling RM{ceiling:.0f}"
        f"{'  ⚠️ over' if over else ''}",
        "",
        "_Campaigns, cheapest CPL first (⚠️ = over ceiling):_",
    ]
    for name, spend, reg in sorted(rows, key=lambda r: (lambda c: math.inf if c is None else c)(_cpl(r[1], r[2]))):
        cpl = _cpl(spend, reg)
        flag = " ⚠️" if (cpl is None or cpl == math.inf or cpl > ceiling) else ""
        out.append(f"- {_short(name)} · CPL {_cpl_text(cpl)} · RM{spend:,.0f} · {reg:.0f} reg{flag}")
    out.append("")

    if cpa_rows is not None and cpa_tiers is not None:
        out += [
            f"## 💰 CPA · last 60 days (real paid sales)",
            f"Blended CPA **{_cpa_text(blended_cpa)}** · target RM{cpa_tiers.healthy_max:.0f} · "
            f"hard-stop RM{cpa_tiers.hard_stop:.0f}",
            "",
            "_Campaigns, most spend first (⚠️ = above hard-stop):_",
        ]
        for disp, sp, sa, c in cpa_rows[:14]:
            flag = " ⚠️" if (c is None or c == math.inf or c > cpa_tiers.hard_stop) else ""
            out.append(f"- {_short(disp)} · CPA {_cpa_text(c)} · RM{sp:,.0f} · {sa:.0f} sales{flag}")
        out.append("")

    pausing = [d for d in decisions if d.should_pause]
    held = [d for d in decisions if d.reason == MANUAL_HOLD]
    rescued = [d for d in decisions if d.reason == cpa.CPL_RESCUED]
    out += ["## 🔧 Monitor actions", ""]
    if not pausing and not held and not rescued:
        out += ["Nothing to pause — all ads within CPL and CPA. ✅", ""]
    else:
        for d in pausing:
            out.append(f"- ✂️ **{_short(d.name)}** — {_action_reason(d, cpa_tiers)} → pausing")
        for d in rescued:
            out.append(f"- 🛟 **{_short(d.name)}** — high CPL but CPA {_cpa_text(d.cpa)} "
                       f"on {d.cpa_sales} sale → kept (profitable)")
        for d in held:
            out.append(f"- 🔒 **{_short(d.name)}** — CPL {_cpl_text(d.cpl)} → held by you (CPL-exempt)")
        out.append("")

    out += ["---", "<sub>🤖 Auto-generated daily · reply here or ping me to act · cc @marketthinkmy-code</sub>"]
    return "\n".join(out)


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    reg_token = result_action_type(s.meta.conversion_event)
    ceiling = s.kpi.cpl_threshold_myr
    now_myt = dt.datetime.utcnow() + dt.timedelta(hours=8)  # Asia/Kuala_Lumpur, no DST
    week_start = _week_start_thursday(now_myt.date())

    rows = []
    for r in g.account_insights(s.meta.account_path, level="campaign",
                                fields="campaign_name,spend,actions",
                                time_range={"since": week_start.isoformat(),
                                            "until": now_myt.date().isoformat()}):
        spend = float(r.get("spend") or 0)
        reg = extract_results(r.get("actions"), reg_token)
        if spend <= 0 and reg == 0:
            continue
        rows.append((r.get("campaign_name", ""), spend, reg))

    decisions = evaluate_account(g, s)

    cpa_rows = blended_cpa = tiers = None
    if s.cpa.enabled:
        try:  # the report must never fail on the CPA add-on
            tiers = cpa.CpaTiers(s.cpa.healthy_max_myr, s.cpa.max_acceptable_myr, s.cpa.hard_stop_myr)
            cpa_rows, blended_cpa = cpa_money_map(g, s, now_myt.date())
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"<!-- CPA section skipped: {type(exc).__name__}: {exc} -->", file=sys.stderr)
            cpa_rows = tiers = None

    print(render_report(now_myt, week_start, rows, decisions, ceiling, cpa_rows, blended_cpa, tiers))


if __name__ == "__main__":
    main()
