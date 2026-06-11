"""AGNT SCALE — Meta Ads custom MCP server (thin HTTP bridge to Hermes).

Exposes the Hermes Meta endpoints as MCP tools for Claude Code / any MCP client.
Heavy logic + memory + tokens stay on the Hermes server; this is a stdio bridge.

Env:
  HERMES_URL              e.g. https://ai-agents-by-raph.tail3c773d.ts.net:8443
  HERMES_INTERNAL_TOKEN   the X-Internal-Token shared secret
  META_WORKSPACE          account_id used for memory scoping (default "mcp")

Install + run (configured as an MCP server in Claude Code):
  pip install "mcp[cli]" httpx
  python meta_mcp.py
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

HERMES = os.environ.get("HERMES_URL", "http://localhost:7778").rstrip("/")
TOKEN = os.environ.get("HERMES_INTERNAL_TOKEN", "")
WORKSPACE = os.environ.get("META_WORKSPACE", "mcp")

mcp = FastMCP("agnt-meta-ads")


async def _post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{HERMES}/agent/{path}",
            json={"account_id": WORKSPACE, **payload},
            headers={"X-Internal-Token": TOKEN, "Content-Type": "application/json"},
        )
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"error": r.text[:500], "status": r.status_code}


@mcp.tool()
async def meta_read(tool: str, ad_account_id: str = "", meta_token: str = "",
                    args: Optional[dict] = None) -> dict:
    """Read Meta Ads data. tool ∈ {list_ad_accounts, get_insights, list_campaigns,
    list_adsets, list_ads, list_pixels, search_interests}."""
    return await _post("meta", {"tool": tool, "ad_account_id": ad_account_id or None,
                                "meta_token": meta_token or None, "args": args or {}})


@mcp.tool()
async def campaign_plan(goal: str, budget_cents: int = 0, countries: Optional[list[str]] = None,
                        pixel_id: str = "", ad_account_id: str = "") -> dict:
    """Design a Meta campaign blueprint + DRY-RUN plan from a goal. Nothing is created."""
    return await _post("campaign/plan", {
        "goal": goal, "budget_cents": budget_cents or None, "countries": countries,
        "pixel_id": pixel_id or None, "ad_account_id": ad_account_id or None,
    })


@mcp.tool()
async def campaign_execute(blueprint: dict, ad_account_id: str, meta_token: str = "",
                           pixel_id: str = "", page_id: str = "", approve: bool = False) -> dict:
    """Execute an APPROVED blueprint (creates campaign + ad sets + optional ads/creatives, PAUSED).
    Requires approve=true and a Meta token with ads_management."""
    return await _post("campaign/execute", {
        "blueprint": blueprint, "ad_account_id": ad_account_id, "meta_token": meta_token or None,
        "pixel_id": pixel_id or None, "page_id": page_id or None, "approve": approve,
    })


@mcp.tool()
async def campaign_optimize(ad_account_id: str, meta_token: str = "",
                            target_roas: float = 0, target_cpa: float = 0) -> dict:
    """Read insights → Kill/Hold/Scale → DRY-RUN budget/status proposals (approval-gated)."""
    return await _post("campaign/optimize", {
        "ad_account_id": ad_account_id, "meta_token": meta_token or None,
        "target_roas": target_roas or None, "target_cpa": target_cpa or None,
    })


@mcp.tool()
async def meta_learn() -> dict:
    """Continuous-learning tick: Meta API version check + recent platform learnings."""
    return await _post("meta/learn", {})


@mcp.tool()
async def calcom_event_types() -> dict:
    """List Cal.com event types. Set CALCOM_API_KEY in MCP env."""
    base = os.environ.get("CALCOM_API_URL", "https://api.cal.com").rstrip("/")
    key = os.environ.get("CALCOM_API_KEY", "")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{base}/v2/event-types",
            headers={
                "Authorization": f"Bearer {key}",
                "cal-api-version": os.environ.get("CALCOM_API_VERSION", "2024-08-13"),
            },
        )
        return r.json()


if __name__ == "__main__":
    mcp.run()
