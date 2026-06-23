"""Structured, mobile-friendly logging with secret redaction.

Routine runs surface stdout in the Claude mobile app, so logs are plain and readable.
Every command should end with :func:`final_summary` — a one-line outcome the mobile
timeline shows at a glance.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Set

# Secret literals registered at runtime so they are never printed verbatim.
_SECRETS: Set[str] = set()

# Token-shaped strings (Meta/Anthropic) get masked even if not explicitly registered.
_TOKEN_RE = re.compile(r"(EAA[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_\-]{20,})")


def register_secret(value: str) -> None:
    """Register a secret value so the formatter masks it in all log output."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def _redact(text: str) -> str:
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, "***REDACTED***")
    return _TOKEN_RE.sub("***REDACTED***", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        return _redact(super().format(record))


_CONFIGURED = False


def get_logger(name: str = "adbot") -> logging.Logger:
    """Return a configured logger that writes redacted INFO logs to stdout."""
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_RedactingFormatter("%(asctime)s  %(levelname)-7s %(message)s",
                                                 datefmt="%H:%M:%S"))
        root = logging.getLogger("adbot")
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
        _CONFIGURED = True
    return logger


def final_summary(logger: logging.Logger, text: str) -> None:
    """Emit the one-line run outcome (prefixed so it stands out in the mobile timeline)."""
    logger.info("──────────────────────────────────────────────")
    logger.info("SUMMARY: %s", text)
