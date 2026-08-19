"""Optimizer → UI proposal contract. The app renders exactly this shape."""
from services.meta.optimize_contract import format_proposal, kill_apply, scale_apply


def test_kill_proposal_shape():
    verdict = {"verdict": "KILL", "campaign_id": 123, "reason": "$80 spent, 0 conversions"}
    proposed = {"summary": "Set status=PAUSED on 123"}
    tool, params = kill_apply("123")
    p = format_proposal(verdict, proposed, apply_tool=tool, apply_params=params)
    assert p["action"] == "kill"
    assert p["campaign_id"] == "123"          # always a string for the UI
    assert p["dry_run"] is True
    assert p["apply"] == {"tool": "update_status",
                          "params": {"campaign_id": "123", "status": "PAUSED"}}


def test_scale_apply_carries_integer_budget():
    tool, params = scale_apply("42", 7200)
    assert tool == "update_budget"
    assert params == {"campaign_id": "42", "daily_budget": 7200}


def test_summary_falls_back_to_verdict_reason():
    p = format_proposal({"verdict": "SCALE", "campaign_id": "1", "reason": "why"},
                        {}, apply_tool="update_budget", apply_params={})
    assert p["summary"] == "why"


def test_unknown_verdict_degrades_to_hold_like_action():
    p = format_proposal({"verdict": "HOLD", "campaign_id": "1"},
                        {}, apply_tool="noop", apply_params={})
    assert p["action"] == "hold"
