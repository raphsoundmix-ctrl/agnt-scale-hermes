"""Wilson score lower confidence bound — 1:1 port of src/lib/engine/wilson.ts.

Pure math, no side effects. Conservative estimate of a rate metric when the
sample is small. Parity asserted in _parity.py (mirrors tests/engine/wilson.test.ts).
"""
from __future__ import annotations

import math
from typing import Optional

Z_95 = 1.96  # DEFAULT_THRESHOLDS.zValue (95% confidence)


def wilson_lcb(successes: float, trials: float, z: float = Z_95) -> Optional[float]:
    """LCB = (p + z²/2n - z√(p(1-p)/n + z²/4n²)) / (1 + z²/n). None if trials == 0."""
    if trials == 0:
        return None
    if successes < 0 or trials < 0:
        return None
    if successes > trials:
        return None

    p = successes / trials
    z2 = z * z
    n = trials

    denominator = 1 + z2 / n
    center = p + z2 / (2 * n)
    spread = z * math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))

    lcb = (center - spread) / denominator
    return max(0.0, lcb)


def calculate_wilson_bounds(raw: dict, z: float = Z_95) -> dict:
    """All five funnel-rate LCBs from a RawMetrics-shaped dict."""
    def g(k: str) -> float:
        return raw.get(k, 0) or 0

    return {
        "ctrLcb": wilson_lcb(g("linkClicks"), g("impressions"), z),
        "lpvPerClickLcb": wilson_lcb(g("landingPageViews"), g("linkClicks"), z),
        "leadPerLpvLcb": wilson_lcb(g("leads"), g("landingPageViews"), z),
        "qualPerLeadLcb": wilson_lcb(g("qualifiedLeads"), g("leads"), z),
        "salePerQualLcb": wilson_lcb(g("sales"), g("qualifiedLeads"), z),
    }
