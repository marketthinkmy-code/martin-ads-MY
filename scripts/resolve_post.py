"""Resolve a Facebook post / reel reference into the object_story_id an ad creative needs.

A reel's URL carries its VIDEO id, but an ad creative wants the PAGE POST id in `<page>_<post>`
form, and for reels those two are not reliably the same number. Guessing wrong produces a creative
that either fails to create or points at nothing, so this asks Meta directly and reports what it
finds — including whether the post is eligible to be promoted at all.

Env:
  ADBOT_POST_REF      a facebook.com URL or a bare numeric id
  ADBOT_TRY_CREATIVE  "1" to settle it by actually minting a creative against the guessed
                      object_story_id. Reading a page post needs pages_read_engagement, which an
                      ads token does not carry — but CREATING a creative only needs ads
                      permissions, so the write succeeds or fails where the read cannot even ask.
                      A creative on its own has no ad set and cannot spend.
"""
from __future__ import annotations

import os
import re

from adbot.commands import graph_client
from adbot.settings import load_settings


def extract_id(ref: str) -> str:
    ids = re.findall(r"(\d{6,})", ref or "")
    if not ids:
        raise SystemExit(f"no numeric id found in {ref!r}")
    return max(ids, key=len)


def probe(graph, label: str, obj: str, fields: str) -> dict | None:
    try:
        data = graph.get_object(obj, fields)
        print(f"[OK]   {label}  {obj}")
        for k, v in data.items():
            if k == "id":
                continue
            # The caption is the reason to run this at all when re-posting an organic video —
            # print it whole, not clipped to a preview.
            limit = 4000 if k in ("description", "message", "title") else 160
            print(f"         {k}: {str(v)[:limit]}")
        return data
    except Exception as exc:  # noqa: BLE001 - probing; a miss is information, not a failure
        print(f"[miss] {label}  {obj}: {str(exc)[:170]}")
        return None


def main() -> None:
    ref = os.environ.get("ADBOT_POST_REF", "").strip()
    if not ref:
        raise SystemExit("ADBOT_POST_REF is required")
    vid = extract_id(ref)
    s = load_settings()
    graph = graph_client(s)
    page = s.meta.page_id
    print(f"ref={ref}\nextracted id={vid}\npage={page}\n")

    probe(graph, "as video ", vid,
          "id,title,description,created_time,length,permalink_url,from")
    story = f"{page}_{vid}"
    post = probe(graph, "as post  ", story,
                 "id,message,created_time,permalink_url,is_eligible_for_promotion,"
                 "promotable_id,status_type")
    if post:
        print(f"\nobject_story_id = {story}")
        if post.get("is_eligible_for_promotion") is False:
            print("WARNING: Meta says this post is NOT eligible for promotion.")
        return

    # The page-post read is gated behind pages_read_engagement, which an ads token does not carry.
    # /promotable_posts is the ads-side equivalent — same objects, ads permissions — so it answers
    # the only question that matters here: which posts can this account actually put money behind.
    print("\n[fallback] /promotable_posts (ads permission, not pages_read_engagement)")
    try:
        posts = graph._get_all(f"{page}/promotable_posts", {
            "fields": "id,message,created_time,permalink_url,is_eligible_for_promotion,"
                      "promotable_id,status_type",
            "is_published": "true", "limit": 50})
    except Exception as exc:  # noqa: BLE001 - not fatal: the creative attempt below can still
        print(f"  unavailable: {str(exc)[:180]}")   # answer the question this read could not
        posts = []

    print(f"  {len(posts)} promotable post(s)")
    hit = None
    for r in posts:
        pid, promotable = str(r.get("id", "")), str(r.get("promotable_id", ""))
        match = vid in (pid.split("_")[-1], promotable) or vid in str(r.get("permalink_url", ""))
        if match:
            hit = r
        print(f"  {pid:<40} {str(r.get('created_time'))[:10]} "
              f"elig={r.get('is_eligible_for_promotion')} "
              f"{str(r.get('message') or '')[:44].replace(chr(10), ' ')}"
              f"{'   <-- MATCH' if match else ''}")

    if not hit and os.environ.get("ADBOT_TRY_CREATIVE") == "1":
        story = f"{page}_{vid}"
        print(f"\n[try] minting a creative against {story} — a creative alone cannot spend")
        try:
            fields = {"object_story_id": story}
            if s.meta.url_tags:
                fields["url_tags"] = s.meta.url_tags
            cre = graph.create_adcreative(s.meta.account_path, **fields)
            back = graph.get_object(cre["id"], "id,effective_object_story_id,title,body")
            print(f"[OK] creative {cre['id']}")
            print(f"     effective_object_story_id: {back.get('effective_object_story_id')}")
            print(f"\nUSABLE object_story_id = {story}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[FAILED] {str(exc)[:400]}")
            print("\nThis post cannot be used as an existing-post ad from the API.")
            return

    if hit:
        print(f"\nobject_story_id = {hit['id']}")
        if hit.get("is_eligible_for_promotion") is False:
            print("WARNING: Meta says this post is NOT eligible for promotion.")
    else:
        print(f"\nNo promotable post carries video id {vid}. A reel can be missing from this list "
              f"when it has not finished processing, or when it was posted in a way Meta will not "
              f"promote; check the page's Reels tab for a 'Boost' option.")


if __name__ == "__main__":
    main()
