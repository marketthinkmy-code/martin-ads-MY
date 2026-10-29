"""Read-only: the structure of a Google Sheet — every tab's title, gid, row count and header row.

Used to locate where a data source (e.g. webinar attendance) lives before a report reads it.
Header cells are column names, not personal data; no data row is ever printed.

Env:
  ADBOT_PROBE_SHEET_ID   the workbook to describe
"""
from __future__ import annotations

import os
import re

from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def main() -> None:
    s = load_settings()
    sid = os.environ["ADBOT_PROBE_SHEET_ID"].strip()
    sheets = SheetsClient(s.secrets.google_sa_json)
    tabs = sheets.tabs(sid)
    print(f"===== SHEET STRUCTURE · {sid[:12]}… · {len(tabs)} tabs =====")
    for title, gid in tabs:
        try:
            values = sheets.read_tab(sid, title)
        except Exception as exc:  # noqa: BLE001 - one unreadable tab must not sink the probe
            print(f"\n[{title}] gid {gid}  !! {str(exc)[:120]}")
            continue
        hdr_i = next((i for i, r in enumerate(values[:10]) if sum(1 for c in r if c.strip()) >= 2), 0)
        header = values[hdr_i] if values else []
        rows = sum(1 for r in values[hdr_i + 1:] if any(c.strip() for c in r))
        print(f"\n[{title}] gid {gid} · header row {hdr_i + 1} · {rows} data rows")
        # a tab with no header row starts with a person's row — never print that
        if any("@" in c or len(re.sub(r"\D", "", c)) >= 8 for c in header):
            print("    header: (first row looks like personal data, not a header — not printed)")
        else:
            print(f"    header: {header[:30]}")
    print("\nDONE (no writes, no data rows printed)")


if __name__ == "__main__":
    main()
