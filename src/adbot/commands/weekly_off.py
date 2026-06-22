"""weekly_off: pause ALL live managed ads (Wed 15:00 GMT+8 kill switch)."""

from __future__ import annotations

from typing import Any, Dict

from . import graph_client
from .. import weekly_off as feature


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    graph = graph_client(settings)
    return feature.run(graph, settings, dry_run=dry_run)
