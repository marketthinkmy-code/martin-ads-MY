"""Create an ENGAGEMENT custom audience of people who watched the page's videos — for retargeting.

Warm traffic that watched a Dr. Martin video but never registered is the cheapest lead the
account is not buying. This builds that audience: viewers who watched at least PERCENT of any
listed video in the last DAYS days. Registrants and buyers are excluded later at the ad-set level
(config + also_exclude), not here.

Meta's accepted video-engagement rule — read back from the operator's own Ads Manager audiences
on 2026-09-22 — is a JSON LIST with one entry per video, the VIDEO id in object_id and the PAGE
id in context_id, and retention_days as a top-level field:
    [{"event_name": "video_view_25_percent", "object_id": "<VIDEO_ID>", "context_id": "<PAGE_ID>"}, ...]
Threshold events: video_view_25_percent / video_view_50_percent / video_view_75_percent.
Only PAGE-associated videos are eligible: a video uploaded to the ad account for a creative is
refused with "(#2654) No Page or New Page Experience Association ... video <id>", so creatives are
resolved through their effective_object_story_id to the page post's attached video, and any id
Meta still names as ineligible is dropped and the create retried. (The event_sources/inclusions
grammar and the {"object_id": <page>, "video_ids": [...]} shape are both rejected — earlier runs.)

Videos = ADBOT_VIDEO_IDS + the page-post videos behind ADBOT_AD_IDS and behind every creative_id in
ADBOT_CREATIVE_SPECS (clone specs) + (ADBOT_INCLUDE_EXISTING=1) every video already listed in the
account's existing video-engagement audiences, so the new audience is a superset of the hand-built
ones. Existing engagement audiences are printed first (name, id, retention, size, video count).

Env: ADBOT_CA_NAME, ADBOT_PAGE_ID, ADBOT_VIDEO_IDS, ADBOT_AD_IDS, ADBOT_CREATIVE_SPECS (comma lists),
ADBOT_PERCENT (25/50/75), ADBOT_DAYS (1-365), ADBOT_INCLUDE_EXISTING (1/0). Writes ONE audience.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from adbot.commands import graph_client
from adbot.settings import REPO_ROOT, load_settings

MAX_VIDEOS = 200          # Ads Manager's own cap per video audience
MAX_ATTEMPTS = 80         # one create attempt per ineligible video, at most

CA_FIELDS = ("id,name,subtype,rule,retention_days,approximate_count_lower_bound,"
             "approximate_count_upper_bound,delivery_status,operation_status,time_created,time_updated")


def _split(s: str):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _parse_rule(raw):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw or "null")
    except ValueError:
        return None


def _post_video(g, story_id: str):
    """The video attached to a page post (the page-associated id Meta accepts)."""
    post = g.get_object(story_id, "attachments{type,media_type,target{id}}") or {}
    for att in ((post.get("attachments") or {}).get("data") or []):
        if "video" in str(att.get("type", "")).lower() or str(att.get("media_type", "")).lower() == "video":
            return str(((att.get("target") or {}).get("id")) or "") or None
    return None


def videos_from_creatives(g, creative_ids, ad_ids):
    """Resolve ads/creatives to their page-post video (preferred) or raw creative video."""
    found = []
    creatives = list(dict.fromkeys(str(c) for c in creative_ids))
    for ad_id in ad_ids:
        try:
            cr = (g.get_object(ad_id, "creative{id}") or {}).get("creative") or {}
            if cr.get("id") and str(cr["id"]) not in creatives:
                creatives.append(str(cr["id"]))
            print(f"[ad] {ad_id} -> creative {cr.get('id')}")
        except Exception as exc:  # noqa: BLE001 - one unreadable ad must not stop the build
            print(f"[ad] {ad_id}: cannot read creative ({str(exc)[:100]})")
    for cid in creatives:
        try:
            c = g.get_object(cid, "effective_object_story_id,video_id,object_story_spec{video_data{video_id}}") or {}
        except Exception as exc:  # noqa: BLE001
            print(f"[creative] {cid}: cannot read ({str(exc)[:100]})")
            continue
        raw = c.get("video_id") or ((c.get("object_story_spec") or {}).get("video_data") or {}).get("video_id")
        story = c.get("effective_object_story_id")
        post_vid = None
        if story:
            try:
                post_vid = _post_video(g, str(story))
            except Exception as exc:  # noqa: BLE001
                print(f"[post] {story}: cannot read attachments ({str(exc)[:100]})")
        print(f"[creative] {cid} -> video {raw} · post {story} -> video {post_vid}")
        pick = post_vid or (str(raw) if raw else None)
        if pick and pick not in found:
            found.append(pick)
    return found


def creative_ids_from_specs(paths):
    ids = []
    for p in paths:
        path = Path(p) if Path(p).is_absolute() else REPO_ROOT / p
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[spec] {p}: cannot read ({str(exc)[:100]})")
            continue
        plans = spec.get("adsets") or [spec]
        for plan in plans:
            for ad in (plan.get("ads") or spec.get("ads") or []):
                if ad.get("creative_id"):
                    ids.append(str(ad["creative_id"]))
    return list(dict.fromkeys(ids))


def existing_video_audiences(g, acct: str, page: str):
    """Print the account's ENGAGEMENT audiences; return the video ids of the video-view ones."""
    try:
        rows = g._get_all(f"{acct}/customaudiences", {"fields": "id,name,subtype", "limit": 100})
    except Exception as exc:  # noqa: BLE001 - a failed listing must not stop the build
        print(f"[existing] listing failed: {str(exc)[:200]}")
        return []
    vids = []
    for r in rows:
        if r.get("subtype") != "ENGAGEMENT":
            continue
        try:
            info = g.get_object(str(r["id"]), CA_FIELDS) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"[existing] {r.get('id')} {r.get('name')!r}: cannot read ({str(exc)[:100]})")
            continue
        rule = _parse_rule(info.get("rule"))
        entries = rule if isinstance(rule, list) else []
        events = sorted({str(e.get("event_name")) for e in entries if isinstance(e, dict)})
        mine = [str(e["object_id"]) for e in entries
                if isinstance(e, dict) and str(e.get("event_name", "")).startswith("video")
                and str(e.get("context_id") or page) == str(page) and e.get("object_id")]
        print(f"[existing] {info.get('id')}  {info.get('name')!r}  retention {info.get('retention_days')}d  "
              f"size {info.get('approximate_count_lower_bound')}-{info.get('approximate_count_upper_bound')}  "
              f"{info.get('delivery_status')}  videos {len(mine)}  events {events}  updated {info.get('time_updated')}")
        vids.extend(mine)
    return list(dict.fromkeys(vids))


