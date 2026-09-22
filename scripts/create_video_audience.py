"""Create an ENGAGEMENT custom audience of people who watched the page's videos — for retargeting.

Warm traffic that watched a Dr. Martin video but never registered is the cheapest lead the
account is not buying. This builds that audience: viewers who watched at least PERCENT of any
listed video in the last DAYS days. Registrants and buyers are excluded later at the ad-set level
(config + also_exclude), not here.

Meta's accepted video-engagement rule — read back from the operator's own Ads Manager audiences
on 2026-09-22 — is a JSON LIST with one entry per video, the VIDEO id in object_id and the PAGE
id in context_id, and retention_days as a top-level field:
    [{"event_name": "video_view_50_percent", "object_id": "<VIDEO_ID>", "context_id": "<PAGE_ID>"}, ...]
Threshold events: video_view_25_percent / video_view_50_percent / video_view_75_percent
(video_completed for 95% and video_watched for 3s are Meta's documented names, unverified here).
The event_sources/inclusions grammar is rejected outright for video audiences, and the
{"object_id": <page>, "video_ids": [...]} shape fails with a generic "(#2654) Failed to create
custom audience" — both recorded in earlier runs; they remain as fallbacks only.

Videos = ADBOT_VIDEO_IDS + the videos behind ADBOT_AD_IDS' creatives + (ADBOT_INCLUDE_EXISTING=1)
every video already listed in the account's existing video-engagement audiences, so the new
audience is a superset of the ones built by hand. Existing engagement audiences are printed
first (name, id, retention, size, video count) so the log documents what the account holds.

Env: ADBOT_CA_NAME, ADBOT_PAGE_ID, ADBOT_VIDEO_IDS (comma), ADBOT_AD_IDS (comma),
ADBOT_PERCENT (25/50/75/95), ADBOT_DAYS (1-365), ADBOT_INCLUDE_EXISTING (1/0). Writes ONE audience.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

EVENTS = {
    3: ["video_watched"],
    25: ["video_view_25_percent"],
    50: ["video_view_50_percent"],
    75: ["video_view_75_percent"],
    95: ["video_completed", "video_view_95_percent"],
}
MAX_VIDEOS = 200          # Ads Manager's own cap per video audience

LIST_FIELDS = ("id,name,subtype,rule,retention_days,approximate_count_lower_bound,"
               "approximate_count_upper_bound,time_created,time_updated,delivery_status")
READ_FIELDS = LIST_FIELDS + ",operation_status"


def _split(s: str):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _parse_rule(raw):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw or "null")
    except ValueError:
        return None


def existing_video_audiences(g, acct: str, page: str):
    """Print the account's ENGAGEMENT audiences; return the video ids of the video-view ones."""
    try:
        rows = g._get_all(f"{acct}/customaudiences", {"fields": LIST_FIELDS, "limit": 100})
    except Exception as exc:  # noqa: BLE001 - a failed listing must not stop the build
        print(f"[existing] listing failed: {str(exc)[:200]}")
        return []
    vids = []
    for r in rows:
        if r.get("subtype") != "ENGAGEMENT":
            continue
        rule = _parse_rule(r.get("rule"))
        entries = rule if isinstance(rule, list) else []
        events = sorted({str(e.get("event_name")) for e in entries if isinstance(e, dict)})
        mine = [str(e["object_id"]) for e in entries
                if isinstance(e, dict) and str(e.get("event_name", "")).startswith("video")
                and str(e.get("context_id") or page) == str(page) and e.get("object_id")]
        print(f"[existing] {r.get('id')}  {r.get('name')!r}  retention {r.get('retention_days')}d  "
              f"size {r.get('approximate_count_lower_bound')}-{r.get('approximate_count_upper_bound')}  "
              f"{r.get('delivery_status')}  videos {len(mine)}  events {events}  updated {r.get('time_updated')}")
        vids.extend(mine)
    return list(dict.fromkeys(vids))


def read_back(g, ca_id: str) -> dict:
    try:
        info = g.get_object(ca_id, READ_FIELDS) or {}
        shown = dict(info)
        if isinstance(shown.get("rule"), str) and len(shown["rule"]) > 300:
            shown["rule"] = shown["rule"][:300] + f"... ({len(info['rule'])} chars)"
        print("[audience] " + json.dumps(shown, ensure_ascii=False))
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
    include_existing = (os.environ.get("ADBOT_INCLUDE_EXISTING") or "1").strip() not in ("0", "false", "no")

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
    from_existing = existing_video_audiences(g, acct, page)
    if include_existing:
        added = [v for v in from_existing if v not in videos]
        print(f"[existing] {len(added)} more videos taken from the account's video audiences")
        videos.extend(added)
    videos = list(dict.fromkeys(videos))[:MAX_VIDEOS]
    print(f"page {page} · {len(videos)} videos · >= {percent}% · last {days} days")
    if not videos:
        raise SystemExit("no videos to build from")
    events = EVENTS.get(percent) or [f"video_view_{percent}_percent"]
    desc = (f"Watched >= {percent}% of a Dr. Martin video in the last {days} days; "
            f"{len(videos)} videos (built {os.environ.get('GITHUB_RUN_ID', 'local')})")

    for ev in events:
        per_video = [{"event_name": ev, "object_id": v, "context_id": page} for v in videos]
        legacy = {"object_id": page, "event_name": ev, "video_ids": videos}
        variants = (
            ("per-video list + retention_days", per_video, {"retention_days": str(days)}),
            ("per-video list, no retention", per_video, {}),
            ("page object + video_ids, no retention", legacy, {}),
            ("page object + video_ids + retention_seconds", {**legacy, "retention_seconds": days * 86400}, {}),
        )
        for label, rule, extra in variants:
            label = f"{ev} · {label}"
            data = {"name": name, "subtype": "ENGAGEMENT", "prefill": "true", "description": desc,
                    "rule": json.dumps(rule, ensure_ascii=False)}
            data.update(extra)
            try:
                ca = g._request("POST", f"{acct}/customaudiences", data=data)
            except Exception as exc:  # noqa: BLE001 - the rejection text is the documentation
                detail = json.dumps(getattr(exc, "payload", None) or {}, ensure_ascii=False)[:600]
                print(f"[rejected] {label}: {str(exc)[:200]}  payload={detail}")
                continue
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
                                        "video_count": len(videos), "videos": videos}, ensure_ascii=False))
            return
    raise SystemExit("no rule variant accepted — see rejections above")


if __name__ == "__main__":
    main()
