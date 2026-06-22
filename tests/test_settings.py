import textwrap

from adbot.settings import has_placeholder, load_settings

CONFIG = textwrap.dedent("""
meta:
  ad_account_id: "act_123"
  page_id: "456"
  pixel_id: "789"
  conversion_event: "LEAD"
  lead_destination: { type: "WEBSITE", link_url: "https://landing.example/x" }
  budget: { daily_amount_myr: 250, adset_min_spend_myr: 50 }
  targeting: { countries: ["MY"], age_min: 25, age_max: 65, advantage_audience: 1 }
naming:
  prefix: "STOCKBLOOM"
kpi:
  cpl_threshold_myr: 40
""")


def _write(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return cfg


def test_budget_converts_to_cents(tmp_path):
    s = load_settings(_write(tmp_path))
    assert s.meta.budget.daily_amount_cents == 25000
    assert s.meta.budget.adset_min_spend_cents == 5000


def test_targeting_spec_is_broad_my_25plus(tmp_path):
    s = load_settings(_write(tmp_path))
    spec = s.meta.targeting.to_spec()
    assert spec["geo_locations"]["countries"] == ["MY"]
    assert spec["age_min"] == 25
    assert spec["targeting_automation"]["advantage_audience"] == 1


def test_account_path_and_promoted_object(tmp_path):
    s = load_settings(_write(tmp_path))
    assert s.meta.account_path == "act_123"
    assert s.meta.promoted_object == {"pixel_id": "789", "custom_event_type": "LEAD"}


def test_campaign_name_uses_prefix(tmp_path):
    s = load_settings(_write(tmp_path))
    assert s.naming.campaign_name("1-1-10") == "STOCKBLOOM | 1-1-10"


def test_has_placeholder():
    assert has_placeholder("act_XXXXXXXX")
    assert has_placeholder("https://your-landing-page.example/x")
    assert not has_placeholder("act_123")


def test_conversion_domain_bare_strips_scheme_and_path():
    from adbot.settings import MetaCfg
    m = MetaCfg(conversion_domain="https://example.com/")
    assert m.conversion_domain_bare == "example.com"


def test_base64_service_account_takes_precedence(tmp_path, monkeypatch):
    import base64 as _b64
    raw = '{"type":"service_account","project_id":"p"}'
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", _b64.b64encode(raw.encode()).decode())
    s = load_settings(_write(tmp_path))
    assert s.secrets.google_sa_json == raw
