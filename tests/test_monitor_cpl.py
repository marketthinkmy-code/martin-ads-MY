import math

import datetime as dt

from adbot import cpa
from adbot.monitor_cpl import (INSUFFICIENT_SPEND, MANUAL_HOLD, NO_RESULTS_YET, OVER_THRESHOLD,
                               WITHIN_THRESHOLD, ZERO_RESULTS, cpl_window, decide, evaluate_account,
                               extract_results, parse_metrics, result_action_type,
                               _week_start_thursday)
from adbot.settings import CpaCfg, KpiCfg, MetaCfg, Settings

KPI = KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80, pause_zero_lead_after_spend=True)


def test_insufficient_spend_is_skipped():
    should, reason, cpl = decide(50, 0, KPI)
    assert not should and reason == INSUFFICIENT_SPEND and cpl is None


def test_zero_results_after_min_spend_pauses():
    should, reason, cpl = decide(100, 0, KPI)
    assert should and reason == ZERO_RESULTS and cpl == math.inf


def test_zero_results_kept_when_disabled():
    kpi = KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80, pause_zero_lead_after_spend=False)
    should, reason, _ = decide(100, 0, kpi)
    assert not should and reason == NO_RESULTS_YET


def test_cpl_over_threshold_pauses():
    should, reason, cpl = decide(100, 1, KPI)
    assert should and reason == OVER_THRESHOLD and round(cpl) == 100


def test_cpl_within_threshold_keeps():
    should, reason, cpl = decide(100, 4, KPI)
    assert not should and reason == WITHIN_THRESHOLD and cpl == 25


def test_result_action_type_is_exact_offsite_pixel_event():
    assert result_action_type("COMPLETE_REGISTRATION") == "offsite_conversion.fb_pixel_complete_registration"
    assert result_action_type("LEAD") == "offsite_conversion.fb_pixel_lead"


def test_extract_results_counts_only_the_exact_bucket():
    # Meta reports the SAME conversion under several overlapping buckets; only the exact
    # offsite-pixel one is Ads Manager "Results". Summing the rest 5x-overcounts (the bug).
    rat = result_action_type("COMPLETE_REGISTRATION")
    actions = [
        {"action_type": "offsite_conversion.fb_pixel_complete_registration", "value": "2"},
        {"action_type": "complete_registration", "value": "2"},
        {"action_type": "omni_complete_registration", "value": "2"},
        {"action_type": "offsite_complete_registration_add_meta_leads", "value": "2"},
        {"action_type": "offsite_complete_registration_add_20_s_calls", "value": "2"},
        {"action_type": "onsite_conversion.post_net_like", "value": "4"},
    ]
    assert extract_results(actions, rat) == 2


def test_parse_metrics_reads_spend_and_results():
    rat = result_action_type("COMPLETE_REGISTRATION")
    insight = {"spend": "120.50", "actions": [{"action_type": rat, "value": "2"},
                                              {"action_type": "complete_registration", "value": "2"}]}
    spend, results = parse_metrics(insight, rat)
    assert spend == 120.5 and results == 2


def test_parse_metrics_handles_empty():
    assert parse_metrics(None, "offsite_conversion.fb_pixel_complete_registration") == (0.0, 0.0)


# ── evaluate_account: whole-account scope, ad-level decisions, registration-only guard ──
class _FakeGraph:
    def __init__(self, campaigns, ads_by_campaign, insights):
        self._campaigns, self._ads, self._insights = campaigns, ads_by_campaign, insights

    def list_campaigns(self, account_path):
        return self._campaigns

    def list_ads_under_campaign(self, campaign_id):
        return self._ads.get(campaign_id, [])

    def get_ad_insight(self, ad_id, date_preset=None, time_range=None):
        return self._insights.get(ad_id)


def _ad(ad_id, status="ACTIVE", event="COMPLETE_REGISTRATION", created_time="2026-01-01"):
    return {"id": ad_id, "name": ad_id, "effective_status": status, "created_time": created_time,
            "adset": {"promoted_object": {"custom_event_type": event} if event else {}}}


def _reg_insight(spend, results):
    rat = result_action_type("COMPLETE_REGISTRATION")
    return {"spend": str(spend), "actions": [{"action_type": rat, "value": str(results)}]}


