"""Read-only credential diagnostic for the (#200) 'Provide valid app ID' error.

Prints only SHAPES and booleans — never the secret values themselves — plus the
result of /me (signed + unsigned) and one ad-account read, so we can tell an empty,
swapped, malformed, or wrong-type token apart in a single run.
"""
from __future__ import annotations

import re

from adbot.clients.graph import GraphClient, GraphError
from adbot.commands import graph_client
from adbot.settings import load_settings

HEX32 = re.compile(r"[0-9a-fA-F]{32}")
JUNK = set(" \t\r\n\"'`")  # characters that should never appear in a pasted credential


def _shape(name: str, val: str) -> str:
    return (f"{name}: present={bool(val)} len={len(val)} "
            f"startswith_EAA={val.startswith('EAA')} "
            f"is_32_hex={bool(HEX32.fullmatch(val))} "
            f"has_junk_char={any(c in JUNK for c in val)}")


def main() -> None:
    s = load_settings()
    tok = s.secrets.meta_token or ""
    sec = s.secrets.meta_app_secret or ""

    print("=== credential shapes (no values shown) ===")
    print(_shape("token     ", tok))
    print(_shape("app_secret", sec))
    print(f"token==app_secret (swapped/identical): {bool(tok) and tok == sec}")
    print(f"account_path: {s.meta.account_path}")
    print()

    print("=== live calls ===")
    checks = [
        ("/me  signed  ", lambda: graph_client(s).me()),
        ("/me  unsigned", lambda: GraphClient(tok, "").me()),
        ("account read ", lambda: graph_client(s).list_campaigns(s.meta.account_path)[:1]),
    ]
    for label, call in checks:
        try:
            print(f"OK   {label}: {str(call())[:140]}")
        except GraphError as exc:
            print(f"FAIL {label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERR  {label}: {type(exc).__name__}: {str(exc)[:140]}")


if __name__ == "__main__":
    main()
