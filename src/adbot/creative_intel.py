"""Read live creative signals -> micro-segment angles/hooks/ideas -> Google Doc backlog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import docwriter
from .logging import final_summary, get_logger
from .monitor_cpl import parse_metrics, result_action_type
from .settings import REPO_ROOT, Settings

INTEL_PROMPT_PATH = Path(REPO_ROOT) / "prompts" / "intel_system.md"


def gather_signals(graph, settings: Settings) -> List[Dict[str, Any]]:
    """Per managed ad: name, status, spend, results (optimized event), CPL over 30 days."""
    token = result_action_type(settings.meta.conversion_event)
    signals: List[Dict[str, Any]] = []
    for campaign in graph.find_campaigns_by_prefix(settings.meta.account_path, settings.naming.prefix):
        for ad in graph.list_ads_under_campaign(campaign["id"]):
            insight = graph.get_ad_insight(ad["id"], "last_30d")
            spend, results = parse_metrics(insight, token)
            signals.append({
                "ad_name": ad.get("name"),
                "status": ad.get("effective_status"),
                "spend": round(spend, 2),
                "results": results,
                "cpl": round(spend / results, 2) if results > 0 else None,
            })
    return signals


def run(graph, llm, docs, settings: Settings, *, dry_run: bool = False) -> Dict[str, Any]:
    from .captions import load_audience  # reuse the framework loader/validator

    log = get_logger()
    audience = load_audience(settings)
    signals = gather_signals(graph, settings)
    log.info("Gathered signals for %d ad(s).", len(signals))

    system = INTEL_PROMPT_PATH.read_text(encoding="utf-8")
    ideas = llm.generate_intel(system, audience, signals)
    log.info("Model proposed %d content idea(s).", len(ideas))

    if dry_run:
        for idea in ideas:
            log.info("  [idea] %s [%s]", idea.get("title"), idea.get("format"))
        final_summary(log, f"intel (dry-run): {len(ideas)} ideas proposed (not written)")
        return {"ideas": len(ideas), "dry_run": True}

    added = docwriter.append_ideas(docs, settings, ideas)
    final_summary(log, f"intel: appended {added} new content ideas to the Google Doc")
    return {"ideas": len(ideas), "appended": added, "dry_run": False}
