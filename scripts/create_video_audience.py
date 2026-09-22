"""Create an ENGAGEMENT custom audience of people who watched the page's videos — for retargeting.

Warm traffic that watched a Dr. Martin video but never registered is the cheapest lead the
account is not buying. This builds that audience: viewers of the listed videos (plus the videos
behind the listed winner ads) who watched at least PERCENT of a video in the last DAYS days.
Registrants and buyers are excluded later at the ad-set level (config + also_exclude), not here.

Meta accepts video-engagement audiences ONLY in the legacy rule format
    {"object_id": <page_id>, "event_name": <event>, "video_ids": [...]}
— the event_sources/inclusions grammar is rejected with "This audience rule format is not
available for video engagement Custom Audience" (run 2026-09-22). The threshold events are
video_watched (3s), video_view_10s, video_view_15s, video_view_25_percent, video_view_50_percent,
video_view_75_percent, video_completed (95%); a few alternative spellings are still tried and every
rejection is printed verbatim. Retention is tried as a top-level retention_days, then inside the
rule, then omitted. The created audience is read back (retention, rule, size) so the log records
what was actually built, and a wrong retention is corrected with an update when Meta allows it.

Env: ADBOT_CA_NAME, ADBOT_PAGE_ID, ADBOT_VIDEO_IDS (comma), ADBOT_AD_IDS (comma; their creatives'
video ids are added), ADBOT_PERCENT (25/50/75/95), ADBOT_DAYS (1-365). Writes ONE audience.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

EVENTS = {
    3: ["video_watched"],
    10: ["video_view_10s"],
    15: ["video_view_15s"],
    25: ["video_view_25_percent", "video_watched_25_percent", "video_view_25", "video_p25_watched"],
    50: ["video_view_50_percent", "video_watched_50_percent", "video_view_50", "video_p50_watched"],
    75: ["video_view_75_percent", "video_watched_75_percent", "video_view_75", "video_p75_watched"],
    95: ["video_completed", "video_view_95_percent", "video_watched_95_percent", "video_p95_watched"],
}

READ_FIELDS = ("name,subtype,retention_days,rule,approximate_count_lower_bound,"
               "approximate_count_upper_bound,operation_status,delivery_status,time_created")


def _split(s: str):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def read_back(g, ca_id: str) -> dict:
    try:
        info = g.get_object(ca_id, READ_FIELDS) or {}
        print("[audience] " + json.dumps(info, ensure_ascii=False)[:1500])
        return info
    except Exception as exc:  # noqa: BLE001 - the audience exists; a failed read is only a missing log line
        print(f"[audience] read-back failed: {str(exc)[:200]}")
        return {}


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
    events = EVENTS.get(percent) or EVENTS[25]
    desc = (f"Watched >= {percent}% of a Dr. Martin video in the last {days} days "
            f"(built {os.environ.get('GITHUB_RUN_ID', 'local')})")

    scopes = [("listed videos", videos)] if videos else []
    scopes.append(("all page videos", None))
    for scope_label, vids in scopes:
        for ev in events:
            base = {"object_id": page, "event_name": ev}
            if vids:
                base["video_ids"] = vids
            retention_variants = (
                ("retention_days", dict(base), {"retention_days": str(days)}),
                ("retention_seconds in rule", {**base, "retention_seconds": days * 86400}, {}),
                ("no retention", dict(base), {}),
            )
            for ret_label, rule, extra in retention_variants:
                label = f"{scope_label} · {ev} · {ret_label}"
                data = {"name": name, "subtype": "ENGAGEMENT", "prefill": "true", "description": desc,
                        "rule": json.dumps(rule, ensure_ascii=False)}
                data.update(extra)
                try:
                    ca = g._request("POST", f"{acct}/customaudiences", data=data)
                except Exception as exc:  # noqa: BLE001 - the rejection text is the documentation
                    msg = str(exc)
                    print(f"[rejected] {label}: {msg[:300]}")
                    if "retention" in msg.lower():
                        continue        # same event, next retention spelling
                    break               # event/video problem: a retention change cannot cure it
                ca_id = str(ca.get("id"))
                print(f"[CREATED] {ca_id} via {label}")
                info = read_back(g, ca_id)
                rd = info.get("retention_days")
                if rd is not None and str(rd) != str(days):
                    try:
                        g._request("POST", ca_id, data={"retention_days": str(days)})
                        print(f"[audience] retention_days {rd} -> {days} updated")
                        info = read_back(g, ca_id)
                    except Exception as exc:  # noqa: BLE001 - report, the operator decides
                        print(f"[audience] retention_days is {rd}, update to {days} rejected: {str(exc)[:200]}")
                print("DONE " + json.dumps({"custom_audience_id": ca_id, "variant": label,
                                            "retention_days": info.get("retention_days"),
                                            "videos": videos}, ensure_ascii=False))
                return
    raise SystemExit("no rule variant accepted — see rejections above")


if __name__ == "__main__":
    main()
