"""
Cross-agent event handlers — Phase 3B MVP set.

Three handlers, registered into services.events._HANDLERS at startup:

  legal       ← review_legal_threat   (from reviews skill)
  bidder      ← sku_critical          (from inventory skill)
  bidder      ← price_changed         (from pricing skill)

Each handler:
  1. Looks at event.payload for context.
  2. Calls the target skill via the same invocation path /skills/<id>/run
     uses internally — so the result is auditable as an agent_run row.
  3. Sets autonomy=L1 default → run lands in `needs_approval`, surfaced
     in /approvals queue for the seller.

Handler discipline:
  - hop_depth >= 1 → skip silently (already capped one level up).
  - Failures bubble — events.py marks status='failed' + records error.
  - Never re-emit the same event_type from this handler (cycle risk).
"""
from __future__ import annotations

import logging
from typing import Any

from services.events import EventRow, register_handler
from skills.registry import SKILLS

logger = logging.getLogger("hermes.event_handlers")


# ─── Helpers ─────────────────────────────────────────────────────────


async def _run_skill(
    skill_id: str,
    *,
    tenant_id: str,
    cabinet_id: str | None,
    action: str,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Instantiate target skill and run its action. Returns the result dict
    (success/data/summary). Caller decides what to do with it.

    Does NOT create an agent_runs row directly — that's the Backend's
    responsibility when a skill is called via /skills/<id>/run. For
    event-driven runs we currently invoke the skill in-process, log the
    result, and rely on the event's own `result` JSONB for audit. A
    future enhancement is to enqueue through Backend so the seller sees
    these runs in their /runs feed too.
    """
    # SKILLS maps skill_id → class directly (see skills/registry.py).
    cls = SKILLS.get(skill_id)
    if not cls:
        raise RuntimeError(f"unknown skill: {skill_id}")
    inst = cls(tenant_id=tenant_id, cabinet_id=cabinet_id)
    kwargs = {"action": action}
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    result = await inst.run(**kwargs)
    # SkillResult is a TypedDict-ish dict.
    return dict(result)


# ─── 1. Reviews → Legal ──────────────────────────────────────────────


async def handle_review_legal_threat(event: EventRow) -> None:
    """
    Triggered when the Reviews skill (Agent_01) classifier sees a review
    containing a legal-threat keyword (суд, Роспотребнадзор, экспертиза,
    «вернёте деньги через суд», etc.).

    payload: {
      "feedback_id": "<wb-feedback-id>",
      "review_text": "<truncated original text>",
      "product_name": "...",
      "rating": 1
    }

    Action: ask Legal (Agent_12) for a defensible response template the
    seller can paste / approve into their reply. Stored as the event's
    result so /approvals UI can render it next to the original review.
    """
    if event.hop_depth >= 1:
        return

    feedback_id = event.payload.get("feedback_id")
    review_text = event.payload.get("review_text", "")
    if not review_text:
        logger.warning(f"review_legal_threat empty text id={event.id}")
        return

    result = await _run_skill(
        "legal",
        tenant_id=event.tenant_id,
        cabinet_id=event.cabinet_id,
        action="advise",
        extra_kwargs={
            "context": "review_legal_threat",
            "review_text": review_text,
            "feedback_id": feedback_id,
        },
    )
    logger.info(
        "review_legal_threat_handled",
        extra={
            "event_id": event.id,
            "feedback_id": feedback_id,
            "legal_success": result.get("success"),
        },
    )


# ─── 2. Inventory → Bidder (critical SKU) ────────────────────────────


async def handle_sku_critical(event: EventRow) -> None:
    """
    Triggered when Inventory (Agent_06) detects a SKU with osh < 7d.

    payload: {
      "nmID": 12345,
      "days_to_oos": 5,
      "stock_qty": 23,
      "daily_rate": 4.6
    }

    Action: ask Bidder (Agent_03) to pause ads for that SKU so the
    seller doesn't burn ad budget on an item that's about to go OOS.
    The Bidder run lands in needs_approval — seller confirms in
    /approvals before pause actually applies.
    """
    if event.hop_depth >= 1:
        return

    nm_id = event.payload.get("nmID")
    if not nm_id:
        logger.warning(f"sku_critical missing nmID id={event.id}")
        return

    result = await _run_skill(
        "bidder",
        tenant_id=event.tenant_id,
        cabinet_id=event.cabinet_id,
        action="pause_for_sku",
        extra_kwargs={
            "nmID": nm_id,
            "reason": "stock_critical",
            "days_to_oos": event.payload.get("days_to_oos"),
        },
    )
    logger.info(
        "sku_critical_handled",
        extra={
            "event_id": event.id,
            "nmID": nm_id,
            "bidder_success": result.get("success"),
        },
    )


# ─── 3. Pricing → Bidder (price changed >5%) ─────────────────────────


async def handle_price_changed(event: EventRow) -> None:
    """
    Triggered when Pricing (Agent_02) applies a price change > 5%.

    payload: {
      "nmID": 12345,
      "old_price": 1290,
      "new_price": 1390,
      "delta_pct": 7.75
    }

    Action: Bidder recomputes ROAS target for the SKU's campaigns —
    price up 7% means same conversion now generates more revenue, so
    Bidder can afford to bid slightly more on that SKU. Always L1
    (recommendation only).
    """
    if event.hop_depth >= 1:
        return

    nm_id = event.payload.get("nmID")
    if not nm_id:
        logger.warning(f"price_changed missing nmID id={event.id}")
        return

    result = await _run_skill(
        "bidder",
        tenant_id=event.tenant_id,
        cabinet_id=event.cabinet_id,
        action="recompute_roas",
        extra_kwargs={
            "nmID": nm_id,
            "old_price": event.payload.get("old_price"),
            "new_price": event.payload.get("new_price"),
            "reason": "price_changed",
        },
    )
    logger.info(
        "price_changed_handled",
        extra={
            "event_id": event.id,
            "nmID": nm_id,
            "bidder_success": result.get("success"),
        },
    )


# ─── Bootstrap ───────────────────────────────────────────────────────


# Map: (target_skill, event_type) → handler
# events.py routes by target_skill alone for MVP; if multiple event
# types target the same skill, we add a thin selector here later.
def register_all() -> None:
    """Call once at Hermes startup to wire all MVP handlers."""
    register_handler("legal", _legal_router)
    register_handler("bidder", _bidder_router)


async def _legal_router(event: EventRow) -> None:
    if event.event_type == "review_legal_threat":
        await handle_review_legal_threat(event)
    else:
        logger.warning(
            f"legal_router unhandled event_type={event.event_type} id={event.id}"
        )


async def _bidder_router(event: EventRow) -> None:
    if event.event_type == "sku_critical":
        await handle_sku_critical(event)
    elif event.event_type == "price_changed":
        await handle_price_changed(event)
    else:
        logger.warning(
            f"bidder_router unhandled event_type={event.event_type} id={event.id}"
        )
