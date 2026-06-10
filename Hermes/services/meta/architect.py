"""Campaign Architect — natural-language goal → structured blueprint → dry-run plan.

Pipeline (ADR Р-32 L3):
  goal (NL) → design_blueprint() [LLM, grounded in knowledge] → blueprint JSON
            → build_dry_run_plan() → ordered list of DRY-RUN Meta proposals (for approval)

Nothing executes here. The executor resolves refs ({{campaign}}, interest names) and
runs the approved proposals with dry_run=False only after per-action approval +
ads_management access.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from services.llm_router import call_llm
from services.meta import knowledge as k
from services.meta import tools as t

ARCHITECT_SYSTEM = (
    "You are Campaign Architect for Meta Ads. Turn a business goal into a complete, "
    "launchable campaign blueprint. Choose the objective from "
    "[OUTCOME_AWARENESS, OUTCOME_TRAFFIC, OUTCOME_ENGAGEMENT, OUTCOME_LEADS, "
    "OUTCOME_APP_PROMOTION, OUTCOME_SALES] from the BUSINESS goal (sales → OUTCOME_SALES, "
    "needs a pixel; leads → OUTCOME_LEADS; visits → OUTCOME_TRAFFIC). Never confuse attention "
    "with business results. Budgets are in CENTS (minor units). Use EITHER campaign daily_budget "
    "(CBO) OR per-adset daily_budget (ABO), not both. 1-2 ad sets. Everything launches PAUSED. "
    "Return ONLY a JSON object, no prose:\n"
    '{"objective":"OUTCOME_*","rationale":"one sentence",'
    '"campaign":{"name":"...","daily_budget_cents":5000,"special_ad_categories":[]},'
    '"adsets":[{"name":"...","optimization_goal":"OFFSITE_CONVERSIONS","billing_event":"IMPRESSIONS",'
    '"daily_budget_cents":null,"targeting":{"countries":["US"],"age_min":18,"age_max":65,'
    '"interests":["..."]},"needs_pixel":true}],'
    '"ads":[{"name":"...","adset_index":0,"primary_text":"...","headline":"...","cta":"LEARN_MORE"}]}'
    " English only."
)


def _parse_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?", "", s).strip().rstrip("`").strip()
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return {"_unparsed": text[:1500]}


def _build_input(goal: str, *, budget_cents: Optional[int], pixel_id: Optional[str],
                 countries: Optional[list[str]], niche: Optional[str]) -> str:
    return (
        f"GOAL: {goal}\n"
        f"DAILY BUDGET (cents): {budget_cents if budget_cents else 'choose a sensible default'}\n"
        f"PIXEL available: {'yes (' + pixel_id + ')' if pixel_id else 'no'}\n"
        f"GEO: {', '.join(countries) if countries else 'choose'}\n"
        f"NICHE: {niche or '(infer)'}\n\n"
        "Design the blueprint and return the JSON."
    )


async def design_blueprint(goal: str, *, budget_cents: Optional[int] = None,
                           pixel_id: Optional[str] = None, countries: Optional[list[str]] = None,
                           niche: Optional[str] = None, model: Optional[str] = None,
                           system_suffix: Optional[str] = None) -> dict:
    user = _build_input(goal, budget_cents=budget_cents, pixel_id=pixel_id,
                        countries=countries, niche=niche)
    resp = await call_llm([{"role": "user", "content": user}], system=ARCHITECT_SYSTEM,
                          system_suffix=system_suffix, complexity="medium", max_tokens=1400)
    raw = resp["choices"][0]["message"]["content"]
    bp = _parse_json(raw)
    # Light normalization / guardrails.
    obj = bp.get("objective")
    if obj in k.OBJECTIVES:
        d = k.OBJECTIVE_DEFAULTS[obj]
        for aset in bp.get("adsets", []):
            aset.setdefault("optimization_goal", d["optimization_goal"])
            aset.setdefault("billing_event", d["billing_event"])
    return bp


async def build_dry_run_plan(blueprint: dict, ad_account_id: str, *,
                             pixel_id: Optional[str] = None) -> list[dict]:
    """Blueprint → ordered DRY-RUN proposals. Refs use placeholders the executor resolves."""
    plan: list[dict] = []
    objective = blueprint["objective"]
    k.validate_objective(objective)

    camp = blueprint.get("campaign", {})
    plan.append(await t.create_campaign(
        ad_account_id, camp.get("name", "AGNT Campaign"), objective,
        daily_budget=camp.get("daily_budget_cents"),
        special_ad_categories=camp.get("special_ad_categories", []),
    ))

    for idx, aset in enumerate(blueprint.get("adsets", [])):
        tg = aset.get("targeting", {})
        targeting = t.build_targeting(
            countries=tg.get("countries"), age_min=tg.get("age_min", 18),
            age_max=tg.get("age_max", 65),
        )
        promoted = None
        if aset.get("needs_pixel") and pixel_id:
            ev = "PURCHASE" if objective == "OUTCOME_SALES" else "LEAD"
            promoted = {"pixel_id": pixel_id, "custom_event_type": ev}
        proposal = await t.create_adset(
            ad_account_id, aset.get("name", f"Ad set {idx + 1}"), "{{campaign_id}}",
            optimization_goal=aset["optimization_goal"], billing_event=aset["billing_event"],
            daily_budget=aset.get("daily_budget_cents"), targeting=targeting, promoted_object=promoted,
        )
        # Carry the interest names the executor must resolve to IDs via search_interests.
        proposal["resolve"] = {"interests": tg.get("interests", []), "adset_index": idx}
        plan.append(proposal)

    page_id = blueprint.get("page_id")
    for ad in blueprint.get("ads") or []:
        spec = t.normalize_ad_spec(ad)
        media = spec.get("media_ref") or {}
        mtype = str(media.get("type") or "image_url").lower()
        if mtype == "image_url" and media.get("value"):
            plan.append(await t.upload_ad_image(ad_account_id, image_url=str(media["value"])))
        elif mtype == "video_url" and media.get("value"):
            plan.append(await t.upload_ad_video(ad_account_id, file_url=str(media["value"])))
        plan.append(await t.create_ad_creative(
            ad_account_id,
            name=f"{spec['name']} creative",
            page_id=str(page_id or "{{page_id}}"),
            message=spec["primary_text"] or "(copy)",
            link=spec["link"] or "https://example.com",
            headline=spec.get("headline"),
            image_hash=media.get("value") if mtype == "image_hash" else None,
            video_id=media.get("value") if mtype == "video_id" else None,
            cta=spec.get("cta", "LEARN_MORE"),
        ))
        plan.append(await t.create_ad(
            ad_account_id, spec["name"], "{{adset_id}}", "{{creative_id}}",
        ))

    return plan
