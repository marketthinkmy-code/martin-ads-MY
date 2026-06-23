#!/usr/bin/env bash
# One-shot setup: create the venv, install adbot, and run the offline test suite.
# Safe to run in a Claude Code web session (no credentials or network to Meta needed).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> Creating virtualenv (.venv)"
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing adbot (+dev)"
python -m pip install --upgrade pip >/dev/null
python -m pip install -e ".[dev]"

echo "==> Running offline tests"
python -m pytest -q

cat <<'NEXT'

==> Setup complete.

Next steps:
  1. Fill in config/config.yaml   (ad account, page, pixel, landing URL, budget, CPL...)
  2. Paste Soo Cheng's framework into config/audience.md (remove the TODO markers)
  3. Copy .env.example -> .env and add the 3 secrets (Meta token, Google SA, Anthropic key)
  4. Validate:    source .venv/bin/activate && python -m adbot doctor
  5. Dry-run:     python -m adbot sync --dry-run   and   python -m adbot build --dry-run
  6. Go live:     python -m adbot sync   then   python -m adbot build

See README.md for the full runbook and the three cloud routines.
NEXT
