# Agent Memory Audit & Maintenance (#12)

Generated: 2026-06-09. Branch: `agent-memory-maintenance`. **No prod DELETE, no deploy** — awaiting raph approval.

---

## A — Schema audit (read-only)

### DDL summary

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| id | bigint | NOT NULL | serial PK |
| account_id | text | NOT NULL | — |
| ad_account_id | text | NULL | — |
| agent_id | text | NOT NULL | — |
| scope | text | NOT NULL | CHECK (`short` \| `long`) |
| kind | text | NOT NULL | — |
| content | text | NOT NULL | — |
| meta | jsonb | NOT NULL | `{}` |
| created_at | timestamptz | NOT NULL | `now()` |
| expires_at | timestamptz | NULL | — |
| embedding | vector(384) | NULL | — |

### Indexes

| Index | Definition |
|-------|------------|
| agent_memory_pkey | btree (id) |
| idx_am_acct_agent_scope | (account_id, agent_id, scope) |
| idx_am_acct_adacct | (account_id, ad_account_id) |
| idx_am_acct_created | (account_id, created_at DESC) |
| idx_am_embed | ivfflat (embedding vector_cosine_ops) |
| idx_am_acct_agent_created | **pending** — migration `005_mem_maintenance_index.sql` |

### RLS

- ENABLE + **FORCE** on `agent_memory`
- Policies: `mem_read`, `mem_insert`, `mem_update`, `mem_delete` scoped to `app.account_id` + `app.agent_id`
- App role: `mem_app` — NOSUPERUSER, **NOBYPASSRLS**

### What exists vs what #12 needs

| Need | Status |
|------|--------|
| `created_at` for TTL / cap eviction | **Present** (001) |
| `updated_at` / `last_accessed_at` | **Missing** — not required for v1; cap/TTL use `created_at` |
| `expires_at` | **Present but unused** — could be wired later for per-row TTL |
| Content hash column | **Missing** — dedup uses `md5(content)` at query time (no migration required) |
| Embedding for cosine dedup | **Present** (384-dim, long scope only) |
| Composite index (account_id, agent_id, created_at) | **In migration 005** — apply on deploy |

---

## B — Test garbage cleanup (dry-run only)

### Protected (never delete without explicit scope)

| account_id | rows | note |
|------------|------|------|
| cmq10poe100014kia5zexgxwa | 2 | real workspace |
| _global | 10 | platform knowledge |
| demo | 2 | **needs raph confirmation** before any delete |

### Delete allowlist (test runs) — dry-run counts

| account_id | rows to DELETE |
|------------|----------------|
| healthz | 2 |
| memtest | 8 |
| runtest1 | 4 |
| slugtest | 4 |
| plantest | 1 |
| embed1 | 4 |
| orchx1 | 8 |
| **Total** | **31** |

### Planned SQL (after raph says **go**)

```sql
BEGIN;
SELECT account_id, count(*) FROM agent_memory
WHERE account_id IN ('healthz','memtest','runtest1','slugtest','plantest','embed1','orchx1')
GROUP BY 1 ORDER BY 1;

-- review output, then:
DELETE FROM agent_memory
WHERE account_id IN ('healthz','memtest','runtest1','slugtest','plantest','embed1','orchx1');

SELECT account_id, agent_id, count(*) FROM agent_memory GROUP BY 1,2 ORDER BY 1,2;
-- expect 14 rows remaining (real + _global + demo)
COMMIT;  -- or ROLLBACK
```

**Status:** not executed — waiting for approval.

---

## C — `_global` / `_platform` audit (read-only)

### Contents (10 rows)

| kind | source | purpose |
|------|--------|---------|
| primer, fact, rule (×8) | `scripts/seed_meta_knowledge.py` | Meta Ads domain baseline from `services/meta/knowledge.py` |
| version_state | `services/meta/watcher.py` | API version baseline (v22.0) |
| drift | `services/meta/watcher.py` | Live API drift sample from test/create_campaign |

### Write paths

1. **`scripts/seed_meta_knowledge.py`** — manual/one-off seed (`mem.remember("_global", "_platform", …)`)
2. **`services/meta/watcher.py`** — `record_api_learning()` on version change + drift errors; cron hits `/agent/meta/learn`
3. **`routers/agnt_agent.py`** — `campaign/plan` and `campaign/execute` write to `account_id or "_global"` with **`agent_id="ad_setting"`** (not `_platform`) when workspace id missing — minor inconsistency, not cross-tenant

### Read paths / isolation

- Chat/run/search endpoints call `mem.recall/search(req.account_id, …)` — **only the workspace bucket**
- RLS verified: workspace `cmq10poe100014kia5zexgxwa` sees **2 rows**, `_global` rows = **0**
- `_global/_platform` readable only when session scoped to `account_id=_global`, `agent_id=_platform` → **10 rows**

### Conclusion

