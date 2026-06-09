"""Manual agent_memory maintenance run (dry-run by default).

Usage (inside agnt-hermes container or with DATABASE_URL set):
    python scripts/mem_maintenance.py
    MEM_TTL_DAYS=30 MEM_MAX_PER_AGENT=500 MEM_MAINT_DRY_RUN=1 python scripts/mem_maintenance.py
    MEM_MAINT_DRY_RUN=0 MEM_TTL_DAYS=7 python scripts/mem_maintenance.py  # destructive — needs approval
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.mem_maintenance import run_maintenance


async def main() -> None:
    report = await run_maintenance()
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
