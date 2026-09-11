"""CPL guardrail: decide which ads to pause, and run the pause against Meta.

"CPL" here means cost per the campaign's optimized conversion event (e.g. Complete
Registration), not a hardcoded "lead". The decision logic is a pure function (unit-tested);
the runner reads insights via the Graph client, only ever acts on ACTIVE ads, and never
un-pauses — re-activation is always a human (or weekly_on) decision.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import cpa, state
from .logging import final_summary, get_logger
from .settings import KpiCfg, Settings

INSUFFICIENT_SPEND = "insufficient_spend"
ZERO_RESULTS = "zero_results_over_min_spend"
OVER_THRESHOLD = "cpl_over_threshold"
WITHIN_THRESHOLD = "within_threshold"
NO_RESULTS_YET = "no_results_yet"
MANUAL_HOLD = "manual_hold"  # owner asked to keep this ad running despite CPL
CPL_GRACE_NEW = "cpl_grace_new_ad"  # young ad over CPL — exempted so its leads can mature to sales
GRACE_BRAKE = "cpl_grace_brake"     # young ad burning too hard with no sales — grace withdrawn
LEAD_QUALITY = "lead_quality_regs_no_sales"  # plenty of sign-ups, zero buyers — the CPL is a lie
OVER_THRESHOLD_CUT = "cpl_over_threshold_cut"  # over the ceiling under the daily rule: cut budget, keep running
CUT_RESCUED = "cpa_rescued_no_cut"             # over CPL but selling at a healthy CPA — budget left alone
CUT_LABEL_PREFIX = "ADBOT_CPL_CUT_"            # + YYYY-MM-DD (MYT): marks an ad set / campaign cut today


def grace_braked(spend: float, cpl: Optional[float], cpa_sales: int, kpi: KpiCfg) -> bool:
    """True when a young ad is burning hard enough that the CPL grace must NOT protect it.

    Only ever brakes an ad with ZERO matched paid sales — one with real sales at an acceptable CPA
    is rescued by cpa.combined_decision before this runs, and that rescue wins. Either trigger
    fires: a CPL far above the ceiling, or spend past a hard cash cap with nothing to show for it.
    """
    if cpa_sales > 0:
        return False                       # real sales -> CPA decides, never the brake
    multiple = kpi.cpl_grace_max_cpl_multiple
    if (multiple > 0 and cpl is not None and cpl != math.inf
            and cpl > kpi.cpl_threshold_myr * multiple):
        return True
    cap = kpi.cpl_grace_max_spend_myr
    return cap > 0 and spend >= cap


def webinars_since(created: dt.date, today: dt.date, kpi: KpiCfg) -> Optional[int]:
    """How many webinars have both RUN and had time to be banked since an ad went live.

    Sales only happen on webinar nights, so calendar age is the wrong clock: an ad born the day
    after a webinar is six days old before its registrants get their first chance to buy, while
    one born the day before gets its chance immediately. Counting webinars measures the thing that
    actually decides the ad's fate.

    A webinar counts once `settle_days` have passed since it ran, because the paid list is filled
    in by hand and same-night sales are not in the sheet yet — judging before then reads a real
    sale as a zero. Returns None when no webinar weekday is configured, so callers fall back to
    the day-count grace.
    """
    weekday = kpi.webinar_weekday
    if weekday is None or not 0 <= weekday <= 6:
        return None
    cutoff = today - dt.timedelta(days=max(kpi.webinar_settle_days, 0))
    # first webinar STRICTLY after the ad was created
    step = (weekday - created.weekday()) % 7 or 7
    when, n = created + dt.timedelta(days=step), 0
    while when <= cutoff:
        n += 1
        when += dt.timedelta(days=7)
    return n


def _week_start_thursday(today: dt.date) -> dt.date:
    """Most recent Thursday (the weekly ON/reset day) on or before `today`."""
    return today - dt.timedelta(days=(today.weekday() - 3) % 7)  # Mon=0..Thu=3


def cpl_window(settings: Settings, today: dt.date):
    """(date_preset, time_range) for the CPL lookback.

    'week_thu' = week-to-date from the most recent Thursday — the window the operator
    actually reviews (matches the weekly OFF/ON cycle). Anything else is a Meta date_preset.
    """
    lookback = settings.kpi.cpl_lookback
    if lookback == "week_thu":
        return None, {"since": _week_start_thursday(today).isoformat(), "until": today.isoformat()}
    return lookback, None


def result_action_type(conversion_event: str) -> str:
    """The exact insights action_type that equals Ads Manager "Results" for a pixel-optimized ad.

    Meta reports the SAME conversion under several overlapping buckets (complete_registration,
    omni_complete_registration, offsite_complete_registration_*, offsite_conversion.fb_pixel_*),
    so we must match ONE exactly — substring-summing them multiplies the real count.
    """
    return f"offsite_conversion.fb_pixel_{(conversion_event or '').lower()}"


def extract_results(actions: Optional[List[Dict[str, Any]]], action_type: str) -> float:
    """Sum values for ONLY the exact optimized-event bucket (= Ads Manager 'Results')."""
    total = 0.0
    for action in actions or []:
        if action.get("action_type") == action_type:
            try:
                total += float(action.get("value", 0))
            except (TypeError, ValueError):
                continue
    return total


def parse_metrics(insight: Optional[Dict[str, Any]], token: str) -> Tuple[float, float]:
    """Return (spend, results) from a raw insight row for the optimized event."""
    if not insight:
        return 0.0, 0.0
    try:
        spend = float(insight.get("spend", 0) or 0)
    except (TypeError, ValueError):
        spend = 0.0
    return spend, extract_results(insight.get("actions"), token)


def zero_reg_spend_line(kpi: KpiCfg) -> float:
    """Spend at which an ad with ZERO registrations is paused.

    Operator rule 2026-09-11: 1.5x the target CPL (1.5 x RM60 = RM90). When the multiple is 0
    the older cpl_min_spend_myr line applies.
    """
    if kpi.cpl_zero_reg_spend_multiple > 0:
        return kpi.cpl_threshold_myr * kpi.cpl_zero_reg_spend_multiple
    return kpi.cpl_min_spend_myr


def plan_cut(current_cents: int, pct: float, floor_myr: float) -> int:
    """New daily budget after a pct cut, never below the floor (all in minor units)."""
    floor = int(round(floor_myr * 100))
    target = int(round(current_cents * (1.0 - pct / 100.0)))
    return max(target, floor)


def decide(spend: float, results: float, kpi: KpiCfg) -> Tuple[bool, str, Optional[float]]:
    """(should_pause, reason, cpl). cpl is None when undefined, inf when results==0.

    Zero registrations: paused once spend reaches zero_reg_spend_line (rule 2). With
    registrations: judged only after cpl_min_spend_myr; over the ceiling is a PAUSE under
    cpl_over_action="pause", or a budget CUT (reason OVER_THRESHOLD_CUT, should_pause False)
    under "cut" — the caller applies the cut.
    """
    if results <= 0:
        if kpi.pause_zero_lead_after_spend and spend >= zero_reg_spend_line(kpi):
            return True, ZERO_RESULTS, math.inf
        if spend < kpi.cpl_min_spend_myr:
            return False, INSUFFICIENT_SPEND, None
        return False, NO_RESULTS_YET, math.inf
    if spend < kpi.cpl_min_spend_myr:
        return False, INSUFFICIENT_SPEND, None
    cpl = spend / results
    if cpl > kpi.cpl_threshold_myr:
        if (kpi.cpl_over_action or "pause").lower() == "cut":
            return False, OVER_THRESHOLD_CUT, cpl
        return True, OVER_THRESHOLD, cpl
    return False, WITHIN_THRESHOLD, cpl


@dataclass
class AdDecision:
    ad_id: str
    name: str
    spend: float
    results: float
    cpl: Optional[float]
    should_pause: bool
    reason: str
    cpa: Optional[float] = None     # 60-day real-sales CPA (None when not judged)
    cpa_sales: int = 0              # 60-day matched paid sales
    age_days: Optional[int] = None  # ad age, for the conversion-window guard
    should_cut: bool = False        # daily rule: over CPL -> cut the ad set's budget instead of pausing
    adset_id: Optional[str] = None
    campaign_id: Optional[str] = None


def _mkey(name: str) -> str:
    """Campaign match key: drop a leading '(Image)' tag Meta adds, then width/punct-robust key."""
    s = (name or "").strip()
    if s.lower().startswith("(image)"):
        s = s[len("(image)"):]
    return cpa.ad_key(s)


def build_cpa_context(graph, settings: Settings, today: dt.date):
    """(60-day sales by (campaign,ad), 60-day spend by ad_id, 60-day regs by ad_id,
    most-recent sale date by (campaign,ad)) for the CPA gate.

    Registrations ride the same insights call at no extra API cost — the lead-quality gate
    needs sign-ups and sales side by side. The last-sale date feeds the unhealthy-band rule:
    a pause candidate only counts as such once a full webinar cycle has passed since it last
    sold anything.

    Returns empty dicts when CPA is disabled or any source is unavailable, so a Sheets/Meta
    hiccup degrades the monitor to CPL-only rather than breaking it.
    """
    if not settings.cpa.enabled:
        return {}, {}, {}, {}
    try:
        from .clients.sheets import SheetsClient
        values = SheetsClient(settings.secrets.google_sa_json).read_tab(
            settings.cpa.spreadsheet_id, settings.cpa.sales_tab)
        sales, _cols, _hdr = cpa.parse_sales(values, settings.cpa.price_myr)
        cutoff = today - dt.timedelta(days=60)
        sold: Dict[Tuple[str, str], int] = {}
        last_sale: Dict[Tuple[str, str], dt.date] = {}
        for s in sales:
            if s.date and s.date > cutoff:
                key = (_mkey(s.campaign), cpa.ad_key(s.ad))
                sold[key] = sold.get(key, 0) + 1
                if key not in last_sale or s.date > last_sale[key]:
                    last_sale[key] = s.date
        token = result_action_type(settings.meta.conversion_event)
        spend: Dict[str, float] = {}
        regs: Dict[str, float] = {}
        for row in graph.account_insights(
                settings.meta.account_path, level="ad", fields="ad_id,spend,actions",
                time_range={"since": cutoff.isoformat(), "until": today.isoformat()}):
            try:
                spend[row.get("ad_id")] = float(row.get("spend") or 0)
            except (TypeError, ValueError):
                continue
            regs[row.get("ad_id")] = extract_results(row.get("actions"), token)
        return sold, spend, regs, last_sale
    except Exception as exc:  # noqa: BLE001
        get_logger().warning("CPA context unavailable (%s) — CPL-only this run", exc)
        return {}, {}, {}, {}


def evaluate_account(graph, settings: Settings, *, cpa_ctx=None) -> List[AdDecision]:
    """Read every active ad in the account and compute per-ad pause decisions (no writes).

    Whole-account scope (every campaign in the Martin MY account), but judged one ad at a time —
    a single bad creative is paused without touching the rest of its ad set or campaign.
    Only ads whose ad set optimizes for the configured conversion event (e.g. Complete
    Registration) are evaluated, so a campaign chasing a different objective can never be
    paused on a registration-CPL it was never trying to produce.
    """
    account = settings.meta.account_path
    token = result_action_type(settings.meta.conversion_event)
    want_event = (settings.meta.conversion_event or "").upper()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT
    cpl_preset, cpl_range = cpl_window(settings, today)
    ctx = cpa_ctx if cpa_ctx is not None else build_cpa_context(graph, settings, today)
    sold60, spend60 = ctx[0], ctx[1]
    regs60 = ctx[2] if len(ctx) > 2 else {}
    last60 = ctx[3] if len(ctx) > 3 else {}
    use_cpa = settings.cpa.enabled and (bool(sold60) or bool(spend60))
    tiers = cpa.CpaTiers(settings.cpa.healthy_max_myr, settings.cpa.max_acceptable_myr,
                         settings.cpa.hard_stop_myr)

    # Batch every active ad's CPL-window insight into ONE account-level call (level=ad) instead
    # of one request per ad. A whole-account scan otherwise fires dozens of /insights calls and
    # trips Meta's per-account rate limit ("too many calls from this ad account"), which fails
    # the whole monitor run — so nothing gets paused. Ads with no row (zero delivery in the
    # window) fall through to parse_metrics(None)=spend 0 and are simply left running.
    insight_by_ad: Dict[str, Dict[str, Any]] = {}
    for row in graph.account_insights(account, level="ad", fields="ad_id,spend,actions",
                                      date_preset=cpl_preset, time_range=cpl_range):
        aid = row.get("ad_id")
        if aid:
            insight_by_ad[aid] = row

    decisions: List[AdDecision] = []
    for campaign in graph.list_campaigns(account):
        if campaign.get("effective_status") != "ACTIVE":  # paused/archived have no live ads
            continue
        camp_key = _mkey(campaign.get("name", ""))
        for ad in graph.list_ads_under_campaign(campaign["id"]):
            if ad.get("effective_status") != "ACTIVE":
                continue
            promoted = (ad.get("adset") or {}).get("promoted_object") or {}
            if (promoted.get("custom_event_type") or "").upper() != want_event:
                continue  # not optimized for our event — not ours to judge or pause
            name = ad.get("name", ad["id"])
            insight = insight_by_ad.get(ad["id"])
            spend, results = parse_metrics(insight, token)
            created = cpa.parse_date((ad.get("created_time") or "")[:10])
            age = (today - created).days if created else None

            held = any(h and h in name for h in settings.kpi.cpl_hold)
            if held:                                   # a hold exempts from CPL (not CPA)
                cpl_pause, cpl_reason = False, MANUAL_HOLD
                cpl = (spend / results) if results else (math.inf if spend else None)
            else:
                cpl_pause, cpl_reason, cpl = decide(spend, results, settings.kpi)

            cpa_val: Optional[float] = None
            n_sales = 0
            should_pause, reason = cpl_pause, cpl_reason
            if use_cpa:
                key60 = (camp_key, cpa.ad_key(name))
                n_sales = sold60.get(key60, 0)
                sp60 = spend60.get(ad["id"], 0.0)
                cpa_val = cpa.cpa(sp60, n_sales)
                # Unhealthy band (max_acceptable < CPA <= hard_stop): a pause candidate only
                # once a full webinar + settle cycle has passed since its LAST sale with no new
                # one — that cycle is its chance to redeem the CPA before the pause lands.
                last_sale = last60.get(key60)
                cycle_done = (last_sale is not None
                              and (webinars_since(last_sale, today, settings.kpi) or 0) >= 1)
                should_pause, reason = cpa.combined_decision(
                    cpl_pause=cpl_pause, cpl_reason=cpl_reason, cpa_value=cpa_val,
                    cpa_sales=n_sales, cpa_spend=sp60, age_days=age, tiers=tiers,
                    conversion_days=settings.cpa.conversion_days, min_spend=settings.cpa.min_spend_myr,
                    unhealthy_cycle_done=cycle_done)

            # CPL grace for brand-new ads: a young ad over CPL but still pulling registrations hasn't
            # had time for those webinar sign-ups to mature into paid sales, so a CPL-only pause kills
            # the creative test prematurely. Exempt OVER_THRESHOLD only — a zero-lead ad (ZERO_RESULTS)
            # or a proven CPA hard-stop still pauses.
            #
            # Whether an ad is "young" is measured in webinars when one is configured, not days: the
            # webinar is the only moment a registration can turn into a sale, so an ad that has not
            # sat through one has provably not had its chance yet. cpl_grace_days is the fallback.
            passed = webinars_since(created, today, settings.kpi) if created else None
            if passed is not None:
                unproven = passed < settings.kpi.cpl_grace_webinars
            else:
                unproven = age is not None and age < settings.kpi.cpl_grace_days
            if should_pause and reason == OVER_THRESHOLD and unproven:
                if grace_braked(spend, cpl, n_sales, settings.kpi):
                    reason = GRACE_BRAKE   # burning too hard to shelter — the pause stands
                else:
                    should_pause, reason = False, CPL_GRACE_NEW

            # Lead-quality gate: an ad can hold a beautiful CPL for months while producing
            # registrants who never buy — 60+ sign-ups with zero sales is not bad luck, it is the
            # hook selecting the wrong people, and registration-optimised delivery keeps feeding
            # it BECAUSE its sign-ups are cheap. Enough 60-day registrations with zero matched
            # sales pauses the ad regardless of CPL. A manual hold still wins; an ad that has not
            # yet sat through a webinar is not judged (its registrants never had the chance to
            # buy); and without the sheet (use_cpa) silence is not evidence of anything.
            min_regs = settings.kpi.lead_quality_min_regs
            if (not should_pause and not held and use_cpa and min_regs > 0 and not unproven
                    and n_sales == 0 and regs60.get(ad["id"], 0) >= min_regs):
                should_pause, reason = True, LEAD_QUALITY

            # Daily rule 1 (2026-09-11): over the CPL ceiling -> cut the budget 30%, keep running.
            # Only when nothing above already pauses the ad. An ad with real 60-day sales at a
            # healthy CPA is left alone (cpl_cut_respect_cpa): registration cost is the wrong
            # metric for an ad that is buying customers at the right price.
            should_cut = False
            if cpl_reason == OVER_THRESHOLD_CUT and not should_pause and not held:
                rescued = (settings.kpi.cpl_cut_respect_cpa and use_cpa and n_sales > 0
                           and cpa_val is not None and cpa_val <= settings.cpa.healthy_max_myr)
                if rescued:
                    reason = CUT_RESCUED
                else:
                    should_cut, reason = True, OVER_THRESHOLD_CUT

            decisions.append(AdDecision(ad["id"], name, spend, results, cpl, should_pause, reason,
                                        cpa=cpa_val, cpa_sales=n_sales, age_days=age,
                                        should_cut=should_cut, adset_id=ad.get("adset_id"),
                                        campaign_id=campaign["id"]))
    return decisions


def apply_cut(graph, settings: Settings, d: AdDecision, today: dt.date, label_cache: Dict[str, str]):
    """Cut the budget behind one over-CPL ad, once per day. Returns (status, detail).

    The budget lives on the ad set (ABO) or, when the ad set carries none, on the campaign (CBO).
    A dated label (ADBOT_CPL_CUT_<today>) on that entity is the once-a-day guard: the monitor
    runs every 20 minutes and must not compound 30% cuts through the day. Never below the floor.
    """
    kpi = settings.kpi
    entity_id, entity_kind = d.adset_id, "adset"
    obj = graph.get_object(entity_id, "name,daily_budget,adlabels{id,name},campaign_id") if entity_id else {}
    if not obj.get("daily_budget"):
        entity_id, entity_kind = (obj.get("campaign_id") or d.campaign_id), "campaign"
        if not entity_id:
            return "skip", "no ad set / campaign id"
        obj = graph.get_object(entity_id, "name,daily_budget,adlabels{id,name}")
        if not obj.get("daily_budget"):
            return "skip", "no daily budget on ad set or campaign (lifetime budget?)"
    labels = (obj.get("adlabels") or {}).get("data", obj.get("adlabels") or []) or []
    today_label = f"{CUT_LABEL_PREFIX}{today.isoformat()}"
    if any(l.get("name") == today_label for l in labels):
        return "skip", f"{entity_kind} {entity_id} already cut today"
    current = int(obj["daily_budget"])
    new = plan_cut(current, kpi.cpl_cut_pct, kpi.cpl_cut_floor_myr)
    if new >= current:
        return "skip", f"{entity_kind} {entity_id} at floor RM{current / 100:,.0f}"
    graph.set_daily_budget(entity_id, new)
    label_id = label_cache.get(today_label)
    if not label_id:
        label_id = graph.get_or_create_label(settings.meta.account_path, today_label)
        label_cache[today_label] = label_id
    keep = [l["id"] for l in labels if l.get("id")]
    graph.set_entity_labels(entity_id, keep + [label_id])
    return "cut", f"{entity_kind} {entity_id} RM{current / 100:,.0f} -> RM{new / 100:,.0f}/day"


def run(graph, settings: Settings, *, dry_run: bool = False) -> Dict[str, Any]:
    log = get_logger()
    event = settings.meta.conversion_event
    decisions = evaluate_account(graph, settings)
    to_pause = [d for d in decisions if d.should_pause]

    for d in decisions:
        cpl_str = "∞" if d.cpl == math.inf else (f"{d.cpl:.2f}" if d.cpl is not None else "n/a")
        cpa_str = ("" if d.cpa is None else
                   f" CPA={'∞' if d.cpa == math.inf else f'{d.cpa:.0f}'}(60d {d.cpa_sales} sale,{d.age_days}d)")
        if d.should_pause:
            verb = "WOULD PAUSE" if dry_run else "PAUSE"
        elif d.should_cut:
            verb = "WOULD CUT" if dry_run else "CUT"
        else:
            verb = "keep"
        log.info("  [%s] %s  spend=%.2f %s=%.0f CPL=%s%s (%s)",
                 verb, d.name, d.spend, event.lower(), d.results, cpl_str, cpa_str, d.reason)

    paused, failed = 0, 0
    if not dry_run:
        for d in to_pause:
            try:
                graph.update_status(d.ad_id, "PAUSED")
            except Exception as exc:  # noqa: BLE001 - one un-pausable ad (e.g. Meta briefly blocking
                # writes on the account) must NOT crash the whole run and fail the scheduled job.
                # Log and move on; the ad is still over threshold so the next run retries it.
                log.warning("  [skip] could not pause %s: %s", d.name, exc)
                failed += 1
                continue
            state.append_pause_log(d.ad_id, "ad", d.reason,
                                   {"spend": d.spend, "results": d.results,
                                    "cpl": None if d.cpl is None or d.cpl == math.inf else round(d.cpl, 2),
                                    "cpa": None if d.cpa is None or d.cpa == math.inf else round(d.cpa, 2),
                                    "cpa_sales": d.cpa_sales})
            paused += 1

    # Daily rule 1: budget cuts for ads over the CPL ceiling (never for ads being paused).
    to_cut = [d for d in decisions if d.should_cut and not d.should_pause]
    cut, cut_skipped, cut_failed = 0, 0, 0
    if to_cut and not dry_run:
        today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
        label_cache: Dict[str, str] = {}
        for d in to_cut:
            try:
                status, detail = apply_cut(graph, settings, d, today, label_cache)
            except Exception as exc:  # noqa: BLE001 - one failed cut must not fail the run
                log.warning("  [skip] could not cut %s: %s", d.name, exc)
                cut_failed += 1
                continue
            if status == "cut":
                cut += 1
                log.info("  [CUT] %s  %s (CPL %.2f > %.0f)", d.name, detail,
                         d.cpl or 0.0, settings.kpi.cpl_threshold_myr)
                state.append_pause_log(d.ad_id, "ad", "budget_cut",
                                       {"spend": d.spend, "results": d.results, "detail": detail,
                                        "cpl": None if d.cpl is None or d.cpl == math.inf else round(d.cpl, 2)})
            else:
                cut_skipped += 1
                log.info("  [no cut] %s  %s", d.name, detail)
    elif to_cut:
        for d in to_cut:
            log.info("  [WOULD CUT] %s  ad set %s by %.0f%% (floor RM%.0f)", d.name, d.adset_id,
                     settings.kpi.cpl_cut_pct, settings.kpi.cpl_cut_floor_myr)

    active_left = len([d for d in decisions if not d.should_pause])
    summary = (f"CPL monitor ({event}): evaluated {len(decisions)} active ads, "
               f"{'would pause' if dry_run else 'paused'} {len(to_pause) if dry_run else paused}, "
               f"{'would cut' if dry_run else 'cut'} {len(to_cut) if dry_run else cut}, "
               f"{active_left} remain under CPL {settings.kpi.cpl_threshold_myr:.0f} MYR")
    if not dry_run and failed:
        summary += f"; {failed} pause(s) failed, will retry next run (see [skip] warnings)"
    if not dry_run and (cut_skipped or cut_failed):
        summary += f"; cuts skipped {cut_skipped} (already today / at floor), failed {cut_failed}"
    final_summary(log, summary)
    return {"evaluated": len(decisions), "paused": (len(to_pause) if dry_run else paused),
            "cut": (len(to_cut) if dry_run else cut), "remaining": active_left,
            "failed": (0 if dry_run else failed), "dry_run": dry_run}