def read_back(g, ca_id: str) -> dict:
    try:
        info = g.get_object(ca_id, CA_FIELDS) or {}
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
    event = f"video_view_{percent}_percent"

    videos = _split(os.environ.get("ADBOT_VIDEO_IDS", ""))
    creative_ids = creative_ids_from_specs(_split(os.environ.get("ADBOT_CREATIVE_SPECS", "")))
    print(f"[spec] {len(creative_ids)} creative ids from the clone specs")
    for v in videos_from_creatives(g, creative_ids, _split(os.environ.get("ADBOT_AD_IDS", ""))):
        if v not in videos:
            videos.append(v)
    from_existing = existing_video_audiences(g, acct, page)
    if include_existing:
        added = [v for v in from_existing if v not in videos]
        print(f"[existing] {len(added)} more videos taken from the account's video audiences")
        videos.extend(added)
    videos = list(dict.fromkeys(videos))[:MAX_VIDEOS]
    print(f"page {page} · {len(videos)} videos · >= {percent}% ({event}) · last {days} days")
    if not videos:
        raise SystemExit("no videos to build from")

    dropped = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not videos:
            break
        rule = [{"event_name": event, "object_id": v, "context_id": page} for v in videos]
        desc = (f"Watched >= {percent}% of a Dr. Martin video in the last {days} days; "
                f"{len(videos)} videos (built {os.environ.get('GITHUB_RUN_ID', 'local')})")
        data = {"name": name, "subtype": "ENGAGEMENT", "prefill": "true", "description": desc,
                "retention_days": str(days), "rule": json.dumps(rule, ensure_ascii=False)}
        try:
            ca = g._request("POST", f"{acct}/customaudiences", data=data)
        except Exception as exc:  # noqa: BLE001 - the rejection text is the documentation
            msg = str(exc)
            m = re.search(r"with video (\d+)", msg)
            if m and m.group(1) in videos:
                videos.remove(m.group(1))
                dropped.append(m.group(1))
                print(f"[drop] video {m.group(1)} is not page-associated — {len(videos)} left")
                continue
            detail = json.dumps(getattr(exc, "payload", None) or {}, ensure_ascii=False)[:600]
            print(f"[rejected] attempt {attempt}: {msg[:200]}  payload={detail}")
            break
        ca_id = str(ca.get("id"))
        print(f"[CREATED] {ca_id} with {len(videos)} videos after {attempt} attempt(s); dropped {len(dropped)}: {dropped}")
        info = read_back(g, ca_id)
        rd = info.get("retention_days")
        if rd is not None and str(rd) != str(days):
            try:
                g._request("POST", ca_id, data={"retention_days": str(days)})
                print(f"[audience] retention_days {rd} -> {days} updated")
                info = read_back(g, ca_id)
            except Exception as exc:  # noqa: BLE001 - report, the operator decides
                print(f"[audience] retention_days is {rd}, update to {days} rejected: {str(exc)[:200]}")
        print("DONE " + json.dumps({"custom_audience_id": ca_id, "event": event,
                                    "retention_days": info.get("retention_days"),
                                    "video_count": len(videos), "videos": videos,
                                    "dropped": dropped}, ensure_ascii=False))
        return
    raise SystemExit(f"no audience created — dropped {len(dropped)} ineligible videos, see log above")


if __name__ == "__main__":
    main()
