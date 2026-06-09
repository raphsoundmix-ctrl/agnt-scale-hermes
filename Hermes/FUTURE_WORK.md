# AGNT SCALE — Hermes FUTURE WORK (agent memory + self-improvement)

Recorded 2026-06-08 by Claude. Two deferred enhancements to the agent layer. Neither blocks current operation; isolation + per-account memory are correct and live.

## 1. Agent memory maintenance (TTL / dedup / pruning)

### DONE — foundation shipped (dormant, 2026-06-09)

- `services/mem_maintenance.py` — TTL (`MEM_TTL_DAYS`), `expires_at` cleanup, exact/cosine dedup, per-agent cap (`MEM_MAX_PER_AGENT`); all policies opt-in, `MEM_MAINT_DRY_RUN=1` by default.
- CLI `scripts/mem_maintenance.py` + `POST /agent/memory/maintain` (token-protected).
- Optional APScheduler tick (`MEM_MAINT_SCHEDULE=0` off).
- Migration `005_mem_maintenance_index.sql` — `idx_am_acct_agent_created` (applied in prod).
- Fail-closed `account_id` on agent write paths (`chat` / `run` / `note` / `campaign/plan` / `campaign/execute`) — no silent `_global` fallback.
- `MEM_DEDUP_EXACT` default `0` (all policies strictly opt-in).
- Memory cleaned to baseline **14 rows** (`cmq10`=2, `demo`=2, `_global`=10). Deployed @ `fccbdcd`; post-deploy checklist passed.
- Migration `006_mem_maint_role.sql` — `mem_maint` (`NOLOGIN NOSUPERUSER BYPASSRLS`, SELECT-only) for cross-account bucket listing; per-bucket deletes stay under `mem_app` + RLS. Graceful fallback if role missing. Deployed @ `08bd2cd`.
- `expires_at` on write in `remember()` — `short` → `now() + MEM_EXPIRES_SHORT_DAYS` (default 7d); `long` → NULL unless `MEM_EXPIRES_LONG_DAYS` set. New rows only, no backfill.

### PENDING #12

**(a) Enable policies** — turn on TTL/cap/scheduler when memory volume warrants it (14 rows today — premature).

Still deferred (not in foundation):
- contradiction resolution (newer fact supersedes older)
- semantic dedup at scale tuning

Files: `services/agnt_memory.py`, `services/mem_maintenance.py`, `routers/agnt_agent.py`. Skill ref: /agent-memory-patterns.

## 2. Self-improvement loop for agent prompts/logic (eval-gated)

Current: agent system prompts + models live in agents.py (static code); logic in services/. Updated ONLY manually via deploy.sh. Memory self-updates per interaction; prompts/logic do NOT auto-evolve (by design — no silent prompt drift on real-money accounts).

Future:
- mine production failures -> cluster root causes
- propose prompt edits / new few-shot examples
- HARD GATE: eval vs a golden set + HUMAN approval BEFORE any prod deploy
- versioned prompts (semver) + changelog + rollback

NEVER auto-deploy prompt changes to prod without eval + approval — agents operate real ad budgets.

Skills: agent-self-improvement-loop, agent-evaluator, /agent-design.
