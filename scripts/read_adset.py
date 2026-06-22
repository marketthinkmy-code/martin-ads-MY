"""Read-only: dump one ad set's real targeting + delivery settings as JSON.

Used to faithfully clone a winning ad set into a new campaign (the insights MCP can't
return targeting). No writes.
"""
from __future__ import annotations

import json
import os

from adbot.commands import graph_client
from adbot.settings import load_settings


def main() -> None:
    adset_id = os.environ.get("ADSET_ID", "").strip()
    if not adset_id:
        raise SystemExit("Set ADSET_ID=<ad set id> — pass the ad set you want to read.")
    s = load_settings()
    g = graph_client(s)
    fields = ("name,campaign_id,optimization_goal,billing_event,bid_strategy,"
              "daily_budget,destination_type,promoted_object,attribution_spec,"
              "use_new_app_click,targeting")
    obj = g.get_object(adset_id, fields)
    print(json.dumps(obj, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
