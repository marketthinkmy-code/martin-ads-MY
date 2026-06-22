"""Read-only CPA preview: read the Paid Student List tab and print what we parsed.

Verifies the data foundation (tab readable? columns detected? date range? recent sales?)
BEFORE any pause logic is built on it. Prints the service-account email so the sheet can
be shared with it. Run via the adbot-cpa-preview workflow.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os

from adbot import cpa
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings


def _sa_email(sa_json: str) -> str:
    try:
        if sa_json and os.path.exists(sa_json):
            data = json.load(open(sa_json))
        else:
            try:
                data = json.loads(sa_json)
            except Exception:  # noqa: BLE001
                data = json.loads(base64.b64decode(sa_json))
        return data.get("client_email", "?")
    except Exception:  # noqa: BLE001
        return "<could not read service-account JSON>"


def main() -> None:
    s = load_settings()
    sa = s.secrets.google_sa_json or ""
    print(f"sa creds        : present={bool(sa)} length={len(sa)} "
          f"looks_like_json={sa.lstrip()[:1] == '{'}")
    print("service account :", _sa_email(s.secrets.google_sa_json))
    print("spreadsheet     :", s.cpa.spreadsheet_id, "| tab:", repr(s.cpa.sales_tab))
    if not s.cpa.spreadsheet_id:
        print("cpa.spreadsheet_id is not set in config — nothing to read.")
        return

    try:
        values = SheetsClient(s.secrets.google_sa_json).read_tab(
            s.cpa.spreadsheet_id, s.cpa.sales_tab)
    except Exception as exc:  # noqa: BLE001
        print(f"READ FAILED: {type(exc).__name__}: {str(exc)[:240]}")
        print("If this is a permission error, share the sheet (Viewer) with the "
              "service-account email shown above, then re-run.")
        return

    print(f"rows returned   : {len(values)}")
    sales, cols, header = cpa.parse_sales(values, s.cpa.price_myr)
    print("header row      :", [h[:18] for h in header[:14]])
    print("detected columns:", cols)
    di = cols.get("date", -1)
    if di >= 0:
        raw = [r[di].strip() for r in values if di < len(r) and r[di].strip()]
        print("sample Date raw :", [v for v in raw if v.lower() != "date"][:12])
    dated = [x.date for x in sales if x.date]
    print(f"sales parsed    : {len(sales)}  (with a usable date: {len(dated)})")
    if dated:
        print(f"date range      : {min(dated)} -> {max(dated)}")

    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # MYT
    win = cpa.count_windows(sales, today)
    print(f"\nsales by window (today MYT={today}): "
          f"14d={win['14d']}  30d={win['30d']}  60d={win['60d']}  lifetime={win['life']}")

    by_ad, _by_adset, by_campaign = cpa.attribute(sales, today)
    print("\nTOP 15 ads by 60d paid sales  [14d/30d/60d/life]  campaign | adset | ad:")
    for key, w in sorted(by_ad.items(), key=lambda kv: -kv[1]["60d"])[:15]:
        print(f"  {w['14d']:>3}/{w['30d']:>3}/{w['60d']:>3}/{w['life']:>4}  "
              f"{key[0][:24]:24} | {key[1][:12]:12} | {key[2][:26]}")
    print("\nPaid sales per campaign  [30d/60d/life]:")
    for key, w in sorted(by_campaign.items(), key=lambda kv: -kv[1]["life"])[:15]:
        print(f"  {w['30d']:>3}/{w['60d']:>3}/{w['life']:>4}  {key[:46]}")


if __name__ == "__main__":
    main()
