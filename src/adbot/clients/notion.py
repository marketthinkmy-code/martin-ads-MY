"""Minimal Notion client: append one row per caption to a database (best-effort logging).

Used to mirror each generated caption+headline into the operator's Notion content DB. The
build flow calls this best-effort, so a Notion hiccup never breaks an ad build.
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
_LIMIT = 2000  # Notion caps each rich_text/title object at 2000 chars


def rich_text(text: str) -> List[Dict[str, Any]]:
    """A Notion rich_text/title array, chunked to respect the 2000-char per-object cap."""
    text = text or ""
    return [{"text": {"content": text[i:i + _LIMIT]}} for i in range(0, len(text), _LIMIT)]


class NotionClient:
    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }

    def get_database(self, database_id: str) -> Dict[str, Any]:
        r = requests.get(f"{API}/databases/{database_id}", headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def create_row(self, database_id: str, properties: Dict[str, Any]) -> str:
        r = requests.post(
            f"{API}/pages", headers=self._headers,
            json={"parent": {"database_id": database_id}, "properties": properties}, timeout=30,
        )
        r.raise_for_status()
        return r.json().get("id", "")
