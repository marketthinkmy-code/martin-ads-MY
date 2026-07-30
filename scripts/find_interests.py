"""Read-only: turn human keywords into REAL Meta interest ids you can target.

Guessing an interest_id is impossible and the interactive connector exposes no interest search,
so this asks Meta directly (search?type=adinterest) via the system-user token. For each seed it
prints the matching interests with their id, audience size and Meta's own category path, then
asks Meta for related interests it would suggest — which is where the non-obvious winners hide.

Seeds come from a JSON file (path via ADBOT_SEEDS, default scripts/targeting_specs/seeds.json)
shaped as {"theme": ["keyword", ...], ...}. No writes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot.commands import graph_client
from adbot.settings import REPO_ROOT, load_settings


def _size(row: dict) -> str:
    lo = row.get("audience_size_lower_bound") or row.get("audience_size") or 0
    hi = row.get("audience_size_upper_bound") or 0
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return "?"
    return f"{lo/1e6:.1f}M–{hi/1e6:.1f}M" if hi else f"{lo/1e6:.1f}M"


def main() -> None:
    seeds_path = Path(os.environ.get("ADBOT_SEEDS", "scripts/targeting_specs/seeds.json"))
    if not seeds_path.is_absolute():
        seeds_path = REPO_ROOT / seeds_path
    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))

    graph = graph_client(load_settings())
    for theme, keywords in seeds.items():
        print(f"\n{'='*78}\n### {theme}\n{'='*78}")
        found_names = []
        for kw in keywords:
            try:
                rows = graph.search_interests(kw, limit=8)
            except Exception as exc:  # noqa: BLE001 - one bad keyword must not kill the sweep
                print(f"\n-- {kw!r}: search failed ({exc})")
                continue
            print(f"\n-- {kw!r} -> {len(rows)} match(es)")
            for r in rows:
                path = " > ".join(r.get("path") or [])
                print(f"   {r.get('id'):<20} {_size(r):>12}  {r.get('name','')[:40]:40} {path[:46]}")
                if r.get("name"):
                    found_names.append(r["name"])

        if found_names:
            try:
                sugg = graph.suggest_interests(found_names[:8], limit=15)
            except Exception as exc:  # noqa: BLE001
                print(f"\n   (suggestions unavailable: {exc})")
                continue
            print(f"\n   ~~ Meta 建议的相关兴趣 ({len(sugg)}) ~~")
            for r in sugg:
                print(f"   {r.get('id'):<20} {_size(r):>12}  {r.get('name','')[:40]:40} "
                      f"{' > '.join(r.get('path') or [])[:46]}")


if __name__ == "__main__":
    main()
