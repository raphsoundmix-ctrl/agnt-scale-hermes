"""Meta Ads tools — read functions + write payload builders.

Writes default to DRY-RUN: they return a uniform "proposed action" dict
{action, method, endpoint, payload, summary, dry_run:True} for human approval.
Call with dry_run=False (only after approval + ads_management access) to execute.
Validation raises ValueError early so bad payloads never reach Meta.
"""
from __future__ import annotations

from typing import Any, Optional

from services.meta import knowledge as k
from services.meta.client import graph_get, graph_post

_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED", "DELETED"}


def _act(account_id: str) -> str:
    s = str(account_id)
    return s if s.startswith("act_") else f"act_{s}"


def _proposed(action: str, endpoint: str, payload: dict, summary: str) -> dict:
    return {"action": action, "method": "POST", "endpoint": endpoint,
            "payload": payload, "summary": summary, "dry_run": True}


# ── READ ────────────────────────────────────────────────────────────────────────

async def list_ad_accounts(*, token: Optional[str] = None) -> list[dict]:
    fields = "account_id,name,currency,account_status,amount_spent,balance"
    r = await graph_get("me/adaccounts", token=token, params={"fields": fields, "limit": 100})
    return r.get("data", [])


async def get_insights(account_id: str, *, level: str = "campaign", date_preset: str = "last_7d",
                       token: Optional[str] = None) -> list[dict]:
    fields = ("campaign_name,adset_name,objective,spend,impressions,reach,frequency,clicks,"
              "ctr,cpc,cpm,actions,cost_per_action_type,purchase_roas")
    params = {"level": level, "date_preset": date_preset, "fields": fields, "limit": 200}
    r = await graph_get(f"{_act(account_id)}/insights", token=token, params=params)
    return r.get("data", [])


async def list_campaigns(account_id: str, *, token: Optional[str] = None) -> list[dict]:
    fields = "id,name,status,objective,daily_budget,lifetime_budget,bid_strategy,start_time,stop_time"
    r = await graph_get(f"{_act(account_id)}/campaigns", token=token, params={"fields": fields, "limit": 200})
    return r.get("data", [])


async def list_adsets(account_id: str, *, token: Optional[str] = None) -> list[dict]:
    fields = ("id,name,status,campaign_id,daily_budget,lifetime_budget,optimization_goal,"
              "billing_event,bid_strategy,targeting")
    r = await graph_get(f"{_act(account_id)}/adsets", token=token, params={"fields": fields, "limit": 200})
    return r.get("data", [])


async def list_ads(account_id: str, *, token: Optional[str] = None) -> list[dict]:
    r = await graph_get(f"{_act(account_id)}/ads", token=token,
                        params={"fields": "id,name,status,adset_id,creative", "limit": 200})
    return r.get("data", [])


async def list_pixels(account_id: str, *, token: Optional[str] = None) -> list[dict]:
    r = await graph_get(f"{_act(account_id)}/adspixels", token=token,
                        params={"fields": "id,name,last_fired_time,is_unavailable", "limit": 50})
    return r.get("data", [])


async def search_interests(query: str, *, token: Optional[str] = None) -> list[dict]:
    r = await graph_get("search", token=token,
                        params={"type": "adinterest", "q": query, "limit": 25})
    return r.get("data", [])


# ── TARGETING ─────────────────────────────────────────────────────────────────

def build_targeting(*, countries: Optional[list[str]] = None, age_min: int = 18, age_max: int = 65,
                    genders: Optional[list[int]] = None, interest_ids: Optional[list[str]] = None,
                    platforms: Optional[list[str]] = None, advantage_audience: bool = True) -> dict:
    t: dict[str, Any] = {
        "geo_locations": {"countries": countries or ["US"]},
        "age_min": max(13, int(age_min)),
        "age_max": min(65, int(age_max)),
    }
    if genders:
        t["genders"] = genders  # [1]=male, [2]=female
    if interest_ids:
        t["flexible_spec"] = [{"interests": [{"id": i} for i in interest_ids]}]
    if platforms:
        t["publisher_platforms"] = platforms  # ["facebook","instagram","audience_network","messenger"]
    # Advantage+ Audience: let Meta expand beyond the defined set (recommended default).
    t["targeting_automation"] = {"advantage_audience": 1 if advantage_audience else 0}
    return t


# ── WRITE (dry-run by default) ──────────────────────────────────────────────────

def build_campaign_payload(name: str, objective: str, *, status: str = "PAUSED",
                           special_ad_categories: Optional[list[str]] = None,
                           daily_budget: Optional[int] = None,
                           bid_strategy: Optional[str] = None) -> dict:
    k.validate_objective(objective)
    if status not in _STATUSES:
        raise ValueError(f"bad status '{status}'")
    if bid_strategy and bid_strategy not in k.BID_STRATEGIES:
        raise ValueError(f"bad bid_strategy '{bid_strategy}'")
    payload: dict[str, Any] = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": special_ad_categories or [],  # REQUIRED by Meta
        "buying_type": "AUCTION",
    }
    if daily_budget:  # presence = CBO (Advantage Campaign Budget)
        payload["daily_budget"] = int(daily_budget)
        payload["bid_strategy"] = bid_strategy or "LOWEST_COST_WITHOUT_CAP"
    return payload


