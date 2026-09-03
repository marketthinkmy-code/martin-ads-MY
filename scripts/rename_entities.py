"""One-shot: rename campaigns / ad sets / ads WITHOUT touching anything else.

Why this exists: the interactive connector's ads_update_entity force-pauses whatever it edits,
so a rename through it would stop delivery — the whole point here is to mark ACTIVE winners
(🌟 prefix) while they keep running. Posting the name field alone via the system-user token
changes the name and nothing else: status, budget and schedule are never written.

Renaming is safe for sheet attribution: the Paid Student List joins key on cpa.ad_key /
monitor_cpl._mkey, which NFKC-normalize, casefold and strip every non-word character — an emoji
prefix normalizes away, so historical UTM rows keep matching the renamed entity, and future
leads carry the new name, which normalizes to the same key.

Reads a JSON spec from ADBOT_RENAME_SPEC (default scripts/rename_specs/star_winners.json) —
names carry CJK / emoji / pipes, which an env-var pair format would mangle:

    {"renames": [{"id": "120240000000000000", "name": "🌟 New Name"}, ...]}

Each rename is isolated — one failure never blocks the rest — and the job exits non-zero if any
failed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot.commands import graph_client
from adbot.settings import REPO_ROOT, load_settings


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_RENAME_SPEC", "scripts/rename_specs/star_winners.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        raise SystemExit(f"no spec at {spec_path}")
    renames = json.loads(spec_path.read_text(encoding="utf-8")).get("renames") or []
    if not renames:
        print("spec has no renames — nothing to do.")
        return
    graph = graph_client(load_settings())
    ok = 0
    for item in renames:
        entity_id, name = str(item["id"]), str(item["name"])
        try:
            before = graph.get_object(entity_id, "name,effective_status")
            graph._request("POST", entity_id, data={"name": name})
            after = graph.get_object(entity_id, "name,effective_status")
            print(f"[RENAMED] {entity_id} {before.get('name', '?')!r} -> {after.get('name')!r}"
                  f" · status still {after.get('effective_status')}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every entity, judge the job at the end
            print(f"[FAILED] {entity_id}: {exc}")
    print(f"done: {ok}/{len(renames)} renamed")
    if ok < len(renames):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
