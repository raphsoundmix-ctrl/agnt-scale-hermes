"""Cal.com API — scheduling links for campaign review / launch checkpoints.

Use Cal.com Cloud or self-hosted instance. Do NOT embed Cal UI in the app —
agents surface booking links in chat/plan context only.
Docs: https://cal.com/docs/api-reference/v2
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


def _base() -> str:
    return (os.environ.get("CALCOM_API_URL") or "https://api.cal.com").rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("CALCOM_API_KEY", "").strip()
    if not key:
        raise ValueError("CALCOM_API_KEY not configured")
    return {
        "Authorization": f"Bearer {key}",
        "cal-api-version": os.environ.get("CALCOM_API_VERSION", "2024-06-14"),
    }


async def list_event_types(*, username: Optional[str] = None) -> list[dict[str, Any]]:
    user = (username or os.environ.get("CALCOM_USERNAME") or "").strip()
    params = {"username": user} if user else None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{_base()}/v2/event-types", headers=_headers(), params=params)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and "data" in body:
            return body["data"] if isinstance(body["data"], list) else []
        return body if isinstance(body, list) else []


def booking_link(username: str, slug: str, base_url: Optional[str] = None) -> str:
    """Public booking URL (no API call)."""
    root = (base_url or os.environ.get("CALCOM_BOOKING_URL") or "https://cal.com").rstrip("/")
    return f"{root}/{username.strip('/')}/{slug.strip('/')}"


async def format_context_suffix(
    *,
    username: Optional[str] = None,
    review_slug: str = "campaign-review",
) -> Optional[str]:
    """Suggest a booking link for launch/review checkpoints."""
    user = (username or os.environ.get("CALCOM_USERNAME") or "").strip()
    if not user:
        return None
    link = booking_link(user, review_slug)
    extra = ""
    if os.environ.get("CALCOM_API_KEY"):
        try:
            types = await list_event_types()
            names = [str(t.get("title") or t.get("slug")) for t in types[:5]]
            if names:
                extra = f" Available event types: {', '.join(names)}."
        except Exception:  # noqa: BLE001
            pass
    return (
        f"\n\n[SCHEDULING — Cal.com] For launch/review checkpoints, offer booking: {link}.{extra} "
        "Use for human approval gates, not auto-spend."
    )
