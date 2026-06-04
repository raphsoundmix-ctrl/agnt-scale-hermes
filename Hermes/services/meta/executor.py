"""Campaign executor — runs an APPROVED blueprint live (dry_run=False).

Resolves the refs the dry-run plan left as placeholders: creates the campaign, threads
its real id into the ad sets, resolves interest names → ids via search_interests, and
attaches the pixel for conversion objectives. Everything is created PAUSED.

Approval-gated upstream (the user reviewed the dry-run plan; the endpoint requires
approve=true). Live writes require `ads_management`; without it Meta returns a permission
error which is surfaced, never silently swallowed.
"""
from __future__ import annotations

from typing import Any, Optional

from services.meta import knowledge as k
from services.meta import tools as t


async def _resolve_interest_ids(names: Optional[list[str]], token: Optional[str], *, limit: int = 3) -> list[str]:
    ids: list[str] = []
    for name in names or []:
        try:
            hits = await t.search_interests(name, token=token)
        except Exception:  # noqa: BLE001 — interest resolution is best-effort
            hits = []
        if hits:
            ids.append(str(hits[0]["id"]))
        if len(ids) >= limit:
            break
    return ids


async def execute_plan(blueprint: dict, ad_account_id: str, token: Optional[str], *,
                       pixel_id: Optional[str] = None) -> dict:
    objective = blueprint["objective"]
    k.validate_objective(objective)
    steps: list[dict[str, Any]] = []

    # 1. Campaign (PAUSED).
    camp = blueprint.get("campaign", {})
    cres = await t.create_campaign(
        ad_account_id, camp.get("name", "AGNT Campaign"), objective,
        daily_budget=camp.get("daily_budget_cents"),
        special_ad_categories=camp.get("special_ad_categories", []),
        dry_run=False, token=token,
    )
    campaign_id = cres.get("id")
    steps.append({"action": "create_campaign", "id": campaign_id})

    # 2. Ad sets (real campaign_id threaded in; interests resolved to ids).
    adset_ids: list[str] = []
    for idx, aset in enumerate(blueprint.get("adsets", [])):
        tg = aset.get("targeting", {})
        interest_ids = await _resolve_interest_ids(tg.get("interests"), token)
        targeting = t.build_targeting(
            countries=tg.get("countries"), age_min=tg.get("age_min", 18),
            age_max=tg.get("age_max", 65), interest_ids=interest_ids or None,
        )
        promoted = None
        if aset.get("needs_pixel") and pixel_id:
            ev = "PURCHASE" if objective == "OUTCOME_SALES" else "LEAD"
            promoted = {"pixel_id": pixel_id, "custom_event_type": ev}
        ares = await t.create_adset(
            ad_account_id, aset.get("name", f"Ad set {idx + 1}"), campaign_id,
            optimization_goal=aset["optimization_goal"], billing_event=aset["billing_event"],
            daily_budget=aset.get("daily_budget_cents"), targeting=targeting,
            promoted_object=promoted, dry_run=False, token=token,
        )
        adset_ids.append(ares.get("id"))
        steps.append({"action": "create_adset", "id": ares.get("id"),
                      "resolved_interests": interest_ids})

    return {
        "status": "created",
        "campaign_id": campaign_id,
        "adset_ids": adset_ids,
        "steps": steps,
        "note": "Created PAUSED. Ads/creatives require a Page + media (next phase). "
                "Review in Ads Manager, then activate when ready.",
    }
