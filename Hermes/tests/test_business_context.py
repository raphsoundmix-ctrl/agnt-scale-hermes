"""Business-profile → uncached system suffix. Empty context must stay empty."""
from services.business_context import (
    BusinessProfile,
    enrich_task_input,
    format_business_context_suffix,
    format_locale_suffix,
    merge_system_suffix,
)


def test_no_profile_no_suffix():
    assert format_business_context_suffix(None) is None
    assert format_business_context_suffix(BusinessProfile()) is None
    assert format_business_context_suffix({"niche": "  "}) is None  # whitespace ≠ data


def test_profile_suffix_contains_facts_and_the_ecommerce_guard():
    s = format_business_context_suffix({"niche": "LOCAL_SERVICES", "avg_ticket_usd": 120})
    assert "niche=LOCAL_SERVICES" in s
    assert "$120" in s
    assert "do NOT assume e-commerce" in s


def test_locale_suffix_only_for_russian():
    assert format_locale_suffix("ru") is not None
    assert "Russian" in format_locale_suffix("ru-RU")
    assert format_locale_suffix("en-US") is None
    assert format_locale_suffix(None) is None


def test_merge_drops_empties_and_preserves_order():
    assert merge_system_suffix(None, None) is None
    assert merge_system_suffix("a", None, "b") == "ab"


def test_enrich_only_fills_gaps_for_niche_sensitive_agents():
    profile = BusinessProfile(niche="FITNESS", offer="8-week program")
    # Untouched agent: identity, not a copy.
    inp = {"x": 1}
    assert enrich_task_input("optimizer", inp, profile) is inp
    # script_writer: fills missing fields...
    out = enrich_task_input("script_writer", {}, profile)
    assert out["niche"] == "FITNESS"
    assert out["offer"] == "8-week program"
    # ...but never overrides what the caller provided.
    out2 = enrich_task_input("script_writer", {"offer": "custom"}, profile)
    assert out2["offer"] == "custom"
