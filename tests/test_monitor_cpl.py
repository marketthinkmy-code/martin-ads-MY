import math

import datetime as dt

from adbot import cpa
from adbot.monitor_cpl import (CPL_GRACE_NEW, GRACE_BRAKE, INSUFFICIENT_SPEND, MANUAL_HOLD,
                               NO_RESULTS_YET, OVER_THRESHOLD, WITHIN_THRESHOLD, ZERO_RESULTS,
                               cpl_window, decide, evaluate_account, extract_results, parse_metrics,
                               result_action_type, webinars_since, _week_start_thursday)
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

    def account_insights(self, account_path, *, level, fields, date_preset=None, time_range=None):
        # Batched whole-account insights (level=ad): one row per ad, ad_id folded into each row
        # (mirrors the Graph API, which returns ad_id as a column on level=ad insights).
        return [dict(row, ad_id=aid) for aid, row in self._insights.items()]


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


def test_run_isolates_a_failed_pause(monkeypatch):
    # A Meta write error on ONE ad (e.g. the account briefly blocking writes) must NOT crash the
    # whole monitor run — a crash fails the scheduled job and emails the operator. The other
    # over-CPL ads still get paused; the failed one is left for the next run to retry.
    from adbot import monitor_cpl, state
    monkeypatch.setattr(state, "append_pause_log", lambda *a, **k: None)  # no disk writes in the test

    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True))
    campaigns = [{"id": "A", "name": "MTC", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("bad"), _ad("good")]}                 # both CPL 100 > 40 -> both flagged to pause
    insights = {"bad": _reg_insight(100, 1), "good": _reg_insight(100, 1)}

    class _GraphRaisingOnBad(_FakeGraph):
        def update_status(self, entity_id, status):
            if entity_id == "bad":
                raise RuntimeError("temporarily blocked from performing this action")
            return {"id": entity_id, "status": status}

    result = monitor_cpl.run(_GraphRaisingOnBad(campaigns, ads, insights), settings, dry_run=False)
    assert result["paused"] == 1 and result["failed"] == 1  # 'good' paused; 'bad' isolated, no crash


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
    ck = cpa.ad_key("mtc - news")
    sold = {(ck, cpa.ad_key("rescue_me")): 10, (ck, cpa.ad_key("kill_me")): 2}
    spend60 = {"rescue_me": 7000.0, "kill_me": 4000.0}  # CPA 700 (rescue) / 2000 (hard stop)

    by_name = {d.name: d for d in evaluate_account(
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=(sold, spend60))}

    assert by_name["rescue_me"].should_pause is False
    assert by_name["rescue_me"].reason == cpa.CPL_RESCUED          # over-CPL but profitable
    assert by_name["kill_me"].should_pause is True
    assert by_name["kill_me"].reason == cpa.HARD_STOP              # CPA>1200, matured -> pause


def test_cpa_match_is_width_and_punctuation_robust():
    # A sale logged with a full-width colon and no spaces still credits the Meta ad whose name
    # uses a half-width colon and spaces — ad_key (NFKC + strip punctuation) folds both to one key.
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True),
                        cpa=CpaCfg(enabled=True, hard_stop_myr=1200, conversion_days=14,
                                   min_spend_myr=1000))
    campaigns = [{"id": "A", "name": "MARTIN-MY | Scale", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("MAR Video 5: 林書豪 story")]}                  # Meta: half-width colon + spaces
    insights = {"MAR Video 5: 林書豪 story": _reg_insight(300, 3)}   # CPL 100 > 40 -> would pause
    sold = {(cpa.ad_key("martin-my | scale"),                       # sheet: full-width ：, no spaces
             cpa.ad_key("mar video 5：林書豪story")): 10}
    spend60 = {"MAR Video 5: 林書豪 story": 7000.0}                  # CPA 700 -> healthy rescue
    d = {x.name: x for x in evaluate_account(
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=(sold, spend60))}
    assert d["MAR Video 5: 林書豪 story"].cpa_sales == 10           # matched despite width/space
    assert d["MAR Video 5: 林書豪 story"].reason == cpa.CPL_RESCUED


