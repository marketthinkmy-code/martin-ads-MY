import textwrap

import pytest

from adbot.caption_source import load_from_notion
from adbot.creative_groups import CAROUSEL, SINGLE_IMAGE, VIDEO, Asset, Unit
from adbot.settings import load_settings

CONFIG = textwrap.dedent("""
meta:
  ad_account_id: "act_123"
  page_id: "PAGE9"
  pixel_id: "PIX9"
  conversion_event: "LEAD"
  conversion_domain: "landing.example"
  lead_destination: { type: "WEBSITE", link_url: "https://landing.example/x" }
  budget: { daily_amount_myr: 250, adset_min_spend_myr: 50 }
  targeting: { countries: ["MY"], age_min: 25, age_max: 65, advantage_audience: 1 }
naming:
  prefix: "MARTIN-MY"
notion:
  enabled: true
  database_id: "DB"
  captions_source: true
  require_status: "Approved"
""")


def _settings(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return load_settings(cfg)


def _units():
    return [
        Unit("image_1", SINGLE_IMAGE, [Asset("f1", "image-1.png", "image/png", meta_id="h1")]),
        Unit("carousel", CAROUSEL, [Asset("f2", "1.png", "image/png", meta_id="h2"),
                                    Asset("f3", "2.png", "image/png", meta_id="h3")]),
    ]


def _page(pid, caption, headline, status="Approved", edited="2026-06-22T00:00:00Z"):
    return {"id": pid, "last_edited_time": edited, "properties": {
        "Caption": {"rich_text": [{"plain_text": caption}]},
        "Headline": {"rich_text": [{"plain_text": headline}]},
        "Status": {"status": {"name": status}},
    }}


def _page_with_title(pid, caption, headline, title, status="Approved"):
    p = _page(pid, caption, headline, status)
    p["properties"]["Title"] = {"title": [{"plain_text": title}]}
    return p


def _card(text):
    return {"type": "numbered_list_item",
            "numbered_list_item": {"rich_text": [{"plain_text": text}]}}


class FakeNotion:
    def __init__(self, rows, blocks=None):
        self._rows = rows
        self._blocks = blocks or {}

    def query_by_content_id(self, db, content_id, prop="Content ID"):
        return self._rows.get(content_id, [])

    def get_block_children(self, page_id):
        return self._blocks.get(page_id, [])


def test_load_from_notion_reads_copy_and_parses_cards(tmp_path):
    s = _settings(tmp_path)
    rows = {
        "image_1": [_page("p1", "cap one", "🔴 head one")],
        "carousel": [_page("p2", "cap car", "🔴 head car")],
    }
    blocks = {"p2": [_card("卡一 — 描述一"), _card("卡二 — 描述二"),
                     {"type": "paragraph", "paragraph": {"rich_text": []}}]}
    caps = load_from_notion(FakeNotion(rows, blocks), s, _units())

    assert caps["image_1"]["caption"] == "cap one"
    assert caps["image_1"]["headline"] == "🔴 head one"
    cards = caps["carousel"]["carousel_card_texts"]
    assert cards == [{"name": "卡一", "description": "描述一"},
                     {"name": "卡二", "description": "描述二"}]


def test_ad_name_derived_from_notion_title(tmp_path):
    s = _settings(tmp_path)
    s.notion.require_status = ""
    # Title '马丁MY | <angle> | <descriptor>' -> ad name '<KindLabel>：<descriptor>' (no month prefix).
    rows = {"image_1": [_page_with_title("p1", "cap", "🔴 h", "马丁MY | 体质 | 书包·姿势")]}
    caps = load_from_notion(FakeNotion(rows), s, [_units()[0]])  # _units()[0] is SINGLE_IMAGE
    assert caps["image_1"]["name"] == "Single image：书包·姿势"


def test_no_title_means_no_ad_name(tmp_path):
    s = _settings(tmp_path)
    s.notion.require_status = ""
    rows = {"image_1": [_page("p1", "cap", "🔴 h")]}  # no Title property
    caps = load_from_notion(FakeNotion(rows), s, [_units()[0]])
    assert "name" not in caps["image_1"]  # build falls back to its default 'PREFIX | content_id'


def test_require_status_excludes_unapproved(tmp_path):
    s = _settings(tmp_path)  # require_status == "Approved"
    rows = {
        "image_1": [_page("p1", "cap one", "head one", status="Approved")],
        "carousel": [_page("p2", "cap car", "head car", status="In Review")],
    }
    with pytest.raises(RuntimeError) as e:
        load_from_notion(FakeNotion(rows), s, _units())
    assert "carousel" in str(e.value) and "image_1" not in str(e.value)


def test_prefers_newest_matching_row(tmp_path):
    s = _settings(tmp_path)
    s.notion.require_status = ""  # any status
    rows = {
        "image_1": [_page("old", "old cap", "old", edited="2026-06-01T00:00:00Z"),
                    _page("new", "new cap", "new", edited="2026-06-20T00:00:00Z")],
    }
    caps = load_from_notion(FakeNotion(rows), s, [_units()[0]])
    assert caps["image_1"]["caption"] == "new cap"


def test_missing_caption_text_is_an_error(tmp_path):
    s = _settings(tmp_path)
    s.notion.require_status = ""
    rows = {"image_1": [_page("p1", "", "head only")]}  # empty caption body
    with pytest.raises(RuntimeError):
        load_from_notion(FakeNotion(rows), s, [_units()[0]])


def test_non_strict_stubs_missing_instead_of_raising(tmp_path):
    s = _settings(tmp_path)  # require_status == "Approved"
    rows = {"image_1": [_page("p1", "cap one", "head one", status="Approved")]}
    # carousel has no approved row -> stubbed (not raised) in dry-run mode
    caps = load_from_notion(FakeNotion(rows), s, _units(), strict=False)
    assert caps["image_1"]["caption"] == "cap one"
    assert "no approved Notion caption" in caps["carousel"]["caption"]
