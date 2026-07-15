"""Read-only: dump one or more ad sets' real targeting + delivery settings as JSON.

Used to faithfully clone a winning ad set into a new campaign (the insights MCP can't
return targeting). No writes. Set ADSET_IDS to a comma-separated list (or ADSET_ID for one).
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def main() -> None:
    raw = (os.environ.get("ADSET_IDS") or os.environ.get("ADSET_ID") or "").strip()
    id_list = [x.strip() for x in raw.split(",") if x.strip()]
    if not id_list:
        raise SystemExit("Set ADSET_IDS=<comma-separated ad set ids> (or ADSET_ID for one).")
    s = load_settings()
    g = graph_client(s)
    fields = ("name,campaign_id,optimization_goal,billing_event,bid_strategy,"
              "daily_budget,destination_type,promoted_object,attribution_spec,"
              "use_new_app_click,targeting")
    for adset_id in id_list:
        print(f"===== ADSET {adset_id} =====")
        try:
            obj = g.get_object(adset_id, fields)
            print(json.dumps(obj, indent=2, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - report each, keep reading the rest
            print(f"[error] {adset_id}: {exc}")
        print(f"===== END {adset_id} =====")


if __name__ == "__main__":
    main()