def test_cpa_match_keeps_campaign_so_other_campaign_sales_dont_leak():
    # The same ad name selling under a DIFFERENT campaign must NOT credit this ad — this is why
    # campaign stays in the key: MY must never inherit an SG campaign's sales on a shared name.
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True),
                        cpa=CpaCfg(enabled=True, hard_stop_myr=1200, conversion_days=14,
                                   min_spend_myr=1000))
    campaigns = [{"id": "A", "name": "[MY] Housewife", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("MAR Video 5: 林書豪 story")]}
    insights = {"MAR Video 5: 林書豪 story": _reg_insight(300, 3)}   # CPL 100 -> would pause
    sold = {(cpa.ad_key("[sg] scale-cbo"),                          # the sale is under an SG campaign
             cpa.ad_key("mar video 5：林書豪story")): 5}
    spend60 = {"MAR Video 5: 林書豪 story": 300.0}
    d = {x.name: x for x in evaluate_account(
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=(sold, spend60))}
    assert d["MAR Video 5: 林書豪 story"].cpa_sales == 0            # SG sale did NOT leak into MY
    assert d["MAR Video 5: 林書豪 story"].should_pause is True      # so the CPL pause stands


def test_evaluate_account_cpl_grace_exempts_brand_new_ad():
    # A brand-new ad over CPL but still pulling registrations is exempt from the CPL pause until it
    # is old enough for its webinar sign-ups to convert. A same-aged ZERO-lead ad still pauses, and
    # an aged over-CPL ad still pauses — grace only shields OVER_THRESHOLD within the window.
    settings = Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                        kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                                   cpl_lookback="last_3d", pause_zero_lead_after_spend=True,
                                   cpl_grace_days=7))
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    fresh = (today - dt.timedelta(days=3)).isoformat()    # inside the 7-day grace
    aged = (today - dt.timedelta(days=30)).isoformat()    # past the grace
    campaigns = [{"id": "A", "name": "MARTIN-MY | Test", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("new_over", created_time=fresh),      # CPL 60>40, has leads, young -> graced
                 _ad("new_zero", created_time=fresh),      # young but 0 leads -> still pauses
                 _ad("old_over", created_time=aged)]}      # CPL 60>40, aged -> pauses
    insights = {"new_over": _reg_insight(300, 5), "new_zero": _reg_insight(100, 0),
                "old_over": _reg_insight(300, 5)}
    by_name = {d.name: d for d in evaluate_account(_FakeGraph(campaigns, ads, insights), settings)}

    assert by_name["new_over"].should_pause is False
    assert by_name["new_over"].reason == CPL_GRACE_NEW
    assert by_name["new_zero"].should_pause is True        # zero leads is never graced
    assert by_name["old_over"].should_pause is True        # aged over-CPL still pauses


def _grace_brake_settings():
    # threshold 40 -> brake at CPL > 60 (1.5x); spend cap 500.
    return Settings(meta=MetaCfg(conversion_event="COMPLETE_REGISTRATION"),
                    kpi=KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80, cpl_lookback="last_3d",
                               pause_zero_lead_after_spend=True, cpl_grace_days=7,
                               cpl_grace_max_cpl_multiple=1.5, cpl_grace_max_spend_myr=500),
                    cpa=CpaCfg(enabled=True, hard_stop_myr=1200, conversion_days=14,
                               min_spend_myr=1000))


def test_grace_brake_stops_a_young_ad_burning_with_no_sales():
    # The Hook 4 failure: a young ad stayed "protected" at CPL 93 until it had burned ~RM1,200.
    # Grace must be withdrawn once a no-sale ad blows past the CPL multiple OR the spend cap;
    # a mildly-over young ad still gets its runway.
    settings = _grace_brake_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    fresh = (today - dt.timedelta(days=3)).isoformat()
    campaigns = [{"id": "A", "name": "MARTIN-MY | Test", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("mild", created_time=fresh),        # CPL 50: over 40, under 60 -> graced
                 _ad("cpl_blowout", created_time=fresh),  # CPL 100 > 60 -> braked
                 _ad("cash_burn", created_time=fresh)]}   # CPL 50 but spent 600 >= 500 -> braked
    insights = {"mild": _reg_insight(300, 6), "cpl_blowout": _reg_insight(300, 3),
                "cash_burn": _reg_insight(600, 12)}
    by_name = {d.name: d for d in evaluate_account(          # empty ctx = no matched sales
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=({}, {}))}

    assert by_name["mild"].should_pause is False
    assert by_name["mild"].reason == CPL_GRACE_NEW
    assert by_name["cpl_blowout"].should_pause is True
    assert by_name["cpl_blowout"].reason == GRACE_BRAKE
    assert by_name["cash_burn"].should_pause is True
    assert by_name["cash_burn"].reason == GRACE_BRAKE


