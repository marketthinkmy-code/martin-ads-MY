"""Re-print a step's captured output as GitHub check-run annotations (::notice::).

The operator's sandbox can reach api.github.com but not the blob store that serves job logs, so
the lines that matter (built ids, interest ids, the error that stopped a run) are surfaced as
annotations, which the check-runs API returns as plain JSON. Usage:

    python scripts/annotate_output.py <captured-output-file> <title> [line-regex]

Lines not matching the regex are dropped; the rest is split into chunks small enough for one
annotation each (at most 8 chunks, oldest first). Never fails the job.
"""
from __future__ import annotations

import pathlib
import re
import sys

CHUNK = 3500
MAX_CHUNKS = 8


def main() -> None:
    src = pathlib.Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else "output"
    pattern = re.compile(sys.argv[3]) if len(sys.argv) > 3 else None
    text = src.read_text(encoding="utf-8", errors="replace") if src.exists() else "(no output captured)"
    lines = [ln.rstrip() for ln in text.splitlines()]
    if pattern:
        lines = [ln for ln in lines if pattern.search(ln)]
    body = "\n".join(lines) or "(nothing matched)"
    chunks = [body[i:i + CHUNK] for i in range(0, len(body), CHUNK)][:MAX_CHUNKS]
    for i, chunk in enumerate(chunks, 1):
        enc = chunk.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::notice title={title} {i}/{len(chunks)}::{enc}")


if __name__ == "__main__":
    main()
