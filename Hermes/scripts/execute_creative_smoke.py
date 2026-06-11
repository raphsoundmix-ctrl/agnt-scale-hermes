#!/usr/bin/env python3
"""Smoke: campaign execute with creative chain (META_MOCK=1). Cleans up after."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import _bootstrap  # noqa: F401

ACCT = "executecreativesmoke"
BASE = os.environ.get("HERMES_SMOKE_URL", "http://127.0.0.1:7777")

BLUEPRINT = {
    "objective": "OUTCOME_TRAFFIC",
    "campaign": {"name": "Smoke Creative Campaign", "daily_budget_cents": 5000},
    "adsets": [{
        "name": "Smoke Ad Set",
        "optimization_goal": "LINK_CLICKS",
        "billing_event": "IMPRESSIONS",
        "targeting": {"countries": ["US"]},
    }],
    "page_id": "987654321",
    "ads": [{
        "name": "Smoke Ad",
        "adset_index": 0,
        "primary_text": "Visit our site today.",
        "headline": "Learn more",
        "cta": "LEARN_MORE",
        "link": "https://example.com/landing",
        "media_ref": {"type": "image_url", "value": "https://example.com/image.jpg"},
    }],
}


async def main() -> int:
    import httpx
    from services import agnt_memory as mem

    tok = os.environ.get("HERMES_INTERNAL_TOKEN", "")
    headers = {"X-Internal-Token": tok, "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120.0) as client:
        r = await client.post(
            "/agent/campaign/execute",
            json={
                "account_id": ACCT,
                "ad_account_id": "act_100200300",
                "blueprint": BLUEPRINT,
                "approve": True,
            },
        )
        print("EXECUTE HTTP", r.status_code)
        if r.status_code != 200:
            print(r.text, file=sys.stderr)
            return 1
        body = r.json()
        print(json.dumps(body, indent=2))
        actions = {s.get("action") for s in body.get("steps") or []}
        needed = {"create_campaign", "create_adset", "upload_ad_image", "create_ad_creative", "create_ad"}
        if not needed.issubset(actions):
            print("FAIL missing steps", actions, file=sys.stderr)
            return 1
        if not body.get("campaign_id") or not body.get("adset_ids"):
            print("FAIL missing ids", file=sys.stderr)
            return 1
        if not body.get("creative_ids") or not body.get("ad_ids"):
            print("FAIL missing creative/ad ids", file=sys.stderr)
            return 1

    pool = await mem._get_pool()
    async with pool.acquire() as con:
        n = await con.execute("DELETE FROM agent_memory WHERE account_id=$1", ACCT)
        print("CLEANUP", n)

    print("EXECUTE CREATIVE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
