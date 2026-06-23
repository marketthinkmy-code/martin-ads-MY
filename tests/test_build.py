import textwrap

import adbot.state as state
from adbot.build_1_1_10 import build, creative_spec
from adbot.creative_groups import CAROUSEL, SINGLE_IMAGE, VIDEO, Asset, Unit
from adbot.settings import load_settings

CONFIG = textwrap.dedent("""
meta:
  ad_account_id: "act_123"
  page_id: "PAGE9"
  pixel_id: "PIX9"
  conversion_event: "LEAD"
  conversion_domain: "landing.example"
  call_to_action: "SIGN_UP"
  lead_destination: { type: "WEBSITE", link_url: "https://landing.example/x" }
  budget: { daily_amount_myr: 250, adset_min_spend_myr: 50 }
  targeting: { countries: ["MY"], age_min: 25, age_max: 65, advantage_audience: 1 }
  build: { creatives_per_adset: 10, activate_after_build: true }
naming:
  prefix: "STOCKBLOOM"
""")


def _settings(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return load_settings(cfg)


def _units():
    return [
        Unit("vid", VIDEO, [Asset("f1", "a.mp4", "video/mp4", meta_id="vid_1")]),
        Unit("img", SINGLE_IMAGE, [Asset("f2", "b.jpg", "image/jpeg", meta_id="hash_2")]),
        Unit("car", CAROUSEL, [Asset("f3", "1.jpg", "image/jpeg", meta_id="h3"),
                               Asset("f4", "2.jpg", "image/jpeg", meta_id="h4")]),
    ]


CAPTIONS = {
    "vid": {"caption": "cap v", "headline": "head v"},
    "img": {"caption": "cap i", "headline": "head i"},
    "car": {"caption": "cap c", "headline": "head c",
            "carousel_card_texts": [{"name": "n1", "description": "d1"},
                                    {"name": "n2", "description": "d2"}]},
}


class FakeGraph:
    def __init__(self):
        self.calls = []
        self._n = 0

    def _id(self, p):
        self._n += 1
        return f"{p}{self._n}"

    def create_campaign(self, account, **f):
        self.calls.append(("campaign", f))
        return {"id": "camp1"}

    def create_adset(self, account, **f):
        self.calls.append(("adset", f))
        return {"id": "adset1"}

    def create_adcreative(self, account, **f):
        self.calls.append(("creative", f))
        return {"id": self._id("cr")}

    def create_ad(self, account, **f):
        self.calls.append(("ad", f))
        return {"id": self._id("ad")}

    def update_status(self, eid, status):
        self.calls.append(("status", eid, status))

    def get_video_thumbnail(self, video_id):
        return "https://example.test/thumb.jpg"


def test_creative_spec_video(tmp_path):
    s = _settings(tmp_path)
    spec = creative_spec(s, _units()[0], CAPTIONS["vid"])
    vd = spec["object_story_spec"]["video_data"]
    assert vd["video_id"] == "vid_1" and vd["message"] == "cap v"
    assert vd["call_to_action"]["type"] == "SIGN_UP"


def test_creative_spec_single_image(tmp_path):
    s = _settings(tmp_path)
    spec = creative_spec(s, _units()[1], CAPTIONS["img"])
    ld = spec["object_story_spec"]["link_data"]
    assert ld["image_hash"] == "hash_2" and ld["name"] == "head i"


def test_creative_spec_carousel(tmp_path):
    s = _settings(tmp_path)
    spec = creative_spec(s, _units()[2], CAPTIONS["car"])
    cards = spec["object_story_spec"]["link_data"]["child_attachments"]
    assert [c["image_hash"] for c in cards] == ["h3", "h4"]
    assert cards[0]["name"] == "n1" and cards[1]["description"] == "d2"


def test_build_creates_and_activates(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    s = _settings(tmp_path)
    g = FakeGraph()
    entities = build(g, s, _units(), CAPTIONS, dry_run=False)

    campaign = next(c[1] for c in g.calls if c[0] == "campaign")
    assert campaign["daily_budget"] == 25000
    assert campaign["objective"] == "OUTCOME_LEADS"
    assert campaign["status"] == "PAUSED"
    assert campaign["bid_strategy"] == "LOWEST_COST_WITHOUT_CAP"

    adset = next(c[1] for c in g.calls if c[0] == "adset")
    assert "daily_min_spend_target" not in adset  # single-ad-set CBO uses the full campaign budget
    assert adset["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert adset["promoted_object"] == {"pixel_id": "PIX9", "custom_event_type": "LEAD"}
    assert adset["targeting"]["targeting_automation"]["advantage_audience"] == 1

    ads = [c[1] for c in g.calls if c[0] == "ad"]
    assert len(ads) == 3
    assert all(a["status"] == "PAUSED" for a in ads)
    assert all(a["conversion_domain"] == "landing.example" for a in ads)

    activations = [(c[1], c[2]) for c in g.calls if c[0] == "status"]
    assert ("camp1", "ACTIVE") in activations
    assert ("adset1", "ACTIVE") in activations
    assert sum(1 for _, st in activations if st == "ACTIVE") == 5  # campaign+adset+3 ads

    assert entities["campaign_id"] == "camp1"
    assert len(entities["ad_ids"]) == 3
    assert entities["activated"] is True


def test_creative_spec_includes_url_tags(tmp_path):
    s = _settings(tmp_path)
    s.meta.url_tags = "utm_source={{adset.name}}&utm_content={{ad.name}}"
    spec = creative_spec(s, _units()[0], CAPTIONS["vid"])
    assert spec["url_tags"] == "utm_source={{adset.name}}&utm_content={{ad.name}}"


def test_build_dry_run_creates_nothing(tmp_path):
    s = _settings(tmp_path)
    g = FakeGraph()
    result = build(g, s, _units(), CAPTIONS, dry_run=True)
    assert result["dry_run"] is True
    assert g.calls == []


def test_run_all_gives_each_subfolder_a_unique_state_key_and_angle_label(tmp_path, monkeypatch):
    """build_all must name each campaign for its angle AND give it a UNIQUE state_key — otherwise
    every subfolder after the first reuses the first campaign's id (shared-state bug) and piles
    its ads into the wrong campaign."""
    import adbot.commands.build as buildmod

    s = _settings(tmp_path)
    s.drive.creatives_folder_id = "PARENT"
    s.meta.build.state_key = "entities_angles"

    class FakeDrive:
        def list_children(self, parent):
            assert parent == "PARENT"
            return [{"id": "F1", "name": "营养·方向错了"}, {"id": "F2", "name": "黄金期·封板"}]

        def is_folder(self, child):
            return True

    captured = []
    monkeypatch.setattr(buildmod, "drive_client", lambda settings: FakeDrive())
    monkeypatch.setattr(buildmod, "run", lambda st, dry_run=False: captured.append(
        (st.drive.creatives_folder_id, st.meta.build.label, st.meta.build.state_key)) or {"ok": True})

    out = buildmod.run_all(s, dry_run=True)
    assert out["campaigns"] == 2
    assert captured == [
        ("F1", "营养·方向错了", "entities_angles__F1"),
        ("F2", "黄金期·封板", "entities_angles__F2"),
    ]
