"""Meta payload builders + the dry-run gate.

The single most important invariant in the repo: a write call with default
arguments must return a PROPOSAL, not reach the network. If any of these tests
ever needs a mock HTTP layer, the gate is broken.
"""
import asyncio

import pytest

from services.meta import tools as t


# ── account id normalisation ────────────────────────────────────────────────

def test_act_prefixing():
    assert t._act("123") == "act_123"
    assert t._act("act_9") == "act_9"


# ── targeting ───────────────────────────────────────────────────────────────

def test_targeting_defaults():
    tg = t.build_targeting()
    assert tg["geo_locations"] == {"countries": ["US"]}
    assert tg["targeting_automation"] == {"advantage_audience": 1}


def test_targeting_age_is_clamped_to_meta_limits():
    tg = t.build_targeting(age_min=10, age_max=99)
    assert tg["age_min"] == 13   # Meta minimum
    assert tg["age_max"] == 65   # Meta maximum


def test_targeting_interests_become_flexible_spec():
    tg = t.build_targeting(interest_ids=["1", "2"], advantage_audience=False)
    assert tg["flexible_spec"] == [{"interests": [{"id": "1"}, {"id": "2"}]}]
    assert tg["targeting_automation"] == {"advantage_audience": 0}


# ── campaign payload ────────────────────────────────────────────────────────

def test_campaign_payload_defaults_to_paused_with_required_fields():
    p = t.build_campaign_payload("N", "OUTCOME_TRAFFIC")
    assert p["status"] == "PAUSED"
    assert p["special_ad_categories"] == []  # REQUIRED by Meta, even when empty
    assert p["buying_type"] == "AUCTION"
    assert "daily_budget" not in p  # no budget → ABO, budget lives on ad sets


def test_campaign_budget_presence_means_cbo_with_bid_strategy():
    p = t.build_campaign_payload("N", "OUTCOME_SALES", daily_budget=5000)
    assert p["daily_budget"] == 5000
    assert p["bid_strategy"] == "LOWEST_COST_WITHOUT_CAP"


@pytest.mark.parametrize("kwargs", [
    {"objective": "OUTCOME_VIBES"},
    {"objective": "OUTCOME_TRAFFIC", "status": "RUNNING"},
    {"objective": "OUTCOME_TRAFFIC", "daily_budget": 100, "bid_strategy": "YOLO"},
])
def test_campaign_payload_rejects_bad_enums(kwargs):
    objective = kwargs.pop("objective")
    with pytest.raises(ValueError):
        t.build_campaign_payload("N", objective, **kwargs)


# ── ad set payload ──────────────────────────────────────────────────────────

def test_adset_payload_valid():
    p = t.build_adset_payload(
        "A", "camp1", optimization_goal="LANDING_PAGE_VIEWS",
        billing_event="IMPRESSIONS",
        promoted_object={"pixel_id": "px", "custom_event_type": "LEAD"},
    )
    assert p["campaign_id"] == "camp1"
    assert p["status"] == "PAUSED"
    assert p["promoted_object"]["pixel_id"] == "px"
    assert "targeting" in p  # falls back to default targeting, never absent


def test_adset_payload_rejects_bad_billing_event():
    with pytest.raises(ValueError):
        t.build_adset_payload("A", "c", optimization_goal="REACH", billing_event="CLICKS")


# ── creative payload ────────────────────────────────────────────────────────

def test_creative_payload_shape():
    p = t.build_creative_payload(
        "C", "page1", message="msg", link="https://x.io",
        image_hash="h1", headline="H", cta="SHOP_NOW",
    )
    ld = p["object_story_spec"]["link_data"]
    assert ld["call_to_action"] == {"type": "SHOP_NOW", "value": {"link": "https://x.io"}}
    assert ld["image_hash"] == "h1"
    assert ld["name"] == "H"


def test_creative_payload_rejects_unknown_cta():
    with pytest.raises(ValueError):
        t.build_creative_payload("C", "p", message="m", link="l", cta="BUY_OR_ELSE")


# ── ad spec normalisation / image hash extraction ───────────────────────────

def test_normalize_ad_spec_nested_creative_wins_over_flat():
    spec = t.normalize_ad_spec({
        "name": "Ad", "primary_text": "flat",
        "creative": {"primary_text": "nested", "cta": "SIGN_UP"},
    })
    assert spec["primary_text"] == "nested"
    assert spec["cta"] == "SIGN_UP"
    assert spec["adset_index"] == 0


def test_extract_image_hash_paths():
    assert t.extract_image_hash({"images": {"f.png": {"hash": "abc"}}}) == "abc"
    assert t.extract_image_hash({"hash": "xyz"}) == "xyz"
    with pytest.raises(ValueError):
        t.extract_image_hash({"images": {}})


# ── THE DRY-RUN GATE ────────────────────────────────────────────────────────

def test_writes_default_to_dry_run_proposals_not_network():
    # No mock HTTP layer exists in this file on purpose: with default args,
    # none of these coroutines may attempt a network call.
    campaign = asyncio.run(t.create_campaign("123", "N", "OUTCOME_TRAFFIC"))
    adset = asyncio.run(t.create_adset(
        "123", "A", "{{campaign_id}}",
        optimization_goal="LANDING_PAGE_VIEWS", billing_event="IMPRESSIONS",
    ))
    ad = asyncio.run(t.create_ad("123", "Ad", "as1", "cr1"))
    budget = asyncio.run(t.update_budget("camp1", 6000))
    status = asyncio.run(t.update_status("camp1", "PAUSED"))

    for proposal in (campaign, adset, ad, budget, status):
        assert proposal["dry_run"] is True
        assert {"action", "method", "endpoint", "payload", "summary"} <= set(proposal)

    assert campaign["endpoint"] == "act_123/campaigns"
    assert budget["payload"] == {"daily_budget": 6000}


def test_status_validation_holds_even_in_dry_run():
    with pytest.raises(ValueError):
        asyncio.run(t.update_status("camp1", "RUNNING"))