async def create_campaign(account_id: str, name: str, objective: str, *, dry_run: bool = True,
                          token: Optional[str] = None, **kw) -> dict:
    payload = build_campaign_payload(name, objective, **kw)
    endpoint = f"{_act(account_id)}/campaigns"
    if dry_run:
        return _proposed("create_campaign", endpoint, payload,
                         f"Create campaign '{name}' ({objective}, {payload['status']})")
    return await graph_post(endpoint, token=token, data=payload)


def build_adset_payload(name: str, campaign_id: str, *, optimization_goal: str, billing_event: str,
                        daily_budget: Optional[int] = None, targeting: Optional[dict] = None,
                        promoted_object: Optional[dict] = None, bid_strategy: Optional[str] = None,
                        bid_amount: Optional[int] = None, status: str = "PAUSED",
                        start_time: Optional[str] = None, end_time: Optional[str] = None) -> dict:
    k.validate_optimization_goal(optimization_goal)
    if billing_event not in k.BILLING_EVENTS:
        raise ValueError(f"bad billing_event '{billing_event}'. Valid: {k.BILLING_EVENTS}")
    if status not in _STATUSES:
        raise ValueError(f"bad status '{status}'")
    payload: dict[str, Any] = {
        "name": name,
        "campaign_id": str(campaign_id),
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "targeting": targeting or build_targeting(),
        "status": status,
    }
    if daily_budget:  # ABO (per-adset budget); omit when the campaign is CBO
        payload["daily_budget"] = int(daily_budget)
    if bid_strategy:
        payload["bid_strategy"] = bid_strategy
    if bid_amount:
        payload["bid_amount"] = int(bid_amount)
    if promoted_object:
        payload["promoted_object"] = promoted_object  # {pixel_id, custom_event_type} for conversions
    if start_time:
        payload["start_time"] = start_time
    if end_time:
        payload["end_time"] = end_time
    return payload


async def create_adset(account_id: str, name: str, campaign_id: str, *, dry_run: bool = True,
                       token: Optional[str] = None, **kw) -> dict:
    payload = build_adset_payload(name, campaign_id, **kw)
    endpoint = f"{_act(account_id)}/adsets"
    if dry_run:
        return _proposed("create_adset", endpoint, payload,
                         f"Create ad set '{name}' → {payload['optimization_goal']}")
    return await graph_post(endpoint, token=token, data=payload)


def build_creative_payload(name: str, page_id: str, *, message: str, link: str,
                           image_hash: Optional[str] = None, video_id: Optional[str] = None,
                           cta: str = "LEARN_MORE", headline: Optional[str] = None) -> dict:
    if cta not in k.CALL_TO_ACTIONS:
        raise ValueError(f"bad cta '{cta}'. Valid: {k.CALL_TO_ACTIONS}")
    link_data: dict[str, Any] = {"message": message, "link": link,
                                 "call_to_action": {"type": cta, "value": {"link": link}}}
    if headline:
        link_data["name"] = headline
    if image_hash:
        link_data["image_hash"] = image_hash
    if video_id:
        link_data["video_id"] = video_id
    return {"name": name, "object_story_spec": {"page_id": str(page_id), "link_data": link_data}}


async def create_ad_creative(account_id: str, *, dry_run: bool = True, token: Optional[str] = None,
                             **kw) -> dict:
    payload = build_creative_payload(**kw)
    endpoint = f"{_act(account_id)}/adcreatives"
    if dry_run:
        return _proposed("create_ad_creative", endpoint, payload, f"Create creative '{payload['name']}'")
    return await graph_post(endpoint, token=token, data=payload)


async def create_ad(account_id: str, name: str, adset_id: str, creative_id: str, *,
                    status: str = "PAUSED", dry_run: bool = True, token: Optional[str] = None) -> dict:
    if status not in _STATUSES:
        raise ValueError(f"bad status '{status}'")
    payload = {"name": name, "adset_id": str(adset_id), "creative": {"creative_id": str(creative_id)},
               "status": status}
    endpoint = f"{_act(account_id)}/ads"
    if dry_run:
        return _proposed("create_ad", endpoint, payload, f"Create ad '{name}'")
    return await graph_post(endpoint, token=token, data=payload)


async def update_budget(object_id: str, daily_budget: int, *, dry_run: bool = True,
                        token: Optional[str] = None) -> dict:
    payload = {"daily_budget": int(daily_budget)}
    if dry_run:
        return _proposed("update_budget", str(object_id), payload,
                         f"Set daily_budget={daily_budget} on {object_id}")
    return await graph_post(str(object_id), token=token, data=payload)


async def update_status(object_id: str, status: str, *, dry_run: bool = True,
                        token: Optional[str] = None) -> dict:
    if status not in _STATUSES:
        raise ValueError(f"bad status '{status}'")
    payload = {"status": status}
    if dry_run:
        return _proposed("update_status", str(object_id), payload, f"Set status={status} on {object_id}")
    return await graph_post(str(object_id), token=token, data=payload)
