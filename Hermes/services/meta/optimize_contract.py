"""Normalize optimizer dry-run proposals for the production UI contract."""
from __future__ import annotations

from typing import Any, Optional


def format_proposal(
    verdict: dict[str, Any],
    proposed: dict[str, Any],
    *,
    apply_tool: str,
    apply_params: dict[str, Any],
) -> dict[str, Any]:
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
        "apply": {"tool": apply_tool, "params": apply_params},
    }


def kill_apply(campaign_id: str) -> tuple[str, dict[str, Any]]:
    return "update_status", {"campaign_id": str(campaign_id), "status": "PAUSED"}


def scale_apply(campaign_id: str, daily_budget: int) -> tuple[str, dict[str, Any]]:
    return "update_budget", {"campaign_id": str(campaign_id), "daily_budget": int(daily_budget)}
