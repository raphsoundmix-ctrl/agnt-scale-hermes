#!/usr/bin/env python3
"""Smoke: optimizer apply spec + locale=ru chat. Cleans up after."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import _bootstrap  # noqa: F401

ACCT = "applylocalesmoke"
BASE = os.environ.get("HERMES_SMOKE_URL", "http://127.0.0.1:7777")


def _cyrillic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "\u0400" <= c <= "\u04FF") / len(letters)


async def main() -> int:
    import httpx
    from services import agnt_memory as mem

    tok = os.environ.get("HERMES_INTERNAL_TOKEN", "")
    headers = {"X-Internal-Token": tok, "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120.0) as client:
        r = await client.post(
            "/agent/campaign/optimize",
            json={
                "account_id": ACCT,
                "ad_account_id": "act_100200300",
                "target_roas": 2.0,
            },
        )
        print("OPTIMIZE HTTP", r.status_code)
        if r.status_code != 200:
            print(r.text, file=sys.stderr)
            return 1
        body = r.json()
        print("SAMPLE PROPOSAL:", json.dumps(body["proposals"][0], indent=2, ensure_ascii=False))
        for p in body.get("proposals") or []:
            apply = p.get("apply") or {}
            if p["action"] in ("kill", "scale") and not (apply.get("tool") and apply.get("params")):
                print("FAIL missing apply", p, file=sys.stderr)
                return 1
            if p["action"] == "kill":
                if apply.get("tool") != "update_status":
                    print("FAIL kill tool", apply, file=sys.stderr)
                    return 1
                params = apply["params"]
                if params.get("status") != "PAUSED" or not params.get("campaign_id"):
                    print("FAIL kill params", params, file=sys.stderr)
                    return 1
            if p["action"] == "scale":
                if apply.get("tool") != "update_budget":
                    print("FAIL scale tool", apply, file=sys.stderr)
                    return 1
                params = apply["params"]
                if not params.get("campaign_id") or not isinstance(params.get("daily_budget"), int):
                    print("FAIL scale params", params, file=sys.stderr)
                    return 1

        cr = await client.post(
            "/agent/chat",
            json={
                "account_id": ACCT,
                "agent_id": "assistant",
                "message": "In one short paragraph: should I pause a campaign with CTR 0.4% and zero leads after $120 spend?",
                "locale": "ru",
            },
        )
        print("CHAT HTTP", cr.status_code)
        if cr.status_code != 200:
            print(cr.text, file=sys.stderr)
            return 1
        reply = cr.json().get("reply", "")
        lines = [ln.strip() for ln in reply.splitlines() if ln.strip()][:2]
        print("RU REPLY (2 lines):")
        for ln in lines:
            print(ln)
        if _cyrillic_ratio(reply) < 0.25:
            print("FAIL reply not Russian enough", file=sys.stderr)
            return 1

    pool = await mem._get_pool()
    async with pool.acquire() as con:
        n = await con.execute("DELETE FROM agent_memory WHERE account_id=$1", ACCT)
        print("CLEANUP", n)

    print("APPLY+LOCALE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
