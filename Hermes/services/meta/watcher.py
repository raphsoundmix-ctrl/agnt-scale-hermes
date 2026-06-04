"""Continuous-learning watcher — records Meta platform changes to GLOBAL memory.

Robust by design: instead of brittle changelog scraping it learns from
  (a) the pinned API version vs the last recorded one, and
  (b) LIVE API errors that signal platform drift (deprecations, removed/unknown
      fields, newly-required params).
Findings go to the GLOBAL platform-knowledge memory (_global/_platform), embedded for
semantic recall, so every agent sees the latest reality. A scheduled cron hits
/agent/meta/learn for the periodic version check + summary.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from services import agnt_memory as mem
from services.meta.client import MetaAPIError, api_version

ACCT, AGENT = "_global", "_platform"

# Heuristic: error text fragments that usually mean the platform changed under us.
_DRIFT_HINTS = (
    "deprecat", "no longer", "unsupported", "has been removed", "unknown field",
    "is no longer supported", "must migrate", "this version", "invalid parameter",
)


def _meta_dict(row: dict) -> dict:
    raw = row.get("meta")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
    return raw or {}


async def record_api_learning(text: str, *, kind: str = "learning",
                              meta: Optional[dict] = None) -> Optional[int]:
    try:
        return await mem.remember(ACCT, AGENT, text, kind=kind, scope="long",
                                  meta={"domain": "meta_ads", "via": "watcher", **(meta or {})})
    except Exception:  # noqa: BLE001
        return None


async def check_version() -> dict:
    """Compare the pinned API version to the last recorded one; record any change."""
    cur = api_version()
    rows = await mem.recall(ACCT, AGENT, kind="version_state", limit=1)
    last = _meta_dict(rows[0]).get("version") if rows else None
    changed = bool(last and last != cur)
    if last != cur:
        note = (f"Meta Graph API version in use: {cur}"
                + (f" — CHANGED from {last}; review deprecations + the changelog." if changed
                   else " (baseline recorded)."))
        await record_api_learning(note, kind="version_state", meta={"version": cur, "previous": last})
    return {"current": cur, "previous": last, "changed": changed}


def looks_like_drift(err: MetaAPIError) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in _DRIFT_HINTS)


async def capture_error(err: Exception, *, context: str) -> None:
    """If a live Meta error signals platform drift, record it as a learning. Best-effort."""
    try:
        if isinstance(err, MetaAPIError) and looks_like_drift(err):
            await record_api_learning(
                f"API drift observed in {context}: {str(err)[:240]} (code {err.code}).",
                kind="drift", meta={"code": err.code, "context": context},
            )
    except Exception:  # noqa: BLE001
        pass


async def recent_learnings(limit: int = 10) -> list[dict]:
    rows = await mem.recall(ACCT, AGENT, limit=40)
    out = [{"kind": r["kind"], "content": str(r["content"])[:180]}
           for r in rows if r["kind"] in ("drift", "learning", "version_state")]
    return out[:limit]
