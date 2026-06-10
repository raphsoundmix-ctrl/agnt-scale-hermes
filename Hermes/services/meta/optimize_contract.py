"""Normalize optimizer dry-run proposals for the production UI contract."""
from __future__ import annotations

from typing import Any, Optional


def format_proposal(verdict: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Map internal dry-run tool payload → app-facing proposal shape."""
    v = str(verdict.get("verdict", "")).upper()
    action = {"KILL": "kill", "SCALE": "scale"}.get(v, v.lower() or "hold")
    cid = verdict.get("campaign_id")
    return {
        "action": action,
        "summary": str(proposed.get("summary") or verdict.get("reason") or ""),
        "campaign_id": str(cid) if cid else None,
        "reason": verdict.get("reason"),
        "dry_run": True,
    }
