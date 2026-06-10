"""Per-workspace business profile → dynamic LLM system suffix (never cached)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class BusinessProfile(BaseModel):
    niche: Optional[str] = None
    description: Optional[str] = None
    offer: Optional[str] = None
    geo: Optional[str] = None
    primary_goal: Optional[str] = None
    avg_ticket_usd: Optional[float] = None


def _val(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def format_business_context_suffix(profile: Optional[BusinessProfile | dict[str, Any]]) -> Optional[str]:
    """Build uncached system tail from workspace business_profile payload."""
    if profile is None:
        return None
    if isinstance(profile, dict):
        profile = BusinessProfile.model_validate(profile)
    niche = _val(profile.niche)
    description = _val(profile.description)
    offer = _val(profile.offer)
    geo = _val(profile.geo)
    goal = _val(profile.primary_goal)
    ticket = profile.avg_ticket_usd
    if not any((niche, description, offer, geo, goal, ticket is not None)):
        return None
    ticket_s = f"{ticket:g}" if ticket is not None else "(n/a)"
    return (
        "\n\nBUSINESS CONTEXT: "
        f"niche={niche or '(n/a)'}, sells={description or '(n/a)'}, offer={offer or '(n/a)'}, "
        f"geo={geo or '(n/a)'}, goal={goal or '(n/a)'}, avg ticket=${ticket_s}. "
        "Tailor advice to this; do NOT assume e-commerce/ROAS unless niche is ECOMMERCE."
    )


def merge_system_suffix(*parts: Optional[str]) -> Optional[str]:
    chunks = [p for p in parts if p]
    return "".join(chunks) if chunks else None


def enrich_task_input(agent_id: str, inp: dict[str, Any], profile: Optional[BusinessProfile]) -> dict[str, Any]:
    """Merge business_profile into structured task input for niche-sensitive agents."""
    if not profile or agent_id not in ("creative_strategic", "script_writer"):
        return inp
    out = dict(inp)
    if agent_id == "creative_strategic":
        if not _val(out.get("niche")) and profile.niche:
            out["niche"] = profile.niche
    elif agent_id == "script_writer":
        if not _val(out.get("niche")) and profile.niche:
            out["niche"] = profile.niche
        if not _val(out.get("offer")) and profile.offer:
            out["offer"] = profile.offer
    return out
