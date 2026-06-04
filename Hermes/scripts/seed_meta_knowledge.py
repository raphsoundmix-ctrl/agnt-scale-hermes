"""Seed the GLOBAL platform-knowledge memory with Meta Ads domain facts.

Stored under the reserved scope (account_id="_global", agent_id="_platform") so it is
shared across every workspace/agent (not tenant data). Each row is embedded (scope=long)
so agents can semantic-search platform knowledge. The changelog watcher refreshes these.

Run (in the Hermes container):
    docker exec -e PYTHONPATH=/app agnt-hermes python scripts/seed_meta_knowledge.py
Idempotent-ish: re-running appends; safe but creates duplicates — run once per refresh.
"""
from __future__ import annotations

import asyncio
import os

from services.meta import knowledge as k
from services import agnt_memory as mem

ACCT, AGENT = "_global", "_platform"


async def main() -> None:
    n = 0

    async def put(text: str, kind: str) -> None:
        nonlocal n
        await mem.remember(ACCT, AGENT, text, kind=kind, scope="long",
                           meta={"domain": "meta_ads", "source": "knowledge.py"})
        n += 1

    await put(k.PLATFORM_PRIMER, "primer")
    await put("Meta campaign objectives (ODAX): "
              + "; ".join(f"{o} = {d}" for o, d in k.OBJECTIVES.items()), "fact")
    await put("Optimization goals: "
              + "; ".join(f"{g} = {d}" for g, d in k.OPTIMIZATION_GOALS.items()), "fact")
    await put("Objective defaults (optimization_goal / billing / needs_pixel): "
              + str(k.OBJECTIVE_DEFAULTS), "fact")
    await put("Standard pixel conversion events: " + ", ".join(k.PIXEL_EVENTS), "fact")
    await put("Bid strategies: "
              + "; ".join(f"{b} = {d}" for b, d in k.BID_STRATEGIES.items()), "fact")
    await put("SAFETY RULE: configure campaigns via the official Marketing API, never a UI bot. "
              "Writes default to DRY-RUN; every spend/state-changing action requires per-action "
              "human approval. Respect Business-Use-Case rate limits; make gradual (+20%) changes.", "rule")
    await put(f"Meta Graph API version pinned: {os.environ.get('META_API_VERSION', 'v22.0')}. "
              "The changelog watcher reconciles version drift + deprecations.", "fact")

    print(f"seeded {n} platform-knowledge rows under ({ACCT}, {AGENT})")
    rows = await mem.recall(ACCT, AGENT, scope="long", limit=30)
    print("recall sees", len(rows), "rows; kinds:", sorted({r["kind"] for r in rows}))


if __name__ == "__main__":
    asyncio.run(main())
