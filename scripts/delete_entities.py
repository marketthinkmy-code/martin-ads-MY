"""Delete campaigns / ad sets / ads by id — with a guard against deleting anything that ran.

Deletion is the one action here that cannot be undone, so this refuses any entity with lifetime
spend above zero. Something that has spent has performance history attached to it, and that
history is the only record of what was learned; if it should stop, pause it instead. Only
never-delivered mistakes — a campaign built wrong and rebuilt correctly — belong here.

Reads a comma-separated id list from ADBOT_DELETE_IDS. Each entity is isolated; the job exits
non-zero if any failed.
"""
from __future__ import annotations

import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def lifetime_spend(graph, entity_id: str) -> float:
    """Total spend ever recorded against an entity; 0.0 when it never delivered."""
    try:
        rows = graph._get_all(f"{entity_id}/insights",
                              {"date_preset": "maximum", "fields": "spend"})
    except Exception as exc:  # noqa: BLE001 - an unreadable entity is not a licence to delete it
        raise SystemExit(f"{entity_id}: cannot read insights, refusing to delete ({exc})")
    return sum(float(r.get("spend") or 0) for r in rows)


def main() -> None:
    ids = [x.strip() for x in os.environ.get("ADBOT_DELETE_IDS", "").split(",") if x.strip()]
    if not ids:
        print("ADBOT_DELETE_IDS is empty — nothing to do.")
        return
    graph = graph_client(load_settings())
    ok = 0
    for entity_id in ids:
        try:
            before = graph.get_object(entity_id, "name,effective_status")
            spent = lifetime_spend(graph, entity_id)
            if spent > 0:
                print(f"[REFUSED]  {entity_id} {before.get('name', '?')!r} — has spent "
                      f"MYR{spent:,.2f}. Pause it instead; deleting discards its history.")
                continue
            graph._request("POST", entity_id, data={"status": "DELETED"})
            print(f"[DELETED]  {entity_id} {before.get('name', '?')!r} (never spent)")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every entity, judge the job at the end
            print(f"[FAILED]   {entity_id}: {exc}")
    print(f"done: {ok}/{len(ids)} deleted")
    if ok < len(ids):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
