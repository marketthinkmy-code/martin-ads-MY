"""Anthropic client for caption/headline generation and creative-intelligence synthesis."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

DEFAULT_MODEL = "claude-opus-4-8"

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json(text: str) -> Any:
    cleaned = _FENCE_RE.sub("", text).strip()
    # Fall back to the first {...} or [...] block if the model added prose.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


class LLMClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        from anthropic import Anthropic  # lazy

        self._client = Anthropic(api_key=api_key)
        self.model = model

    def _complete_json(self, system: str, user: str, *, max_tokens: int = 4000) -> Any:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return _parse_json(text)

    def generate_caption(self, system_prompt: str, audience_md: str,
                         content: Dict[str, Any]) -> Dict[str, Any]:
        """Return {caption, headline, encoded_audience_signals, carousel_card_texts?}."""
        user = (
            "## AUDIENCE FRAMEWORK (verbatim)\n" + audience_md +
            "\n\n## CONTENT TO WRITE FOR\n" + json.dumps(content, ensure_ascii=False, indent=2) +
            "\n\nReturn ONLY the JSON object described in the system prompt."
        )
        return self._complete_json(system_prompt, user)

    def generate_intel(self, system_prompt: str, audience_md: str,
                       signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return a list of new content-idea objects."""
        user = (
            "## AUDIENCE FRAMEWORK (verbatim)\n" + audience_md +
            "\n\n## LIVE CREATIVE SIGNALS\n" + json.dumps(signals, ensure_ascii=False, indent=2) +
            "\n\nReturn ONLY the JSON array described in the system prompt."
        )
        result = self._complete_json(system_prompt, user, max_tokens=6000)
        return result if isinstance(result, list) else result.get("ideas", [])
