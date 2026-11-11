"""Add EXCLUDED custom audiences to existing ad sets and change nothing else.

Spec (ADBOT_EXCL_SPEC, JSON): {"adset_ids": [...], "exclude_ids": [...]}
Reads each ad set's live targeting, keeps every writable key as it is now (age, geo, locales,
included audiences, placements / page types, Advantage flags), appends the exclusions that are
missing, writes it back and prints the result. Status and budget are never touched.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

DROP_PREFIX = ("effective_",)
DROP = {"dt_consolidation_state"}


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    spec = json.load(open(os.environ["ADBOT_EXCL_SPEC"]))
    add = [str(i) for i in spec["exclude_ids"]]
    for aid in spec["adset_ids"]:
        t = (g.get_object(aid, "targeting") or {}).get("targeting") or {}
        before = {k: t[k] for k in ("age_min", "age_max", "page_types", "publisher_platforms") if k in t}
        new = {k: v for k, v in t.items() if k not in DROP and not k.startswith(DROP_PREFIX)}
        for key in ("custom_audiences", "excluded_custom_audiences"):
            if key in new:
                new[key] = [{"id": str(x["id"])} for x in new[key]]
        have = {x["id"] for x in new.get("excluded_custom_audiences", [])}
        new["excluded_custom_audiences"] = new.get("excluded_custom_audiences", []) + [{"id": i} for i in add if i not in have]
        g._request("POST", aid, data={"targeting": json.dumps(new)})
        bt = (g.get_object(aid, "targeting") or {}).get("targeting") or {}
        after = {k: bt[k] for k in ("age_min", "age_max", "page_types", "publisher_platforms") if k in bt}
        print(f"{aid}  age {bt.get('age_min')}-{bt.get('age_max')}  placements unchanged: {before == after}  "
              f"included {len(bt.get('custom_audiences') or [])}  excluded {[x.get('name') for x in bt.get('excluded_custom_audiences') or []]}")
    print("DONE")


if __name__ == "__main__":
    main()
