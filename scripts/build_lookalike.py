"""Build a customer-list Custom Audience from the Paid Student List, then its MY lookalikes.

Runs inside CI on purpose: the sheet holds real buyer emails and phone numbers, and hashing them
on a runner means the raw values never leave the job. Nothing but counts is ever printed.

Meta matches on SHA-256 of NORMALISED values, so normalisation is the whole ballgame — an
unnormalised phone hashes to a different digest than the same phone Meta holds, and simply fails
to match with no error. Rules applied here (Meta's advanced-matching spec):
  EMAIL    trim, lowercase
  PHONE    digits only, country code included, no leading '+' or trunk '0'
  COUNTRY  ISO-3166 alpha-2, lowercase

The workbook is shared MY/SG, so the dial code also tells us each buyer's market — the same signal
monthly_sales.py uses. Both markets go into the seed (a Chinese-speaking SG parent who bought a
height course is a good pattern for finding MY ones), but the lookalikes are generated in MY only.

Set ADBOT_LAL_AUDIT=1 to run the exact same selection and print a masked sample WITHOUT creating
or uploading anything — the point of an audit is that it shares this code path, so what it shows
is by construction what a real run would send.

Env:
  ADBOT_LAL_AUDIT    "1" to report only, no writes
  ADBOT_LAL_LABEL    suffix for the audience names, e.g. "16 AUG"
  ADBOT_LAL_RATIOS   lookalike bands, default "0-1,1-2,2-3,3-4,4-5" (percent)
  ADBOT_LAL_COUNTRY  lookalike country, default MY
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter

from adbot import cpa
from adbot.commands import graph_client
from adbot.clients.sheets import SheetsClient
from adbot.settings import load_settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def norm_email(raw: str) -> str:
    e = (raw or "").strip().lower()
    return e if EMAIL_RE.match(e) else ""


def mask_email(e: str) -> str:
    if not e:
        return "—"
    user, _, domain = e.partition("@")
    return f"{user[0]}{'*' * max(len(user) - 2, 1)}{user[-1] if len(user) > 1 else ''}@{domain}"


def mask_phone(p: str) -> str:
    return f"{p[:4]}{'*' * max(len(p) - 8, 1)}{p[-4:]}" if len(p) >= 8 else "*" * len(p)


def norm_phone(raw: str) -> tuple[str, str]:
    """(e164-digits, market) — the dial code identifies the buyer's market.

    Cells are hand-typed and some hold two numbers ("0123456789 / 0129876543") or a number with a
    note beside it. Stripping every non-digit from the whole cell welds them into one 21-digit
    string that can never match anything at Meta, so each candidate is tried separately and only a
    result of plausible E.164 length (max 15 digits) is accepted.
    """
    chunks = [c for c in re.split(r"[/,;、|\n]| or | OR ", raw or "") if any(x.isdigit() for x in c)]
    for chunk in chunks or [raw or ""]:
        d = re.sub(r"\D", "", chunk)
        if not d:
            continue
        if d.startswith("60"):
            market = "MY"
        elif d.startswith("65") and len(d) >= 9:
            market = "SG"
        elif d.startswith("0"):                    # local MY form 01x-xxx xxxx
            d, market = "60" + d[1:], "MY"
        elif len(d) == 8 and d[0] in "3689":       # local SG form 9xxx xxxx
            d, market = "65" + d, "SG"
        elif d.startswith("1") and len(d) == 11:
            market = "US"
        else:
            market = "?"                           # HK 852, AU 61, TH 66 ... kept, just untagged
        if 9 <= len(d) <= 15:                      # E.164 caps at 15; anything longer is garbage
            return d, market
    return "", ""


def main() -> None:
    s = load_settings()
    label = os.environ.get("ADBOT_LAL_LABEL", "").strip()
    if not label:
        raise SystemExit("ADBOT_LAL_LABEL is required (e.g. '16 AUG')")
    country = os.environ.get("ADBOT_LAL_COUNTRY", "MY").strip().upper()
    bands = [b.strip() for b in
             os.environ.get("ADBOT_LAL_RATIOS", "0-1,1-2,2-3,3-4,4-5").split(",") if b.strip()]

    graph = graph_client(s)
    account = s.meta.account_path

    values = SheetsClient(s.secrets.google_sa_json).read_tab(
        s.cpa.spreadsheet_id, s.cpa.sales_tab)
    header_idx, cols = 0, cpa.find_columns(values[0])
    for i, row in enumerate(values[:8]):
        cand = cpa.find_columns(row)
        if cand.get("campaign", -1) >= 0 and cand.get("ad", -1) >= 0:
            header_idx, cols = i, cand
            break
    header = values[header_idx]
    email_idx = next((i for i, h in enumerate(header) if "email" in (h or "").casefold()), -1)
    phone_idx = next((i for i, h in enumerate(header)
                      if any(t in (h or "").casefold()
                             for t in ("whatsapp", "號碼", "号码", "phone"))), -1)
    print(f"sheet '{s.cpa.sales_tab}': {len(values)} rows · header {header_idx} · "
          f"email col {email_idx} · phone col {phone_idx}")
    if email_idx < 0 and phone_idx < 0:
        raise SystemExit("no email or phone column — refusing to build an unmatched audience")

    rows, preview, seen, markets, stats = [], [], set(), Counter(), Counter()
    for raw in values[header_idx + 1:]:
        cell = lambda i: raw[i] if 0 <= i < len(raw) else ""
        email = norm_email(cell(email_idx))
        phone, market = norm_phone(cell(phone_idx))
        if not email and not phone:
            stats["skipped_no_identifier"] += 1
            continue
        key = (email, phone)
        if key in seen:                     # one person, many purchases -> one seed record
            stats["duplicate"] += 1
            continue
        seen.add(key)
        markets[market or "?"] += 1
        stats["with_email"] += bool(email)
        stats["with_phone"] += bool(phone)
        rows.append([sha(email), sha(phone), sha(market.lower()) if market else ""])
        preview.append((email, phone, market, cell(cols.get("date", -1))))

    print(f"seed records: {len(rows)}  (email {stats['with_email']} · phone {stats['with_phone']})")
    print(f"  deduplicated: {stats['duplicate']} · no identifier: {stats['skipped_no_identifier']}")
    print(f"  by market: {dict(markets)}")
    if len(rows) < 100:
        raise SystemExit(f"only {len(rows)} usable records — Meta needs 100+ for a lookalike")

    if os.environ.get("ADBOT_LAL_AUDIT") == "1":
        dates = sorted(d for *_, d in preview if d)
        print(f"\n=== AUDIT ONLY — nothing created, nothing uploaded ===")
        print(f"source: spreadsheet {s.cpa.spreadsheet_id} · tab '{s.cpa.sales_tab}'")
        print(f"selection rule: every row with a usable email OR phone. No date filter, no market "
              f"filter — the seed is every buyer on the list.")
        if dates:
            print(f"purchase dates present: {dates[0]} .. {dates[-1]}")
        step = max(len(preview) // 25, 1)
        print(f"\nsample (every {step}th of {len(preview)}, masked):")
        print(f"  {'#':>5}  {'email':<34} {'phone (normalised)':<20} {'mkt':<4} date")
        for i in range(0, len(preview), step):
            e, ph, mk, dt_ = preview[i]
            print(f"  {i + 1:>5}  {mask_email(e):<34} {mask_phone(ph):<20} {mk or '?':<4} {dt_}")
        no_market = [p for p in preview if not p[2] or p[2] == "?"]
        print(f"\nphone did not resolve to a market ({len(no_market)}) — still uploaded, "
              f"they only affect the market tally, not the match:")
        for e, ph, mk, dt_ in no_market[:15]:
            print(f"  {mask_email(e):<34} {mask_phone(ph):<20} {dt_}")
        raise SystemExit(0)

    ca_name = f"MARTIN MYSG PAID CUSTOMER - {label}"
    ca = graph._request("POST", f"{account}/customaudiences", data={
        "name": ca_name,
        "description": f"Paid Student List (RM1997) as at {label}; hashed email+phone+country",
        "subtype": "CUSTOM",
        "customer_file_source": "USER_PROVIDED_ONLY",
    })
    ca_id = ca["id"]
    print(f"[custom audience] {ca_id}  {ca_name}")

    CHUNK = 5000
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        graph._request("POST", f"{ca_id}/users", data={"payload": json.dumps(
            {"schema": ["EMAIL", "PHONE", "COUNTRY"], "data": batch})})
        print(f"  uploaded {i + len(batch)}/{len(rows)}")

    made = []
    for band in bands:
        lo, hi = (float(x) / 100 for x in band.split("-"))
        spec = {"type": "custom_ratio", "ratio": hi, "country": country}
        if lo > 0:
            spec["starting_ratio"] = lo
        pretty = f"{int(hi * 100)}%" if lo == 0 else f"{int(lo * 100)}% to {int(hi * 100)}%"
        name = f"Lookalike ({country}, {pretty}) - {ca_name}"
        lal = graph._request("POST", f"{account}/customaudiences", data={
            "name": name, "subtype": "LOOKALIKE",
            "origin_audience_id": ca_id, "lookalike_spec": json.dumps(spec)})
        made.append({"id": lal["id"], "name": name})
        print(f"[lookalike] {lal['id']}  {name}")

    print("DONE " + json.dumps({"custom_audience": {"id": ca_id, "name": ca_name},
                                "seed_records": len(rows), "lookalikes": made},
                               ensure_ascii=False))


if __name__ == "__main__":
    main()
