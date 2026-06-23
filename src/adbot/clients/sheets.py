"""Google Sheets reader (service account): read a tab's values.

Reuses the Drive service-account credentials (the Drive scope also authorises Sheets
reads). Used by the CPA layer to read the Paid Student List.
"""
from __future__ import annotations

from typing import List

from .drive import build_credentials


class SheetsClient:
    def __init__(self, sa_json: str):
        from googleapiclient.discovery import build  # lazy import
        self._svc = build("sheets", "v4", credentials=build_credentials(sa_json),
                          cache_discovery=False)

    def read_tab(self, spreadsheet_id: str, tab_name: str) -> List[List[str]]:
        """All non-empty rows of a tab, as lists of formatted cell strings."""
        resp = (self._svc.spreadsheets().values()
                .get(spreadsheetId=spreadsheet_id, range=tab_name,
                     valueRenderOption="FORMATTED_VALUE",
                     dateTimeRenderOption="FORMATTED_STRING")
                .execute())
        return resp.get("values", [])
