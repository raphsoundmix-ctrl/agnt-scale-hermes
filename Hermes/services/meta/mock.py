"""Mock fixtures for Meta read endpoints — exercise the full tool surface without a token.

Enabled by env META_MOCK=1. Shapes mirror real Graph API responses closely enough to
build + validate tools and dry-run payloads. NOT used in production.
"""
from __future__ import annotations

from typing import Any


def get(path: str, params: dict) -> dict:
    p = path.lstrip("/")
    if p in ("me/adaccounts", "me/adaccounts/"):
        return {"data": [
            {"id": "act_100200300", "account_id": "100200300", "name": "AGNT Demo — Prospecting",
             "currency": "USD", "account_status": 1, "amount_spent": "1632100", "balance": "0"},
            {"id": "act_100200301", "account_id": "100200301", "name": "AGNT Demo — Retargeting",
             "currency": "USD", "account_status": 1, "amount_spent": "284500", "balance": "0"},
        ]}
    if p.endswith("/insights"):
        return {"data": [
            {"campaign_name": "Winner — UGC", "objective": "OUTCOME_SALES", "spend": "812.40",
             "impressions": "94210", "clicks": "1980", "ctr": "2.10", "cpc": "0.41",
             "actions": [{"action_type": "purchase", "value": "63"}], "purchase_roas": [{"value": "3.20"}]},
            {"campaign_name": "Cold — Interest Stack", "objective": "OUTCOME_SALES", "spend": "519.80",
             "impressions": "120300", "clicks": "1110", "ctr": "0.92", "cpc": "0.47",
             "actions": [{"action_type": "purchase", "value": "11"}], "purchase_roas": [{"value": "0.90"}]},
        ]}
    if p.endswith("/campaigns"):
        return {"data": [
            {"id": "23851000001", "name": "Winner — UGC", "status": "ACTIVE", "objective": "OUTCOME_SALES",
             "daily_budget": "8000", "bid_strategy": "LOWEST_COST_WITHOUT_CAP"},
            {"id": "23851000002", "name": "Cold — Interest Stack", "status": "ACTIVE", "objective": "OUTCOME_SALES",
             "daily_budget": "6000", "bid_strategy": "LOWEST_COST_WITHOUT_CAP"},
        ]}
    if p.endswith("/adsets"):
        return {"data": [
            {"id": "23851100001", "name": "Broad 25-45", "status": "ACTIVE", "daily_budget": "8000",
             "optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS"},
        ]}
    if p.endswith("/ads"):
        return {"data": [{"id": "23851200001", "name": "UGC Hook A", "status": "ACTIVE"}]}
    if p.endswith("/adspixels"):
        return {"data": [{"id": "555000111", "name": "AGNT Pixel", "code": "<mock>",
                          "last_fired_time": "2026-06-04T10:00:00+0000"}]}
    if p == "search":
        return {"data": [
            {"id": "6003139266461", "name": "Skin care", "audience_size_lower_bound": 210000000,
             "audience_size_upper_bound": 250000000, "path": ["Interests", "Beauty"]},
        ]}
    return {"data": [], "_mock_unhandled": p}


def post(path: str, data: dict) -> dict:
    # Live writes are mocked as success with a synthetic id (dry-run never reaches here).
    return {"id": "MOCK_" + path.strip("/").replace("/", "_").upper(), "_mock_echo": data}
