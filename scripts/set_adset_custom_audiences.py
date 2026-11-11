"""Add INCLUDED custom / lookalike audiences to existing ad sets, keeping everything else.

Spec (ADBOT_CA_SPEC, JSON file): {"adset_ids": [...], "custom_audience_ids": [...]}
For each ad set: read its live targeting, keep geo / age / locales / interests / exclusions /
Advantage-audience flag as they are, set custom_audiences to the listed ids, write it back, and
print what Meta returns. Status and budget are never touched.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

KEEP = ("geo_locations", "age_min", "age_max", "genders", "locales", "flexible_spec",
        "targeting_automation", "targeting_relaxation_types")


def main() -> None:
    s = load_settings()
    g = graph_client(s)
    spec = json.load(open(os.environ["ADBOT_CA_SPEC"]))
    ca = [{"id": str(i)} for i in spec["custom_audience_ids"]]
    for aid in spec["adset_ids"]:
        t = (g.get_object(aid, "targeting") or {}).get("targeting") or {}
        new = {k: t[k] for k in KEEP if k in t}
        new["excluded_custom_audiences"] = [{"id": str(x["id"])} for x in t.get("excluded_custom_audiences") or []]
        new["custom_audiences"] = ca
        g._request("POST", aid, data={"targeting": json.dumps(new)})
        back = (g.get_object(aid, "targeting,effective_status") or {})
        bt = back.get("targeting") or {}
        print(f"{aid}  status {back.get('effective_status')}  age {bt.get('age_min')}-{bt.get('age_max')}  "
              f"included {[x.get('name') for x in bt.get('custom_audiences') or []]}  "
              f"excluded {[x.get('name') for x in bt.get('excluded_custom_audiences') or []]}")
    print("DONE")


if __name__ == "__main__":
    main()
