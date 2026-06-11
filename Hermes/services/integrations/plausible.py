"""Plausible Analytics Stats API — cookieless landing-page metrics for agents.

Self-host: plausible/community-edition (separate stack). Cloud: plausible.io.
Docs: https://plausible.io/docs/stats-api
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_BASE = "https://plausible.io"


def _base() -> str:
    return (os.environ.get("PLAUSIBLE_API_URL") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("PLAUSIBLE_API_KEY", "").strip()
    if not key:
        raise ValueError("PLAUSIBLE_API_KEY not configured")
    return {"Authorization": f"Bearer {key}"}


async def site_stats(
    site_id: str,
    *,
    period: str = "7d",
    metrics: str = "visitors,pageviews,bounce_rate,visit_duration",
) -> dict[str, Any]:
    """Aggregate stats for a site (v1 stats API)."""
    params = {
        "site_id": site_id,
        "period": period,
        "metrics": metrics,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{_base()}/api/v1/stats/aggregate",
            headers=_headers(),
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def format_context_suffix(
    site_id: Optional[str],
    *,
    period: str = "7d",
) -> Optional[str]:
    """Dynamic agent suffix — skipped when not configured."""
    if not site_id or not os.environ.get("PLAUSIBLE_API_KEY"):
        return None
    try:
        data = await site_stats(site_id, period=period)
        results = (data.get("results") or {}) if isinstance(data, dict) else {}
        visitors = results.get("visitors", {}).get("value", "?")
        pageviews = results.get("pageviews", {}).get("value", "?")
        bounce = results.get("bounce_rate", {}).get("value", "?")
        return (
            f"\n\n[SITE ANALYTICS — Plausible {period}] "
            f"visitors={visitors}, pageviews={pageviews}, bounce_rate={bounce}%. "
            "Cross-check Meta LPV/leads with on-site traffic; do not treat as Meta attribution."
        )
    except Exception:  # noqa: BLE001
        return None
