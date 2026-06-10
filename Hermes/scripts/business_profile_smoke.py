#!/usr/bin/env python3
"""Smoke: business_profile tailors chat (LEADGEN vs ECOMMERCE). Cleans up after."""
from __future__ import annotations

import asyncio
import os
import re
import sys

ACCT = "bizprofilesmoke"
BASE = os.environ.get("HERMES_SMOKE_URL", "http://127.0.0.1:7777")
MSG = (
    "Оцени мой креатив для Meta: видео 20 сек, оффер бесплатная консультация, "
    "CTR 1.1%, spend $420, CPL $38, 11 лидов за 7 дней. Стоит ли масштабировать?"
)


def _signals(text: str) -> dict[str, bool]:
    t = text.lower()
    return {
        "cpl": bool(re.search(r"\bcpl\b|cost per lead|лид", t)),
        "lead": bool(re.search(r"\blead\b|лид|заявк", t)),
        "roas": bool(re.search(r"\broas\b|return on ad spend|purchase roas", t)),
        "ecom": bool(re.search(r"\broas\b|purchase|e-?commerce|продаж", t)),
    }


async def main() -> int:
    import httpx

    tok = os.environ.get("HERMES_INTERNAL_TOKEN", "")
    headers = {"X-Internal-Token": tok, "Content-Type": "application/json"}

    profiles = {
        "LEADGEN": {
            "niche": "LEADGEN",
            "description": "B2B marketing agency",
            "offer": "Free strategy call",
            "geo": "US",
            "primary_goal": "LEADS",
            "avg_ticket_usd": 2500,
        },
        "ECOMMERCE": {
            "niche": "ECOMMERCE",
            "description": "DTC skincare brand",
            "offer": "Vitamin C serum bundle",
            "geo": "US",
            "primary_goal": "PURCHASES",
            "avg_ticket_usd": 68,
        },
    }

    replies: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120.0) as client:
        for label, bp in profiles.items():
            r = await client.post(
                "/agent/chat",
                json={
                    "account_id": ACCT,
                    "agent_id": "creative_strategic",
                    "message": MSG,
                    "business_profile": bp,
                },
            )
            print(f"CHAT {label} HTTP", r.status_code)
            if r.status_code != 200:
                print(r.text, file=sys.stderr)
                return 1
            reply = r.json().get("reply", "")
            replies[label] = reply
            sig = _signals(reply)
            print(f"--- {label} signals:", sig)
            print(reply[:600], "\n")

    lg, ec = _signals(replies["LEADGEN"]), _signals(replies["ECOMMERCE"])
    if not (lg["cpl"] or lg["lead"]):
        print("FAIL LEADGEN missing CPL/lead framing", file=sys.stderr)
        return 1
    if lg["roas"] and not lg["cpl"]:
        print("FAIL LEADGEN over-indexed ROAS", file=sys.stderr)
        return 1
    if not (ec["roas"] or ec["ecom"]):
        print("FAIL ECOMMERCE missing ROAS/purchase framing", file=sys.stderr)
        return 1

    from services import agnt_memory as mem

    pool = await mem._get_pool()
    async with pool.acquire() as con:
        n = await con.execute("DELETE FROM agent_memory WHERE account_id=$1", ACCT)
        print("CLEANUP", n)

    print("BUSINESS PROFILE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
