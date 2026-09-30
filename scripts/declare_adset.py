"""One-shot: stamp the MY beneficiary/payer declaration onto ad sets built before Meta's
2026-09-08 "verified advertiser" rule, then set them ACTIVE.

Meta refuses to re-activate such an ad set once it is paused ("Provide a verified advertiser so
that ads in this ad set can be delivered to audiences in the selected locations"), which is what
stranded 营养·方向错了 after the weekly OFF of 2026-09-30. The declaration is copied verbatim
from a live MY ad set (the same source clone_test.py uses) rather than guessed. Ids come from
ADBOT_ADSET_IDS (comma list); an empty list is a no-op so the registering push does nothing.
ADBOT_NO_ACTIVATE=1 stamps the declaration and leaves the status alone.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings

_REGULATION = ("regional_regulated_categories", "regional_regulation_identities")


def main() -> None:
    ids = [x.strip() for x in os.environ.get("ADBOT_ADSET_IDS", "").split(",") if x.strip()]
    if not ids:
        print("ADBOT_ADSET_IDS is empty — nothing to do.")
        return
    source = os.environ.get("ADBOT_REG_SOURCE", "120247917148640575")
    graph = graph_client(load_settings())
    obj = graph.get_object(source, ",".join(_REGULATION)) or {}
    reg = {k: obj[k] for k in _REGULATION if obj.get(k)}
    if not reg:
        raise SystemExit(f"source ad set {source} carries no declaration to copy")
    print(f"[regulation] {json.dumps(reg, ensure_ascii=False)}  (copied from ad set {source})")

    ok = 0
    for adset_id in ids:
        try:
            graph._request("POST", adset_id, data={k: json.dumps(v) for k, v in reg.items()})
            print(f"[DECLARED]  {adset_id}")
            if os.environ.get("ADBOT_NO_ACTIVATE") != "1":
                graph.update_status(adset_id, "ACTIVE")
                print(f"[ACTIVATED] {adset_id}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report every ad set, judge the job at the end
            print(f"[FAILED]    {adset_id}: {exc}")
    print(f"done: {ok}/{len(ids)}")
    if ok != len(ids):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
