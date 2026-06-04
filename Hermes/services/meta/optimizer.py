"""Optimizer — deterministic Kill / Hold / Scale over insights → dry-run proposals.

Reads campaign-level insights and applies operator rules (never confuse attention with
business results): KILL spenders with no/poor results, SCALE proven winners by +20%,
HOLD the rest. Output is advisory + a dry-run action per KILL/SCALE; the endpoint
gates execution behind per-action approval. Thresholds are conservative defaults.
"""
from __future__ import annotations

from typing import Any, Optional

MIN_SPEND = 50.0      # don't judge below this spend (too little signal)
MIN_CONV = 10         # need this many conversions to trust a SCALE
SCALE_STEP = 1.20     # +20% gradual scaling (learning-phase safe)

_CONV_TYPES = {
    "purchase", "lead", "offsite_conversion.fb_pixel_purchase",
    "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped",
    "complete_registration", "offsite_conversion.fb_pixel_complete_registration",
}


def _conversions(row: dict) -> float:
    total = 0.0
    for a in row.get("actions") or []:
        if a.get("action_type") in _CONV_TYPES:
            try:
                total += float(a.get("value", 0) or 0)
            except (TypeError, ValueError):
                pass
    return total


def _roas(row: dict) -> Optional[float]:
    arr = row.get("purchase_roas")
    if isinstance(arr, list) and arr and arr[0].get("value") is not None:
        try:
            return float(arr[0]["value"])
        except (TypeError, ValueError):
            return None
    return None


def evaluate(rows: list[dict], *, target_roas: Optional[float] = None,
             target_cpa: Optional[float] = None) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        spend = float(r.get("spend", 0) or 0)
        conv = _conversions(r)
        roas = _roas(r)
        cpa = (spend / conv) if conv else None

        verdict, reason = "HOLD", "within range or insufficient signal"
        if spend >= MIN_SPEND and conv == 0:
            verdict, reason = "KILL", f"${spend:.0f} spent, 0 conversions"
        elif target_roas and roas is not None and roas >= target_roas and conv >= MIN_CONV:
            verdict, reason = "SCALE", f"ROAS {roas:.2f} ≥ target {target_roas} on {int(conv)} conv"
        elif target_cpa and cpa is not None and cpa <= target_cpa and conv >= MIN_CONV:
            verdict, reason = "SCALE", f"CPA ${cpa:.2f} ≤ target ${target_cpa} on {int(conv)} conv"
        elif target_roas and roas is not None and roas < target_roas * 0.5 and spend >= MIN_SPEND:
            verdict, reason = "KILL", f"ROAS {roas:.2f} below half target {target_roas}"

        out.append({
            "campaign_id": r.get("campaign_id"), "name": r.get("campaign_name"),
            "spend": round(spend, 2), "conversions": conv, "roas": roas,
            "cpa": round(cpa, 2) if cpa else None, "verdict": verdict, "reason": reason,
        })
    return out
