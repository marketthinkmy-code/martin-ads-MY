"""One-shot: change the daily budget of campaigns / ad sets, WITHOUT stopping delivery.

Why this exists: the interactive connector's ads_update_entity force-pauses whatever it edits
(`status_forced_to_paused: true`), so raising or lowering a budget through it silently turns the
campaign off — that is how "基因决定论" went dark after a routine RM50->RM100 change. Posting the
budget field on its own via the system-user token changes the budget and nothing else.

Reads ADBOT_BUDGET_SPEC as a comma-separated list of `<entity_id>=<MYR per day>` pairs, e.g.

    ADBOT_BUDGET_SPEC="120246582171650575=100,120247917024380575=150"

Meta stores budgets in minor units, so RM100 is sent as 10000. CBO campaigns carry the budget at
campaign level and ad sets under them reject it — point this at whichever level actually holds it.
Each entity is isolated; the job exits non-zero if any failed. The status field is never touched.
"""
from __future__ import annotations

import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def parse_spec(raw: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"bad ADBOT_BUDGET_SPEC entry {chunk!r} — expected <id>=<MYR>")
        entity_id, myr = (p.strip() for p in chunk.split("=", 1))
        amount = float(myr)
        if amount <= 0:
            raise SystemExit(f"refusing non-positive budget for {entity_id}: {amount}")
        out.append((entity_id, amount))
    return out


def main() -> None:
    pairs = parse_spec(os.environ.get("ADBOT_BUDGET_SPEC", ""))
    if not pairs:
        print("ADBOT_BUDGET_SPEC is empty — nothing to do.")
        return
    graph = graph_client(load_settings())
    ok = 0
    for entity_id, myr in pairs:
        cents = int(round(myr * 100))
        try:
            before = graph.get_object(entity_id, "name,daily_budget,effective_status")
            graph._request("POST", entity_id, data={"daily_budget": cents})
            after = graph.get_object(entity_id, "name,daily_budget,effective_status")
            print(f"[BUDGET] {entity_id} {before.get('name', '?')!r} "
                  f"{before.get('daily_budget')} -> {after.get('daily_budget')} "
                  f"(RM{myr:,.2f}/day) · status still {after.get('effective_status')}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every entity, judge the job at the end
            print(f"[FAILED] {entity_id}: {exc}")
    print(f"done: {ok}/{len(pairs)} budgets set")
    if ok < len(pairs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
