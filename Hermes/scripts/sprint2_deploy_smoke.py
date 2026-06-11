#!/usr/bin/env python3
"""Post-deploy Sprint-2 smoke on throwaway account; cleans up before exit."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import _bootstrap  # noqa: F401

ACCT = "sprint2deploysmoke"
CAB1, CAB2 = "act_smoke_cab1", "act_smoke_cab2"
BASE = os.environ.get("HERMES_SMOKE_URL", "http://127.0.0.1:7777")


async def main() -> int:
    import httpx
    from services import agnt_memory as mem
    from services.meta import mock
    from services.meta import optimizer as meta_optimizer
    from services.meta import tools as meta_tools
    from services.meta.optimize_contract import format_proposal, kill_apply, scale_apply

    tok = os.environ.get("HERMES_INTERNAL_TOKEN", "")
    headers = {"X-Internal-Token": tok, "Content-Type": "application/json"}
    os.environ["META_MOCK"] = "1"

    # 1) optimize contract in-process (prod Hermes has no META_MOCK; HTTP needs workspace token)
    insights = mock.get("act_100200300/insights", {})["data"]
    verdicts = meta_optimizer.evaluate(insights, target_roas=2.0)
    campaigns = mock.get("act_100200300/campaigns", {})["data"]
    budget_by_id = {str(c["id"]): int(c.get("daily_budget") or 0) for c in campaigns}
    props: list[dict] = []
    for v in verdicts:
        cid = v.get("campaign_id")
        if v["verdict"] == "KILL":
            p = await meta_tools.update_status(str(cid), "PAUSED")
            tool, params = kill_apply(str(cid))
            props.append(format_proposal(v, p, apply_tool=tool, apply_params=params))
        elif v["verdict"] == "SCALE":
            cur = budget_by_id.get(str(cid), 0)
            p = await meta_tools.update_budget(str(cid), int(cur * 1.2))
            tool, params = scale_apply(str(cid), int(cur * 1.2))
            props.append(format_proposal(v, p, apply_tool=tool, apply_params=params))
    print("OPTIMIZE CONTRACT", len(props), "proposals")
    if not props:
        print("FAIL no proposals", file=sys.stderr)
        return 1
    for p in props:
        if not {"action", "summary", "dry_run", "apply"}.issubset(p.keys()):
            print("FAIL proposal shape", p, file=sys.stderr)
            return 1
        apply = p.get("apply") or {}
        if p["action"] in ("kill", "scale") and not (apply.get("tool") and apply.get("params")):
            print("FAIL missing apply", p, file=sys.stderr)
            return 1
        if p["dry_run"] is not True:
            print("FAIL dry_run", p, file=sys.stderr)
            return 1
    print(json.dumps({"proposals": props}, indent=2))

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120.0) as client:
        # 2) platform knowledge via chat (learning phase question)
        cr = await client.post(
            "/agent/chat",
            json={
                "account_id": ACCT,
                "agent_id": "ad_setting",
                "message": "What is the Meta ads learning phase and when does a campaign exit it?",
            },
        )
        print("CHAT HTTP", cr.status_code)
        if cr.status_code != 200:
            print(cr.text, file=sys.stderr)
            return 1
        reply = cr.json().get("reply", "")
        print("CHAT REPLY (first 500):", reply[:500])
        low = reply.lower()
        if not any(s in low for s in ("learning", "50", "7 day", "7-day", "optimization event")):
            print("FAIL chat missing learning-phase signal", file=sys.stderr)
            return 1

    # 3) cabinet isolation (memory layer)
    await mem.remember(
        ACCT, "ad_setting", "SMOKE CAB1 only secret",
        kind="fact", scope="long", ad_account_id=CAB1,
    )
    await mem.remember(
        ACCT, "ad_setting", "SMOKE CAB2 only secret",
        kind="fact", scope="long", ad_account_id=CAB2,
    )
    await mem.remember(
        ACCT, "ad_setting", "SMOKE workspace shared",
        kind="fact", scope="long", ad_account_id=None,
    )

    s1 = await mem.recall(ACCT, "ad_setting", scope="long", ad_account_id=CAB1, limit=20)
    s2 = await mem.recall(ACCT, "ad_setting", scope="long", ad_account_id=CAB2, limit=20)
    o = await mem.recall(ACCT, "orchestrator", scope="long", ad_account_id=CAB1, limit=20)
    c1 = {str(r["content"]) for r in s1}
    c2 = {str(r["content"]) for r in s2}
    co = {str(r["content"]) for r in o}
    print("CAB1", sorted(c1))
    print("CAB2", sorted(c2))
    print("ORCH", sorted(co))

    ok = (
        "SMOKE CAB1 only secret" in c1 and "SMOKE CAB2 only secret" not in c1
        and "SMOKE CAB2 only secret" in c2 and "SMOKE CAB1 only secret" not in c2
        and "SMOKE workspace shared" in c1 and "SMOKE workspace shared" in c2
        and "SMOKE CAB1 only secret" in co and "SMOKE CAB2 only secret" in co
    )
    if not ok:
        print("FAIL cabinet isolation", file=sys.stderr)
        return 1

    # 4) cleanup throwaway account (chat short msgs + smoke facts)
    pool = await mem._get_pool()
    async with pool.acquire() as con:
        n = await con.execute("DELETE FROM agent_memory WHERE account_id=$1", ACCT)
        print("CLEANUP", n)

    print("ALL DEPLOY SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
