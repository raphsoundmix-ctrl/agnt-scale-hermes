"""
LLM JSON parsing — tolerant to the markdown-fenced output that many
models emit even when `response_format={"type":"json_object"}` is set.

Observed in production (Claude / DeepSeek / Qwen via OpenRouter):

    ```json
    {"summary": "..."}
    ```

The naive `json.loads(raw)` raises JSONDecodeError on that.

`parse_llm_json` strips the fence, balances bracket-aware truncation,
and falls back to extracting the first {...} or [...] block in the
string if needed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any


logger = logging.getLogger("hermes.llm_json")


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?|\n?```\s*$", re.MULTILINE)


def strip_markdown_fence(raw: str) -> str:
    """Drop opening/closing ```/```json fences. Idempotent on clean input."""
    return _FENCE_RE.sub("", raw).strip()


def _find_balanced_block(text: str, open_char: str, close_char: str) -> str | None:
    """Return the first balanced {...} or [...] substring, or None."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == open_char:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]
    return None


def parse_llm_json(raw: str) -> Any:
    """
    Parse `raw` as JSON. Tolerates:
      - ```json fences (most common)
      - leading/trailing whitespace
      - extra prose before/after the JSON block (Claude sometimes adds
        "Here's the JSON:" prelude even with response_format set)

    Raises `ValueError` on truly unparseable input. Logs the original at
    DEBUG level for postmortem.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty LLM response")

    candidate = strip_markdown_fence(raw)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Maybe wrapped in prose — extract first balanced JSON block
    for open_c, close_c in (("{", "}"), ("[", "]")):
        block = _find_balanced_block(candidate, open_c, close_c)
        if block is None:
            continue
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    logger.debug("llm_json_unparseable", extra={"raw_preview": raw[:500]})
    raise ValueError(f"Could not parse JSON from LLM output: {raw[:200]}")
