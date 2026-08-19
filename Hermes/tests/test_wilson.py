"""Wilson LCB — the guard against low-volume false winners.

Complements services/engine/_parity.py (TS↔Python behavioural parity); here we
pin the properties the optimizer relies on.
"""
from services.engine.wilson import calculate_wilson_bounds, wilson_lcb


def test_degenerate_inputs_return_none():
    assert wilson_lcb(0, 0) is None
    assert wilson_lcb(-1, 100) is None
    assert wilson_lcb(5, -10) is None
    assert wilson_lcb(200, 100) is None  # successes > trials is corrupt data


def test_lcb_is_conservative():
    # LCB never exceeds the observed rate, and is strictly below it on small samples.
    lcb = wilson_lcb(5, 10)
    assert lcb is not None
    assert 0 < lcb < 0.5


def test_readme_claim_2_clicks_on_40_impressions():
    # The exact scenario the docs promise to guard against: 2/40 looks like a
    # 5% CTR — the LCB keeps it under 2%, so it never reads as a winner.
    lcb = wilson_lcb(2, 40)
    assert lcb is not None
    assert lcb < 0.02


def test_confidence_grows_with_sample_at_same_rate():
    small = wilson_lcb(5, 50)
    large = wilson_lcb(50, 500)
    assert small is not None and large is not None
    assert large > small


def test_perfect_small_sample_is_still_penalized():
    # 3/3 must not read as a guaranteed 100% rate.
    assert wilson_lcb(3, 3) < 0.5


def test_bounds_dict_shape_and_zero_data():
    b = calculate_wilson_bounds({})
    assert set(b) == {"ctrLcb", "lpvPerClickLcb", "leadPerLpvLcb",
                      "qualPerLeadLcb", "salePerQualLcb"}
    assert all(v is None for v in b.values())


def test_bounds_zero_successes_on_valid_data_is_zero_not_none():
    # 0/100 is a measured zero, not missing data.
    b = calculate_wilson_bounds({"impressions": 5000, "linkClicks": 100})
    assert b["lpvPerClickLcb"] == 0
