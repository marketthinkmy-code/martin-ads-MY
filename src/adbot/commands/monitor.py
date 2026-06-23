"""monitor: pause ads whose CPL exceeds the threshold."""

from __future__ import annotations

from typing import Any, Dict

from . import graph_client
from .. import monitor_cpl


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    graph = graph_client(settings)
    return monitor_cpl.run(graph, settings, dry_run=dry_run)