- **Not a cross-account leak channel** — RLS isolates `_global` like any other account_id
- **Platform knowledge is NOT merged into agent chat today** — `agents.py` mentions "platform-knowledge memory" but runtime never queries `_global`; knowledge is effectively dormant until a merge step is added (future work, not #12)
- Writes to `_global` are **operational/platform**, not per-user — no per-user pollution bug in `_platform` bucket
- **`demo` account** is separate tenant data (2 short msgs) — confirm with raph before cleanup

---

## D — Maintenance module (#12)

### Files added/changed

| File | Purpose |
|------|---------|
| `services/mem_maintenance.py` | TTL, exact dedup (md5), cosine dedup, per-agent cap |
| `scripts/mem_maintenance.py` | CLI manual trigger |
| `routers/agnt_agent.py` | `POST /agent/memory/maintain` (token-protected) |
| `main.py` | Optional APScheduler (`MEM_MAINT_SCHEDULE=1`) |
| `migrations/005_mem_maintenance_index.sql` | Composite index |
| `requirements.txt` | APScheduler 3.10.4 |
| `.env.example` | Documented env vars |

### Env flags (safe defaults)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MEM_TTL_DAYS` | `0` | Off. Delete rows older than N days |
| `MEM_TTL_SCOPE` | `short` | `short` \| `long` \| `all` |
| `MEM_MAX_PER_AGENT` | `0` | Off. Cap rows per (account_id, agent_id), evict oldest |
| `MEM_DEDUP_EXACT` | `1` | Collapse identical content within bucket |
| `MEM_DEDUP_COSINE` | `0` | Off. Near-dup threshold on long embeddings (e.g. `0.97`) |
| `MEM_MAINT_DRY_RUN` | `1` | **Log only, no DELETE** |
| `MEM_MAINT_SKIP_ACCOUNTS` | `_global` | Skip maintenance for platform bucket |
| `MEM_MAINT_SCHEDULE` | `0` | Off. Enable in-process scheduler |
| `MEM_MAINT_INTERVAL_HOURS` | `24` | Scheduler interval |

Destructive: set `MEM_MAINT_DRY_RUN=0` **only after human approval**.

### RLS behaviour

- Enumerates buckets as pool owner (`agnt`, BYPASSRLS)
- Per-bucket work: `SET LOCAL ROLE mem_app` + `app.account_id` / `app.agent_id` — same as production memory client
- Deletes only what RLS would allow for that bucket

### Dry-run on current prod data (SQL simulation, 2026-06-09)

**Exact dedup** (`MEM_DEDUP_EXACT=1`):

| account_id | agent_id | would delete |
|------------|----------|--------------|
| embed1 | ad_setting | 1 |
| memtest | assistant | 4 |
| slugtest | creative_strategic | 1 |
| **Total** | | **6** |

**TTL** (`MEM_TTL_DAYS=7`, scope=short): **0 rows** (all data ≤ 5 days old)

**Cap** (`MEM_MAX_PER_AGENT=2` example):

| account_id | agent_id | total | would evict |
|------------|----------|-------|-------------|
| memtest | assistant | 8 | 6 |
| orchx1 | creative_strategic | 4 | 2 |
| slugtest | creative_strategic | 4 | 2 |

`_global` skipped when `MEM_MAINT_SKIP_ACCOUNTS=_global` (default).

---

## Verification commands

```bash
# Health
curl -s localhost:7778/health

# Row distribution
docker exec agnt-postgres psql -U agnt -d agnt -c \
  "SELECT account_id, agent_id, count(*) FROM agent_memory GROUP BY 1,2 ORDER BY 1,2;"

# RLS isolation spot-check
docker exec agnt-postgres psql -U agnt -d agnt -c "
BEGIN; SET LOCAL ROLE mem_app;
SELECT set_config('app.account_id','cmq10poe100014kia5zexgxwa',true);
SELECT set_config('app.agent_id','assistant',true);
SELECT count(*) FROM agent_memory WHERE account_id='_global';
ROLLBACK;"

# After deploy + migration 005 — CLI dry-run (inside container)
docker exec -e PYTHONPATH=/app agnt-hermes python scripts/mem_maintenance.py

# HTTP dry-run (needs X-Internal-Token)
TOK=$(grep '^HERMES_INTERNAL_TOKEN=' ~/Container2/Hermes/.env | cut -d= -f2)
curl -s -X POST -H "X-Internal-Token: $TOK" http://localhost:7778/agent/memory/maintain
```

---

## Next steps (require raph **go**)

1. **Task B:** run DELETE transaction for 31 test rows (allowlist above)
2. **Deploy:** `./deploy.sh` + apply `migrations/005_mem_maintenance_index.sql`
3. **demo:** confirm keep or delete before any cleanup
4. **Maintenance:** run dry-run via endpoint/CLI, review logs, then optionally enable policies with `MEM_MAINT_DRY_RUN=0`