def test_evaluate_account_is_whole_account_ad_level_and_registration_only():
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True))
    campaigns = [
        {"id": "A", "name": "MTC - Watches", "effective_status": "ACTIVE"},
        {"id": "B", "name": "STOCKBLOOM | Y", "effective_status": "PAUSED"},  # whole campaign off
    ]
    ads = {
        "A": [
            _ad("over"),                          # spend 100 / 1 reg -> CPL 100 -> PAUSE
            _ad("within"),                        # spend 100 / 4 reg -> CPL 25 -> keep
            _ad("paused_ad", status="PAUSED"),    # not ACTIVE -> skipped
            _ad("purchase", event="PURCHASE"),    # wrong optimized event -> guard skips
            _ad("zero"),                          # spend 100 / 0 reg -> PAUSE (zero results)
        ],
        "B": [_ad("under_paused_campaign")],      # active ad but campaign paused -> skipped
    }
    insights = {"over": _reg_insight(100, 1), "within": _reg_insight(100, 4),
                "purchase": _reg_insight(100, 1), "zero": _reg_insight(100, 0),
                "under_paused_campaign": _reg_insight(100, 1)}

    decisions = evaluate_account(_FakeGraph(campaigns, ads, insights), settings)

    assert {d.name for d in decisions} == {"over", "within", "zero"}
    assert {d.name for d in decisions if d.should_pause} == {"over", "zero"}


def test_evaluate_account_hold_list_exempts_over_ceiling_ad():
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True,
                                   cpl_hold=["街头突击"]))
    campaigns = [{"id": "A", "name": "MTC", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("Video 6：街头突击采访"), _ad("plain_over")]}
    insights = {"Video 6：街头突击采访": _reg_insight(300, 5),  # CPL 60 > 40, but held
                "plain_over": _reg_insight(100, 1)}            # CPL 100 -> still paused
    by_name = {d.name: d for d in evaluate_account(_FakeGraph(campaigns, ads, insights), settings)}

    assert by_name["Video 6：街头突击采访"].should_pause is False
    assert by_name["Video 6：街头突击采访"].reason == MANUAL_HOLD
    assert by_name["plain_over"].should_pause is True


def test_week_to_date_cpl_window_from_thursday():
    # Jun 18 2026 is a Thursday; Jun 22 is the following Monday.
    assert _week_start_thursday(dt.date(2026, 6, 22)) == dt.date(2026, 6, 18)  # Mon -> prior Thu
    assert _week_start_thursday(dt.date(2026, 6, 18)) == dt.date(2026, 6, 18)  # Thu -> itself
    assert _week_start_thursday(dt.date(2026, 6, 24)) == dt.date(2026, 6, 18)  # Wed -> prior Thu
    s = Settings(kpi=KpiCfg(cpl_lookback="week_thu"))
    assert cpl_window(s, dt.date(2026, 6, 22)) == (None, {"since": "2026-06-18", "until": "2026-06-22"})
    assert cpl_window(Settings(kpi=KpiCfg(cpl_lookback="last_3d")), dt.date(2026, 6, 22)) == ("last_3d", None)


def test_evaluate_account_cpa_rescues_and_hard_stops():
    # CPA folded into the CPL decision (60-day window), via an injected context.
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True),
                        cpa=CpaCfg(enabled=True, hard_stop_myr=1200, conversion_days=14,
                                   min_spend_myr=1000))
    campaigns = [{"id": "A", "name": "MTC - News", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("rescue_me"), _ad("kill_me")]}
    insights = {"rescue_me": _reg_insight(300, 3),   # CPL 100 > 40 -> CPL would pause
                "kill_me": _reg_insight(100, 4)}      # CPL 25 -> CPL keeps
    ck = cpa.norm("mtc - news")
    sold = {(ck, cpa.norm("rescue_me")): 10, (ck, cpa.norm("kill_me")): 2}
    spend60 = {"rescue_me": 7000.0, "kill_me": 4000.0}  # CPA 700 (rescue) / 2000 (hard stop)

    by_name = {d.name: d for d in evaluate_account(
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=(sold, spend60))}

    assert by_name["rescue_me"].should_pause is False
    assert by_name["rescue_me"].reason == cpa.CPL_RESCUED          # over-CPL but profitable
    assert by_name["kill_me"].should_pause is True
    assert by_name["kill_me"].reason == cpa.HARD_STOP              # CPA>1200, matured -> pause
