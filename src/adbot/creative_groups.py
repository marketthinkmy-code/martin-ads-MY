"""Group Drive assets into creative units: video / single-image / carousel.

Pure functions over an already-fetched folder tree, so the grouping is unit-tested
without touching Google Drive.

Folder convention:
- A top-level video file        -> one ``video`` unit.
- A top-level image file        -> one ``single_image`` unit.
- A subfolder named ``*<marker>*`` (default "carousel") -> one ``carousel`` unit
  built from the image files inside it (ordered by name).
- Other subfolders are walked recursively.
- A ``.txt`` sidecar with the same stem as a media file (or any ``.txt`` inside a
  carousel folder) supplies that unit's script/brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VIDEO = "video"
SINGLE_IMAGE = "single_image"
CAROUSEL = "carousel"


@dataclass
class Asset:
    file_id: str
    name: str
    mime: str
    script_file_id: Optional[str] = None  # matching .txt sidecar
    local_path: Optional[str] = None      # set after download
    meta_id: Optional[str] = None         # video_id or image_hash after upload


@dataclass
class Unit:
    content_id: str
    kind: str
    assets: List[Asset] = field(default_factory=list)
    script_file_id: Optional[str] = None  # unit-level brief (carousels)
    script_text: Optional[str] = None     # populated after download

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_id": self.content_id,
            "kind": self.kind,
            "assets": [a.__dict__ for a in self.assets],
            "script_file_id": self.script_file_id,
            "script_text": self.script_text,
        }


def slugify(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", name)  # drop extension
    slug = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return slug or "asset"


def is_video(mime: str) -> bool:
    return (mime or "").startswith("video/")


def is_image(mime: str) -> bool:
    return (mime or "").startswith("image/")


def is_folder(node: Dict[str, Any]) -> bool:
    return (node.get("mimeType") or "") == "application/vnd.google-apps.folder"


def _is_text(node: Dict[str, Any]) -> bool:
    return (node.get("name") or "").lower().endswith(".txt")


def _script_map(files: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map a media file's stem -> sidecar .txt file id."""
    return {slugify(f["name"]): f["id"] for f in files if _is_text(f)}


def build_units(node: Dict[str, Any], marker: str = "carousel") -> List[Unit]:
    """Walk a folder tree node ({id,name,mimeType,children:[...]}) into creative units."""
    children: List[Dict[str, Any]] = node.get("children", []) or []
    files = [c for c in children if not is_folder(c)]
    subfolders = [c for c in children if is_folder(c)]
    scripts = _script_map(files)
    units: List[Unit] = []

    for folder in subfolders:
        name = folder.get("name", "")
        if marker.lower() in name.lower():
            imgs = sorted([c for c in (folder.get("children") or []) if is_image(c.get("mimeType", ""))],
                          key=lambda c: c.get("name", ""))
            if imgs:
                texts = [c for c in (folder.get("children") or []) if _is_text(c)]
                units.append(Unit(
                    content_id=slugify(name),
                    kind=CAROUSEL,
                    assets=[Asset(i["id"], i["name"], i["mimeType"]) for i in imgs],
                    script_file_id=texts[0]["id"] if texts else None,
                ))
        else:
            units.extend(build_units(folder, marker))

    for f in files:
        mime = f.get("mimeType", "")
        sidecar = scripts.get(slugify(f["name"]))
        if is_video(mime):
            units.append(Unit(slugify(f["name"]), VIDEO,
                              [Asset(f["id"], f["name"], mime, sidecar)], sidecar))
        elif is_image(mime):
            units.append(Unit(slugify(f["name"]), SINGLE_IMAGE,
                              [Asset(f["id"], f["name"], mime, sidecar)], sidecar))

    return units


def uniquify_ids(units: List[Unit]) -> List[Unit]:
    """Make content_ids unique IN PLACE so distinct files that slugify the same are all kept.

    CJK / non-ascii filenames all collapse to ``asset`` via slugify; without this they'd share a
    content_id and select_ten would drop all but the first. Colliding ids get ``_2``, ``_3`` …
    suffixes in stable order (first occurrence keeps the bare slug).
    """
    counts: Dict[str, int] = {}
    for u in units:
        base = u.content_id
        counts[base] = counts.get(base, 0) + 1
        if counts[base] > 1:
            u.content_id = f"{base}_{counts[base]}"
    return units


def select_ten(units: List[Unit], n: int = 10) -> List[Unit]:
    """Pick the first ``n`` units, de-duplicating by content_id (stable order)."""
    seen, out = set(), []
    for u in units:
        if u.content_id in seen:
            continue
        seen.add(u.content_id)
        out.append(u)
        if len(out) >= n:
            break
    return out
