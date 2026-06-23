"""weekly_on: resume exactly the ads weekly_off paused (Thu 00:00 GMT+8)."""

from __future__ import annotations

from typing import Any, Dict

from . import graph_client
from .. import weekly_on as feature


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    graph = graph_client(settings)
    return feature.run(graph, settings, dry_run=dry_run)
