"""Domain model consistency — the enums the architect and executor trust."""
import pytest

from services.meta import knowledge as k


def test_validate_objective():
    k.validate_objective("OUTCOME_SALES")
    with pytest.raises(ValueError):
        k.validate_objective("CONVERSIONS")  # pre-ODAX name must be rejected


def test_validate_optimization_goal():
    k.validate_optimization_goal("OFFSITE_CONVERSIONS")
    with pytest.raises(ValueError):
        k.validate_optimization_goal("CLICKS")


def test_every_objective_has_internally_consistent_defaults():
    # Guards the tables against drifting apart when the watcher updates them.
    assert set(k.OBJECTIVE_DEFAULTS) == set(k.OBJECTIVES)
    for obj, d in k.OBJECTIVE_DEFAULTS.items():
        assert d["optimization_goal"] in k.OPTIMIZATION_GOALS, obj
        assert d["billing_event"] in k.BILLING_EVENTS, obj
        assert isinstance(d["needs_pixel"], bool), obj


def test_sales_is_the_pixel_gated_objective():
    assert k.defaults_for("OUTCOME_SALES")["needs_pixel"] is True


def test_defaults_for_returns_a_copy():
    d = k.defaults_for("OUTCOME_TRAFFIC")
    d["optimization_goal"] = "MUTATED"
    assert k.OBJECTIVE_DEFAULTS["OUTCOME_TRAFFIC"]["optimization_goal"] == "LANDING_PAGE_VIEWS"
