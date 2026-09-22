"""Create an ENGAGEMENT custom audience of people who watched the page's videos — for retargeting.

Warm traffic that watched a Dr. Martin video but never registered is the cheapest lead the
account is not buying. This builds that audience: viewers of the listed videos (plus the videos
behind the listed winner ads) who watched at least PERCENT of a video in the last DAYS days.
Registrants and buyers are excluded later at the ad-set level (config + also_exclude), not here.

Meta's engagement-audience rule grammar is not documented anywhere this runner can reach, so the
script tries the known rule shapes in order and stops at the first one Meta accepts, printing
every rejection verbatim — the error text names the accepted values.

Env: ADBOT_CA_NAME, ADBOT_PAGE_ID, ADBOT_VIDEO_IDS (comma), ADBOT_AD_IDS (comma; their creatives'
video ids are added), ADBOT_PERCENT (25/50/75/95), ADBOT_DAYS (1-365). Writes ONE audience.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def _split(s: str):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    acct = s.meta.account_path
    name = os.environ.get("ADBOT_CA_NAME") or "MY · 看过视频25%+ · 30天 · retarget"
    page = os.environ.get("ADBOT_PAGE_ID") or "341825319024143"
    percent = int(os.environ.get("ADBOT_PERCENT") or 25)
    days = int(os.environ.get("ADBOT_DAYS") or 30)
    videos = _split(os.environ.get("ADBOT_VIDEO_IDS", ""))
    for ad_id in _split(os.environ.get("ADBOT_AD_IDS", "")):
        try:
            cr = (g.get_object(ad_id, "name,creative{video_id,object_story_spec}") or {}).get("creative") or {}
            vid = cr.get("video_id") or ((cr.get("object_story_spec") or {}).get("video_data") or {}).get("video_id")
            print(f"[ad] {ad_id} -> video {vid}")
            if vid and str(vid) not in videos:
                videos.append(str(vid))
        except Exception as exc:  # noqa: BLE001 - one unreadable ad must not stop the build
            print(f"[ad] {ad_id}: cannot read creative ({str(exc)[:100]})")
    videos = list(dict.fromkeys(videos))
    print(f"page {page} · {len(videos)} videos · >= {percent}% · last {days} days")
    retention = days * 86400

    event_names = {25: ["video_watched_25_percent", "video_watched_25", "video_view_25"],
                   50: ["video_watched_50_percent", "video_watched_50"],
                   75: ["video_watched_75_percent", "video_watched_75"],
                   95: ["video_watched_95_percent", "video_completed"]}[percent]

    variants = []
    for ev in event_names:
        if videos:
            variants.append(("video sources · " + ev, {
                "inclusions": {"operator": "or", "rules": [{
                    "event_sources": [{"type": "video", "id": v} for v in videos],
                    "retention_seconds": retention,
                    "filter": {"operator": "and", "filters": [{"field": "event", "operator": "eq", "value": ev}]}}]}}))
        variants.append(("page source · " + ev, {
            "inclusions": {"operator": "or", "rules": [{
                "event_sources": [{"type": "page", "id": page}],
                "retention_seconds": retention,
                "filter": {"operator": "and", "filters": [{"field": "event", "operator": "eq", "value": ev}]}}]}}))

    for label, rule in variants:
        try:
            ca = g._request("POST", f"{acct}/customaudiences", data={
                "name": name, "subtype": "ENGAGEMENT", "prefill": "true",
                "description": f"Watched >= {percent}% of a Dr. Martin video in the last {days} days (built {os.environ.get('GITHUB_RUN_ID', 'local')})",
                "rule": json.dumps(rule, ensure_ascii=False)})
            print(f"[CREATED] {ca.get('id')} via {label}")
            print("DONE " + json.dumps({"custom_audience_id": ca.get("id"), "variant": label, "videos": videos}, ensure_ascii=False))
            return
        except Exception as exc:  # noqa: BLE001 - the rejection text is the documentation
            print(f"[rejected] {label}: {str(exc)[:300]}")
    raise SystemExit("no rule variant accepted — see rejections above")


if __name__ == "__main__":
    main()
