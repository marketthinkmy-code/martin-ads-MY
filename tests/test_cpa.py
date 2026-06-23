import datetime as dt
import math

from adbot import cpa


def test_norm_collapses_and_casefolds():
    assert cpa.norm("  MTC - News  - 14/1/2026 ") == "mtc - news - 14/1/2026"
    assert cpa.norm("Video\\_8") == "video_8"


def test_find_columns_by_header_name():
    header = ["Name", "Phone Number", "Email", "Created date", "Experience", "Source",
              "UTM Placement", "UTM Campaign", "UTM Ads Set", "UTM Ads Name"]
    cols = cpa.find_columns(header)
    assert (cols["date"], cols["campaign"], cols["adset"], cols["ad"]) == (3, 7, 8, 9)


def test_find_columns_handles_reordering_and_amount():
    header = ["Date", "UTM Ad Name", "UTM Ad Set", "UTM Campaign", "Purchase Amount"]
    cols = cpa.find_columns(header)
    assert (cols["date"], cols["ad"], cols["adset"], cols["campaign"], cols["amount"]) == (0, 1, 2, 3, 4)


def test_parse_date_formats():
    assert cpa.parse_date("14/1/2026") == dt.date(2026, 1, 14)        # D/M/Y (MY locale)
    assert cpa.parse_date("2026-06-15") == dt.date(2026, 6, 15)       # ISO
    assert cpa.parse_date("1/14/2026") == dt.date(2026, 1, 14)        # M/D/Y fallback
    assert cpa.parse_date("14-01-2026") == dt.date(2026, 1, 14)       # dashes
    assert cpa.parse_date("14.1.2026") == dt.date(2026, 1, 14)        # dots
    assert cpa.parse_date("14 Jan 2026") == dt.date(2026, 1, 14)      # month abbr
    assert cpa.parse_date("January 14, 2026") == dt.date(2026, 1, 14)  # month name
    assert cpa.parse_date("14/1/2026 10:30") == dt.date(2026, 1, 14)  # time suffix
    serial = (dt.date(2026, 1, 14) - dt.date(1899, 12, 30)).days
    assert cpa.parse_date(str(serial)) == dt.date(2026, 1, 14)        # Sheets serial
    assert cpa.parse_date("") is None and cpa.parse_date("n/a") is None


def test_parse_sales_and_windows():
    values = [
        ["Name", "Created date", "UTM Campaign", "UTM Ads Set", "UTM Ads Name", "Purchase Amount"],
        ["A", "15/6/2026", "MTC - News - 14/1/2026", "News", "Video 1", "RM2,399"],
        ["B", "14/1/2026", "MTC - News - 14/1/2026", "News", "Video 1", "2099"],
        ["C", "20/6/2026", "MTC - Officer - 14/1/2026", "Officer", "Video 4", ""],
        ["", ""],  # blank -> skipped
    ]
    sales, cols, _header = cpa.parse_sales(values, default_price=2399.0)
    assert len(sales) == 3 and cols["campaign"] == 2
    by_ad_amount = {s.ad: s.amount for s in sales}
    assert by_ad_amount["video 4"] == 2399.0   # blank amount -> default price

    win = cpa.count_windows(sales, dt.date(2026, 6, 21))
    assert win["life"] == 3
    assert win["14d"] == 2 and win["30d"] == 2 and win["60d"] == 2   # only the two June sales


def test_attribute_groups_by_entity():
    today = dt.date(2026, 6, 21)
    sales = [
        cpa.Sale(dt.date(2026, 6, 15), "mtc - news", "news", "video 1", 2399),
        cpa.Sale(dt.date(2026, 6, 16), "mtc - news", "news", "video 1", 2399),
        cpa.Sale(dt.date(2026, 1, 14), "mtc - news", "news", "video 2", 2399),
    ]
    by_ad, _by_adset, by_campaign = cpa.attribute(sales, today)
    assert by_ad[("mtc - news", "news", "video 1")]["life"] == 2
    assert by_ad[("mtc - news", "news", "video 1")]["30d"] == 2
    assert by_campaign["mtc - news"]["life"] == 3
    assert by_campaign["mtc - news"]["30d"] == 2


def test_combined_decision_policy():
    t = cpa.CpaTiers(healthy_max=800, max_acceptable=960, hard_stop=1200)
    cd = cpa.combined_decision

    # CPA rescues an over-CPL ad with healthy real sales (the held-ad case)
    assert cd(cpl_pause=True, cpl_reason="cpl_over_threshold", cpa_value=745, cpa_sales=15,
              cpa_spend=11181, age_days=60, tiers=t, min_spend=1000) == (False, cpa.CPL_RESCUED)
    # rescue still applies when CPA is marginal but profitable (<= hard stop)
    assert cd(cpl_pause=True, cpl_reason="cpl_over_threshold", cpa_value=1100, cpa_sales=3,
              cpa_spend=3300, age_days=30, tiers=t, min_spend=1000) == (False, cpa.CPL_RESCUED)
    # proven hard stop auto-pauses even when CPL is fine
    assert cd(cpl_pause=False, cpl_reason="within_threshold", cpa_value=1737, cpa_sales=8,
              cpa_spend=15000, age_days=60, tiers=t, min_spend=1000) == (True, cpa.HARD_STOP)
    # …but never inside the conversion window (too new)
    assert cd(cpl_pause=False, cpl_reason="within_threshold", cpa_value=1737, cpa_sales=8,
              cpa_spend=15000, age_days=10, tiers=t, conversion_days=14, min_spend=1000) \
        == (False, "within_threshold")
    # …and never below the min spend needed to judge
    assert cd(cpl_pause=False, cpl_reason="within_threshold", cpa_value=1300, cpa_sales=1,
              cpa_spend=1300, age_days=30, tiers=t, min_spend=2000) == (False, "within_threshold")
    # zero matched sales never auto-pauses on CPA, and never rescues — CPL decision stands
    assert cd(cpl_pause=True, cpl_reason="zero_results_over_min_spend", cpa_value=math.inf,
              cpa_sales=0, cpa_spend=5000, age_days=30, tiers=t, min_spend=1000) \
        == (True, "zero_results_over_min_spend")
    assert cd(cpl_pause=False, cpl_reason="within_threshold", cpa_value=600, cpa_sales=10,
              cpa_spend=6000, age_days=40, tiers=t, min_spend=1000) == (False, "within_threshold")


def test_cpa_value_and_tiers():
    t = cpa.CpaTiers(healthy_max=800, max_acceptable=960, hard_stop=1200)
    assert cpa.cpa(1600, 2) == 800
    assert cpa.cpa(100, 0) == math.inf
    assert cpa.cpa(0, 0) is None
    assert cpa.cpa_tier(700, t) == cpa.KEEP
    assert cpa.cpa_tier(900, t) == cpa.MONITOR
    assert cpa.cpa_tier(1100, t) == cpa.PAUSE_CANDIDATE
    assert cpa.cpa_tier(1300, t) == cpa.HARD_STOP
    assert cpa.cpa_tier(None, t) == cpa.NO_SALES
    assert cpa.cpa_tier(math.inf, t) == cpa.NO_SALES
