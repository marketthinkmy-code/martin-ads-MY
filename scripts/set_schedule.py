"""Set the start (and optionally end) time of ad sets, WITHOUT touching their status.

"Schedule this for Wednesday" is two separate facts to Meta: a future start_time, and a status of
ACTIVE. A PAUSED ad set with a start_time never starts on its own — it just sits there. So the
safe order is always: set the time with this script, VERIFY it came back, then activate. Doing it
the other way round means a live ad set with no start date, spending immediately.

Status is deliberately never written here, and the interactive connector is avoided entirely
because ads_update_entity force-pauses whatever it edits.

ADBOT_SCHEDULE_SPEC is a comma-separated list of `<entity_id>=<start>[|<end>]`, where the times
are ISO-8601 WITH an explicit offset so there is no doubt about the timezone:

    ADBOT_SCHEDULE_SPEC="120248429838370575=2026-08-20T00:00:00+0800"

A start time already in the past is refused: Meta would silently begin delivery at once.
"""
from __future__ import annotations

import datetime as dt
import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def parse_when(raw: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(raw)
    if when.tzinfo is None:
        raise SystemExit(f"{raw!r} has no UTC offset — refusing to guess the timezone")
    return when


def main() -> None:
    spec = os.environ.get("ADBOT_SCHEDULE_SPEC", "").strip()
    if not spec:
        print("ADBOT_SCHEDULE_SPEC is empty — nothing to do.")
        return
    now = dt.datetime.now(dt.timezone.utc)
    graph = graph_client(load_settings())
    ok = 0
    entries = [c.strip() for c in spec.split(",") if c.strip()]
    for entry in entries:
        if "=" not in entry:
            raise SystemExit(f"bad entry {entry!r} — expected <id>=<start>[|<end>]")
        entity_id, times = (p.strip() for p in entry.split("=", 1))
        start_raw, _, end_raw = times.partition("|")
        start = parse_when(start_raw)
        if start <= now:
            raise SystemExit(f"{entity_id}: start {start.isoformat()} is not in the future — "
                             f"scheduling it would begin delivery immediately")
        fields = {"start_time": start.isoformat()}
        if end_raw:
            end = parse_when(end_raw)
            if end <= start:
                raise SystemExit(f"{entity_id}: end {end.isoformat()} is not after the start")
            fields["end_time"] = end.isoformat()
        try:
            graph._request("POST", entity_id, data=fields)
            back = graph.get_object(entity_id, "name,start_time,end_time,effective_status")
            print(f"[SCHEDULED] {entity_id} {back.get('name', '?')!r}")
            print(f"            start={back.get('start_time')} end={back.get('end_time', '-')} "
                  f"· status still {back.get('effective_status')}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every entity, judge the job at the end
            print(f"[FAILED] {entity_id}: {exc}")
    print(f"done: {ok}/{len(entries)} scheduled")
    if ok < len(entries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
