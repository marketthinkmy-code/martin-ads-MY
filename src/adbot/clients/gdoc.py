"""Google Docs client (service account): create a Doc and append text in place.

The Drive MCP cannot reliably append to an existing Doc, so the caption log and idea
backlog use the Docs API here. A Doc created when no id is configured is placed inside
the shared Drive folder so the operator can see it.
"""

from __future__ import annotations

from typing import Optional

from .drive import build_credentials


class DocsClient:
    def __init__(self, sa_json: str):
        from googleapiclient.discovery import build  # lazy

        creds = build_credentials(sa_json)
        self._docs = build("docs", "v1", credentials=creds, cache_discovery=False)
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    def ensure_doc(self, doc_id: str, title: str, parent_folder_id: Optional[str] = None) -> str:
        """Return doc_id, creating the Doc (inside parent_folder_id if given) when empty."""
        if doc_id:
            return doc_id
        metadata = {"name": title, "mimeType": "application/vnd.google-apps.document"}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]
        created = self._drive.files().create(
            body=metadata, fields="id", supportsAllDrives=True
        ).execute()
        return created["id"]

    def append_text(self, doc_id: str, text: str) -> None:
        """Append text to the very end of the document body."""
        doc = self._docs.documents().get(documentId=doc_id).execute()
        end_index = doc["body"]["content"][-1]["endIndex"]
        self._docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{
                "insertText": {"location": {"index": end_index - 1}, "text": text}
            }]},
        ).execute()

    def read_text(self, doc_id: str) -> str:
        """Flatten the document's text runs (used for dedup checks)."""
        doc = self._docs.documents().get(documentId=doc_id).execute()
        parts = []
        for element in doc.get("body", {}).get("content", []):
            for run in element.get("paragraph", {}).get("elements", []):
                parts.append(run.get("textRun", {}).get("content", ""))
        return "".join(parts)
