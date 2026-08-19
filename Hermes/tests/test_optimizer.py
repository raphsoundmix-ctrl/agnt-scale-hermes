"""Kill / Hold / Scale — the verdicts that move money must be reproducible.

Every case here is an operator rule stated in the README; if a threshold
changes, a test fails and the docs must change with it.
"""
from services.meta import optimizer


def _row(spend, *, purchases=None, leads=None, roas=None, cid="c1", name="camp"):
    actions = []
    if purchases is not None:
        actions.append({"action_type": "purchase", "value": str(purchases)})
    if leads is not None:
        actions.append({"action_type": "lead", "value": str(leads)})
    row = {"campaign_id": cid, "campaign_name": name,
           "spend": str(spend), "actions": actions}
    if roas is not None:
        row["purchase_roas"] = [{"value": str(roas)}]
    return row


def _one(row, **targets):
    out = optimizer.evaluate([row], **targets)
    assert len(out) == 1
    return out[0]


def test_threshold_contract():
    # Pin the operator constants — a silent change here is a product change.
    assert optimizer.MIN_SPEND == 50.0
    assert optimizer.MIN_CONV == 10
    assert optimizer.SCALE_STEP == 1.20


def test_below_min_spend_is_never_judged():
    v = _one(_row(49.99))
    assert v["verdict"] == "HOLD"


def test_spend_with_zero_conversions_is_kill():
    v = _one(_row(80))
    assert v["verdict"] == "KILL"
    assert "0 conversions" in v["reason"]


def test_scale_requires_target_met_and_min_conversions():
    v = _one(_row(200, purchases=12, roas=3.0), target_roas=2.0)
    assert v["verdict"] == "SCALE"
    assert "ROAS" in v["reason"]


def test_low_volume_winner_is_held_not_scaled():
    # ROAS beats target but only 5 conversions — the false-positive guard.
    v = _one(_row(200, purchases=5, roas=3.0), target_roas=2.0)
    assert v["verdict"] == "HOLD"


def test_roas_below_half_target_is_kill():
    v = _one(_row(200, purchases=20, roas=0.8), target_roas=2.0)
    assert v["verdict"] == "KILL"


def test_cpa_target_scale_path():
    # $100 spend, 20 leads → CPA $5 ≤ target $10 → SCALE.
    v = _one(_row(100, leads=20), target_cpa=10.0)
    assert v["verdict"] == "SCALE"
    assert v["cpa"] == 5.0


def test_no_targets_means_no_scale_ever():
    v = _one(_row(500, purchases=100, roas=9.0))
    assert v["verdict"] == "HOLD"


def test_conversions_sum_across_action_types():
    v = _one(_row(30, purchases=3, leads=2))
    assert v["conversions"] == 5.0


def test_output_shape():
    v = _one(_row(80, cid="123", name="X"))
    assert v["campaign_id"] == "123"
    assert v["name"] == "X"
    assert v["spend"] == 80.0
    assert v["cpa"] is None  # zero conversions → CPA undefined, not zero
