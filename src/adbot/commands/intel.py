"""intel: read live creatives -> angles/hooks/ideas -> append to the idea-backlog Doc."""

from __future__ import annotations

from typing import Any, Dict

from . import docs_client, graph_client, llm_client
from .. import creative_intel


def run(settings, *, dry_run: bool = False) -> Dict[str, Any]:
    graph = graph_client(settings)
    llm = llm_client(settings)
    docs = docs_client(settings) if not dry_run else None
    return creative_intel.run(graph, llm, docs, settings, dry_run=dry_run)