def test_grace_brake_never_touches_an_ad_with_sales_at_acceptable_cpa():
    # The operator's rule: "CPL RM65, if there are sales and CPA is okay, don't close it."
    # Real sales at CPA <= hard stop are rescued BEFORE the brake can apply — even when the ad is
    # young and would otherwise trip both brake triggers (huge CPL, spend past the cap).
    settings = _grace_brake_settings()
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    fresh = (today - dt.timedelta(days=3)).isoformat()
    campaigns = [{"id": "A", "name": "MARTIN-MY | Winner", "effective_status": "ACTIVE"}]
    ads = {"A": [_ad("earner", created_time=fresh)]}
    insights = {"earner": _reg_insight(900, 9)}            # CPL 100 > 60 AND spend 900 > 500 cap
    sold = {(cpa.ad_key("martin-my | winner"), cpa.ad_key("earner")): 3}
    spend60 = {"earner": 1500.0}                            # CPA 500 -> comfortably under 1200

    d = {x.name: x for x in evaluate_account(
        _FakeGraph(campaigns, ads, insights), settings, cpa_ctx=(sold, spend60))}

    assert d["earner"].should_pause is False
    assert d["earner"].reason == cpa.CPL_RESCUED           # sales win over both brake triggers


# ── webinar-clock grace ────────────────────────────────────────────────────────────────────
# Sales only close on webinar nights, so the grace counts webinars survived, not days lived.

WEBINAR_KPI = KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80,
                     webinar_weekday=2, cpl_grace_webinars=1, webinar_settle_days=1)


def test_webinars_since_counts_only_settled_webinars():
    # Wed 2026-08-05 is a webinar; born Mon 08-03, judged Thu 08-06 -> that Wednesday has run and
    # settled (1 day), so the ad has had its chance.
    assert webinars_since(dt.date(2026, 8, 3), dt.date(2026, 8, 6), WEBINAR_KPI) == 1
    # Judged the morning after: the sheet is not filled in yet, so it does not count.
    assert webinars_since(dt.date(2026, 8, 3), dt.date(2026, 8, 5), WEBINAR_KPI) == 0
    # Born ON a webinar day: that night's buyers were never its registrants — count from the next.
    assert webinars_since(dt.date(2026, 8, 5), dt.date(2026, 8, 7), WEBINAR_KPI) == 0
    assert webinars_since(dt.date(2026, 8, 5), dt.date(2026, 8, 13), WEBINAR_KPI) == 1
    # Three weeks on, three webinars.
    assert webinars_since(dt.date(2026, 7, 16), dt.date(2026, 8, 7), WEBINAR_KPI) == 3
    # No webinar weekday configured -> caller falls back to the day-count grace.
    assert webinars_since(dt.date(2026, 8, 3), dt.date(2026, 8, 6), KPI) is None


def test_calendar_age_and_webinar_clock_disagree():
    """The 马六甲 case: old enough for the day-grace to expire, but no webinar has settled."""
    born, judged = dt.date(2026, 7, 30), dt.date(2026, 8, 5)   # Thu -> the Wed webinar is same-day
    kpi = KpiCfg(cpl_threshold_myr=40, cpl_min_spend_myr=80, cpl_grace_days=3,
                 webinar_weekday=2, cpl_grace_webinars=1, webinar_settle_days=1)
    assert (judged - born).days > kpi.cpl_grace_days          # day-clock says "judge it"
    assert webinars_since(born, judged, kpi) == 0             # webinar-clock says "not yet"
