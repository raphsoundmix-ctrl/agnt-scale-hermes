"""Campaign executor — runs an APPROVED blueprint live (dry_run=False).

Resolves the refs the dry-run plan left as placeholders: creates the campaign, threads
its real id into the ad sets, resolves interest names → ids via search_interests, and
attaches the pixel for conversion objectives. When blueprint.ads + page_id are present,
uploads media, creates ad creatives and ads (all PAUSED).

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


async def _resolve_media(
    account_id: str,
    media_ref: Optional[dict],
    token: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (image_hash, video_id) from media_ref or pre-uploaded ids."""
    if not media_ref:
        return None, None
    mtype = str(media_ref.get("type") or "image_url").lower()
    value = media_ref.get("value") or media_ref.get("url")
    if not value:
        raise ValueError("media_ref.value is required")
    if mtype == "image_hash":
        return str(value), None
    if mtype == "video_id":
        return None, str(value)
    if mtype == "image_url":
        res = await t.upload_ad_image(account_id, image_url=str(value), dry_run=False, token=token)
        return t.extract_image_hash(res), None
    if mtype == "video_url":
        res = await t.upload_ad_video(account_id, file_url=str(value), dry_run=False, token=token)
        vid = res.get("id")
        if not vid:
            raise ValueError("Meta advideos response missing id")
        return None, str(vid)
    raise ValueError(f"unsupported media_ref.type '{mtype}'")


async def execute_plan(
    blueprint: dict,
    ad_account_id: str,
    token: Optional[str],
    *,
    pixel_id: Optional[str] = None,
    page_id: Optional[str] = None,
) -> dict:
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
    steps.append({"action": "create_campaign", "id": campaign_id, "status": "PAUSED"})

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
        adset_id = ares.get("id")
        adset_ids.append(adset_id)
        steps.append({"action": "create_adset", "id": adset_id, "status": "PAUSED",
                      "resolved_interests": interest_ids})

    # 3. Ads + creatives (optional — requires page_id + media_ref per ad).
    creative_ids: list[str] = []
    ad_ids: list[str] = []
    ads_spec = blueprint.get("ads") or []
    resolved_page_id = page_id or blueprint.get("page_id")

    if ads_spec:
        if not resolved_page_id:
            raise ValueError(
                "page_id required to create ads — pass in execute request or blueprint.page_id "
                "(Facebook Page connected to the ad account)"
            )
        for ad in ads_spec:
            spec = t.normalize_ad_spec(ad)
            if not spec["primary_text"]:
                raise ValueError(f"ad '{spec['name']}' missing primary_text")
            if not spec["link"]:
                raise ValueError(f"ad '{spec['name']}' missing link")
            adset_idx = spec["adset_index"]
            if adset_idx < 0 or adset_idx >= len(adset_ids):
                raise ValueError(f"adset_index {adset_idx} out of range")

            mtype = str((spec.get("media_ref") or {}).get("type") or "image_url").lower()
            image_hash, video_id = await _resolve_media(ad_account_id, spec.get("media_ref"), token)
            if not image_hash and not video_id:
                raise ValueError(
                    f"ad '{spec['name']}' needs media_ref "
                    "(image_url|image_hash|video_url|video_id)"
                )
            if mtype == "image_url":
                steps.append({"action": "upload_ad_image", "hash": image_hash, "status": "PAUSED"})
            elif mtype == "video_url":
                steps.append({"action": "upload_ad_video", "id": video_id, "status": "PAUSED"})

            cres = await t.create_ad_creative(
                ad_account_id,
                name=f"{spec['name']} creative",
                page_id=str(resolved_page_id),
                message=spec["primary_text"],
                link=spec["link"],
                headline=spec.get("headline"),
                image_hash=image_hash,
                video_id=video_id,
                cta=spec.get("cta", "LEARN_MORE"),
                dry_run=False,
                token=token,
            )
            creative_id = cres.get("id")
            creative_ids.append(creative_id)
            steps.append({"action": "create_ad_creative", "id": creative_id, "status": "PAUSED"})

            ad_res = await t.create_ad(
                ad_account_id, spec["name"], adset_ids[adset_idx], creative_id,
                status="PAUSED", dry_run=False, token=token,
            )
            ad_id = ad_res.get("id")
            ad_ids.append(ad_id)
            steps.append({"action": "create_ad", "id": ad_id, "status": "PAUSED"})

    note = "Created PAUSED."
    if creative_ids:
        note += f" {len(creative_ids)} creative(s), {len(ad_ids)} ad(s)."
    else:
        note += " Ads/creatives skipped (no blueprint.ads). Review in Ads Manager, then activate."

    return {
        "status": "created",
        "campaign_id": campaign_id,
        "adset_ids": adset_ids,
        "creative_ids": creative_ids,
        "ad_ids": ad_ids,
        "page_id": resolved_page_id,
        "steps": steps,
        "note": note,
    }
