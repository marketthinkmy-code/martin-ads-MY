"""One-shot: set a list of Meta entities (campaigns / ad sets / ads) to ACTIVE.

Reads a comma-separated id list from ADBOT_ACTIVATE_IDS. The Graph API infers the entity
type from the id, so campaigns, ad sets, and ads can be mixed freely. Runs on the adbot
system-user token with the usual throttle-retry, so it works even when the operator's
interactive identity is advertising-restricted. Each activation is isolated — one failure
never blocks the rest (same policy as weekly_on) — and the job exits non-zero if any failed.
"""
from __future__ import annotations

import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def main() -> None:
    ids = [x.strip() for x in os.environ.get("ADBOT_ACTIVATE_IDS", "").split(",") if x.strip()]
    if not ids:
        print("ADBOT_ACTIVATE_IDS is empty — nothing to do.")
        return
    graph = graph_client(load_settings())
    ok = 0
    for entity_id in ids:
        try:
            graph.update_status(entity_id, "ACTIVE")
            print(f"[ACTIVATED] {entity_id}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every entity, judge the job at the end
            print(f"[FAILED]    {entity_id}: {exc}")
    print(f"done: {ok}/{len(ids)} activated")
    if ok < len(ids):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
