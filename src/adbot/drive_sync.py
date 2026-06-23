"""Download creatives from the Drive folder and group them into 10 units."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .creative_groups import Unit, build_units, select_ten, uniquify_ids
from .logging import get_logger
from .settings import REPO_ROOT, Settings

DOWNLOAD_DIR = Path(REPO_ROOT) / "downloads"


def fetch_tree(drive, folder_id: str) -> Dict[str, Any]:
    """Recursively build a {id,name,mimeType,children:[...]} tree for the folder."""
    def walk(fid: str, name: str = "root") -> Dict[str, Any]:
        node = {"id": fid, "name": name,
                "mimeType": "application/vnd.google-apps.folder", "children": []}
        for child in drive.list_children(fid):
            if drive.is_folder(child):
                node["children"].append(walk(child["id"], child["name"]))
            else:
                node["children"].append(child)
        return node
    return walk(folder_id)


def load_units(drive, settings: Settings) -> Tuple[Dict[str, Any], List[Unit]]:
    tree = fetch_tree(drive, settings.drive.creatives_folder_id)
    units = select_ten(
        uniquify_ids(build_units(tree, settings.drive.carousel_subfolder_marker)),
        settings.meta.build.creatives_per_adset,
    )
    return tree, units


def download_assets(drive, units: List[Unit], dest_dir: Path = DOWNLOAD_DIR) -> List[Unit]:
    log = get_logger()
    for unit in units:
        for asset in unit.assets:
            target = dest_dir / f"{asset.file_id}_{asset.name}"
            drive.download_file(asset.file_id, target)
            asset.local_path = str(target)
        if unit.script_file_id:
            script_path = dest_dir / f"{unit.script_file_id}.txt"
            drive.download_file(unit.script_file_id, script_path)
            unit.script_text = script_path.read_text(encoding="utf-8", errors="ignore").strip()
        log.info("  [downloaded] %s (%s, %d asset(s))", unit.content_id, unit.kind, len(unit.assets))
    return units
