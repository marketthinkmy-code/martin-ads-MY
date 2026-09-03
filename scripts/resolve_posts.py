"""Read-only: resolve proven ads to the page posts behind them, for existing-post rebuilds.

MY and SG advertise the same Facebook page, so any ad in either account can be rebuilt in MY
by pointing a fresh creative at the SAME page post (clone_test's post_id route) — likes,
comments and shares keep pooling on one post instead of restarting from zero. This prints the
post id (creative.effective_object_story_id) for each ad the operator wants to re-run.

Spec JSON (ADBOT_RESOLVE_SPEC, default scripts/resolve_specs/top13.json):
  accounts[]   ad-account paths to search for names (e.g. "act_1011719073600566")
  ad_ids[]     {label, id} — ads whose id is already known; read directly
  names[]      {label, needle} — ads found by a distinctive substring of their name, searched
               in every account listed, archived copies included (their posts still exist)

Nothing is written. Every match is printed — when a name matches several copies, the operator
picks the copy whose post carries the engagement.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot.commands import graph_client
from adbot.settings import REPO_ROOT, load_settings

FIELDS = ("name,effective_status,created_time,campaign{name},adset{name},"
          "creative{id,effective_object_story_id,object_story_id}")
ALL_STATUSES = ["ACTIVE", "PAUSED", "ARCHIVED", "CAMPAIGN_PAUSED", "ADSET_PAUSED",
                "DISAPPROVED", "PENDING_REVIEW", "WITH_ISSUES", "IN_PROCESS"]


def _row(label: str, ad: dict, account: str = "") -> str:
    cr = ad.get("creative") or {}
    post = cr.get("effective_object_story_id") or cr.get("object_story_id") or "-"
    return (f"{label}\n"
            f"    ad {ad.get('id')}  {account}  {ad.get('effective_status')}  "
            f"created {(ad.get('created_time') or '')[:10]}\n"
            f"    name     {ad.get('name')!r}\n"
            f"    campaign {((ad.get('campaign') or {}).get('name') or '')!r}\n"
            f"    adset    {((ad.get('adset') or {}).get('name') or '')!r}\n"
            f"    creative {cr.get('id')}  POST {post}")


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_RESOLVE_SPEC", "scripts/resolve_specs/top13.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    graph = graph_client(load_settings())
    accounts = spec.get("accounts") or []

    print("===== KNOWN IDS =====")
    for item in spec.get("ad_ids") or []:
        try:
            ad = graph.get_object(str(item["id"]), FIELDS)
            print(_row(item.get("label", item["id"]), ad))
        except Exception as exc:  # noqa: BLE001 - one bad id must not hide the rest
            print(f"{item.get('label', item['id'])}\n    !! {exc}")

    print("\n===== BY NAME =====")
    for item in spec.get("names") or []:
        needle = item["needle"]
        label = item.get("label", needle)
        found = 0
        for account in accounts:
            try:
                rows = graph._get_all(f"{account}/ads", {
                    "fields": FIELDS, "limit": 100,
                    "effective_status": json.dumps(ALL_STATUSES),
                    "filtering": json.dumps([{"field": "name", "operator": "CONTAIN",
                                              "value": needle}]),
                })
            except Exception as exc:  # noqa: BLE001
                print(f"{label}\n    !! {account}: {exc}")
                continue
            # newest first — the most recent copy is usually the one whose post is live
            rows.sort(key=lambda a: a.get("created_time") or "", reverse=True)
            for ad in rows:
                print(_row(label, ad, account))
                found += 1
        if not found:
            print(f"{label}\n    !! no ad containing {needle!r} in {accounts}")
    print("\nDONE")


if __name__ == "__main__":
    main()
