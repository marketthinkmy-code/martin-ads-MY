"""One-shot: switch ad sets to MANUAL placements WITHOUT touching anything else.

Why this exists: the 2026-10-09 placement read showed FB Feed at CPL RM38 and In-stream at RM32,
while FB Reels ran RM68, FB Stories RM82, IG Reels RM75 and IG Stories / Threads bought nothing
(14 days, the active campaigns). The interactive connector's ads_update_entity force-pauses whatever
it edits, so placement edits must go through the system-user token — and an ad set's `targeting`
is replaced whole on write, so this reads the live targeting first and sends it back unchanged
except for the placement keys (operator, 2026-10-09: 其他 ad setting 保持不动). Status, budget
and the Malaysia declaration are never written. Editing targeting restarts the ad set's learning.

Env:
  ADBOT_PLACEMENT_ADSETS   comma-separated ad set ids
  ADBOT_PLACEMENT_PRESET   fb_feed_instream_reels (default) | fb_feed_instream | fb_feed
  ADBOT_PLACEMENT_DRY_RUN  "1" = print the before/after and write nothing
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

PRESETS = {
    "fb_feed_instream_reels": {"publisher_platforms": ["facebook"],
                               "facebook_positions": ["feed", "instream_video", "facebook_reels"]},
    "fb_feed_instream": {"publisher_platforms": ["facebook"],
                         "facebook_positions": ["feed", "instream_video"]},
    "fb_feed": {"publisher_platforms": ["facebook"], "facebook_positions": ["feed"]},
}

# Settable targeting keys carried over verbatim; everything Meta derives on read (effective_*,
# page_types, dt_consolidation_state, targeting_optimization, age_range ...) is dropped.
_KEEP = ("geo_locations", "excluded_geo_locations", "age_min", "age_max", "genders", "locales",
         "flexible_spec", "exclusions", "custom_audiences", "excluded_custom_audiences",
         "targeting_automation", "targeting_relaxation_types", "brand_safety_content_filter_levels",
         "device_platforms", "user_os", "user_device")
_PLACEMENT_KEYS = ("publisher_platforms", "facebook_positions", "instagram_positions",
                   "messenger_positions", "audience_network_positions", "threads_positions",
                   "whatsapp_positions")


def _ids_only(rows):
    return [{"id": str(r["id"])} for r in rows if isinstance(r, dict) and r.get("id")]


def rebuild(t: dict, preset: dict) -> dict:
    out = {k: t[k] for k in _KEEP if k in t}
    # Meta reads back "frequently_in" as an expansion; only home / recent are accepted on write.
    geo = out.get("geo_locations") or {}
    if geo.get("location_types"):
        geo["location_types"] = [x for x in geo["location_types"] if x in ("home", "recent")] or ["home", "recent"]
    for k in ("custom_audiences", "excluded_custom_audiences"):
        if out.get(k):
            out[k] = _ids_only(out[k])
    for k in _PLACEMENT_KEYS:
        out.pop(k, None)
    out.update(preset)
    return out


def _effective(t: dict) -> str:
    return (f"platforms={t.get('effective_publisher_platforms')} "
            f"fb={t.get('effective_facebook_positions')} ig={t.get('effective_instagram_positions')} "
            f"an={t.get('effective_audience_network_positions')}")


def main() -> None:
    ids = [x.strip() for x in os.environ.get("ADBOT_PLACEMENT_ADSETS", "").split(",") if x.strip()]
    preset_name = os.environ.get("ADBOT_PLACEMENT_PRESET", "fb_feed_instream_reels")
    dry = os.environ.get("ADBOT_PLACEMENT_DRY_RUN", "0") == "1"
    if preset_name not in PRESETS:
        raise SystemExit(f"unknown preset {preset_name!r}; choose from {sorted(PRESETS)}")
    if not ids:
        print("ADBOT_PLACEMENT_ADSETS is empty — nothing to do.")
        return
    preset = PRESETS[preset_name]
    graph = graph_client(load_settings())
    ok = 0
    for aid in ids:
        try:
            before = graph.get_object(aid, "name,effective_status,daily_budget,targeting")
            t = before.get("targeting") or {}
            new = rebuild(t, preset)
            print(f"[{aid}] {before.get('name')!r} status={before.get('effective_status')} budget={before.get('daily_budget')}")
            print(f"    before: {_effective(t)}")
            print(f"    after : platforms={new['publisher_platforms']} fb={new['facebook_positions']} (preset {preset_name})")
            if dry:
                print("    DRY RUN — not written"); ok += 1; continue
            graph._request("POST", aid, data={"targeting": json.dumps(new)})
            after = graph.get_object(aid, "effective_status,daily_budget,targeting")
            t2 = after.get("targeting") or {}
            print(f"    now   : {_effective(t2)} status={after.get('effective_status')} budget={after.get('daily_budget')}")
            same = all(t.get(k) == t2.get(k) for k in ("age_min", "age_max", "genders", "locales", "flexible_spec"))
            print(f"    audience unchanged: {same}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every ad set, judge the job at the end
            print(f"[FAILED] {aid}: {exc}")
    print(f"done: {ok}/{len(ids)} ad sets{' (dry run)' if dry else ''}")
    if ok < len(ids):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
