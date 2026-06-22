"""Google Drive client (service account): list a folder tree and download files."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def _load_sa_info(value: str) -> dict:
    """Accept raw JSON or base64-encoded JSON (tolerant of which env var / format was used)."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        import base64  # lazy
        return json.loads(base64.b64decode(value).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Google service-account key is neither a readable path, valid JSON, "
            "nor valid base64-encoded JSON"
        ) from exc


def build_credentials(sa_json: str):
    """Build service-account credentials from a file path, inline JSON, or base64 JSON."""
    from google.oauth2 import service_account  # lazy

    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON[_B64] is not set")
    if os.path.exists(sa_json):
        return service_account.Credentials.from_service_account_file(sa_json, scopes=SCOPES)
    return service_account.Credentials.from_service_account_info(_load_sa_info(sa_json), scopes=SCOPES)


class DriveClient:
    FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, sa_json: str):
        from googleapiclient.discovery import build  # lazy

        self._svc = build("drive", "v3", credentials=build_credentials(sa_json),
                          cache_discovery=False)

    def list_children(self, folder_id: str) -> List[Dict[str, Any]]:
        """Direct children (files + subfolders) of a folder."""
        out: List[Dict[str, Any]] = []
        page_token = None
        while True:
            resp = self._svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=200, pageToken=page_token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            out.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def is_folder(self, file_obj: Dict[str, Any]) -> bool:
        return file_obj.get("mimeType") == self.FOLDER_MIME

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload  # lazy

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        request = self._svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path
