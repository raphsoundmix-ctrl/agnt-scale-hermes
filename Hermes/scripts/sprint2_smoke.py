#!/usr/bin/env python3
"""Sprint 2 smoke: optimize contract, platform knowledge, cabinet isolation."""
from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> int:
    from services.meta import optimizer as opt
    from services.meta import tools as t
    from services.meta import mock
    from services.meta.optimize_contract import format_proposal, kill_apply, scale_apply
    from services import agnt_memory as mem

    os.environ["META_MOCK"] = "1"
    insights = mock.get("act_100200300/insights", {})["data"]
    verdicts = opt.evaluate(insights, target_roas=2.0)
    campaigns = mock.get("act_100200300/campaigns", {})["data"]
    budget_by_id = {str(c["id"]): int(c.get("daily_budget") or 0) for c in campaigns}
    proposals: list[dict] = []
    for v in verdicts:
        cid = v.get("campaign_id")
        if v["verdict"] == "KILL":
            p = await t.update_status(str(cid), "PAUSED")
            tool, params = kill_apply(str(cid))
            proposals.append(format_proposal(v, p, apply_tool=tool, apply_params=params))
        elif v["verdict"] == "SCALE":
            cur = budget_by_id.get(str(cid), 0)
            if cur:
                new_budget = int(cur * opt.SCALE_STEP)
                p = await t.update_budget(str(cid), new_budget)
                tool, params = scale_apply(str(cid), new_budget)
                proposals.append(format_proposal(v, p, apply_tool=tool, apply_params=params))

    print("=== OPTIMIZE CONTRACT ===")
    print(json.dumps({"proposals": proposals}, indent=2))
    required = {"action", "summary", "dry_run", "apply"}
    for p in proposals:
        if not required.issubset(p.keys()):
            print("FAIL missing keys", p, file=sys.stderr)
            return 1
        if p["dry_run"] is not True:
            print("FAIL dry_run", p, file=sys.stderr)
            return 1
        apply = p.get("apply") or {}
        if not apply.get("tool") or not apply.get("params"):
            print("FAIL missing apply", p, file=sys.stderr)
            return 1

    print("\n=== PLATFORM KNOWLEDGE ===")
    pk = await mem.search_platform_knowledge("Meta learning phase optimization events", limit=3)
    print(f"rows={len(pk)}")
    for r in pk:
        print("-", str(r["content"])[:140])
    if not pk:
        print("FAIL no platform knowledge", file=sys.stderr)
        return 1
    joined = " ".join(str(r["content"]) for r in pk).lower()
    if "learning" not in joined and "50" not in joined:
        print("WARN learning-phase signal weak in retrieved rows")

    print("\n=== CABINET ISOLATION ===")
    acct = "sprint2smoke"
    cab1, cab2 = "act_cab1", "act_cab2"
    await mem.remember(
        acct, "ad_setting", "CAB1 secret fact for cabinet one",
        kind="fact", scope="long", ad_account_id=cab1,
    )
    await mem.remember(
        acct, "ad_setting", "CAB2 secret fact for cabinet two",
        kind="fact", scope="long", ad_account_id=cab2,
    )
    await mem.remember(
        acct, "ad_setting", "WORKSPACE level shared fact",
        kind="fact", scope="long", ad_account_id=None,
    )

    s1 = await mem.recall(acct, "ad_setting", scope="long", ad_account_id=cab1, limit=20)
    s2 = await mem.recall(acct, "ad_setting", scope="long", ad_account_id=cab2, limit=20)
    o = await mem.recall(acct, "orchestrator", scope="long", ad_account_id=cab1, limit=20)

    c1 = {str(r["content"]) for r in s1}
    c2 = {str(r["content"]) for r in s2}
    co = {str(r["content"]) for r in o}
    print("cab1 sees:", sorted(c1))
    print("cab2 sees:", sorted(c2))
    print("orch sees:", sorted(co))

    ok1 = "CAB1 secret fact for cabinet one" in c1 and "CAB2 secret fact for cabinet two" not in c1
    ok2 = "CAB2 secret fact for cabinet two" in c2 and "CAB1 secret fact for cabinet one" not in c2
    ok_ws1 = "WORKSPACE level shared fact" in c1
    ok_ws2 = "WORKSPACE level shared fact" in c2
    oko = "CAB1 secret fact for cabinet one" in co and "CAB2 secret fact for cabinet two" in co

    pool = await mem._get_pool()
    async with pool.acquire() as con:
        await con.execute("DELETE FROM agent_memory WHERE account_id=$1", acct)

    if not (ok1 and ok2 and ok_ws1 and ok_ws2 and oko):
        print("FAIL isolation", ok1, ok2, ok_ws1, ok_ws2, oko, file=sys.stderr)
        return 1

    print("\nALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
