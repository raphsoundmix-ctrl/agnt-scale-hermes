# AGNT SCALE — Hermes Agent Runtime

Server-side **brain** of **AGNT SCALE** (AI Decision OS for Meta Ads).
Path-A native runtime: an isolated FastAPI gateway, **not** coupled to MAO.

- **UI (Next.js, Vercel):** [agnt_scale_meta](https://github.com/raphsoundmix-ctrl/agnt_scale_meta) — dashboards, Meta OAuth, proxy to Hermes
- **This repo (Hermes):** agents, memory (Postgres RLS + embeddings), Meta execute/optimize, orchestration

Architecture: `docs/agnt-scale-architecture.md`

## Stack
- FastAPI gateway (`Hermes/`), token-auth via `X-Internal-Token`
- LLM via OpenRouter (`services/llm_router.py`) — Sonnet 4.5 / Haiku 3.5, provider-swappable
- Memory: Postgres (pgvector) with **RLS isolation** per-account / per-agent; the orchestrator reads ALL agents in its account
- Embeddings: **local** `fastembed` (`bge-small-en-v1.5`, 384-dim) — no API key, memory text never leaves the server
- Docker Compose: `agnt-hermes` (7778) · `agnt-postgres` (pgvector) · `agnt-redis` · `agnt-qdrant`

## Agents
`creative_strategic` · `script_writer` · `ad_setting` · `assistant` · `orchestrator`

## Endpoints (`/agent`)
- `POST /chat` — conversational; per-agent short-term memory; orchestrator pulls cross-agent semantic memory
- `POST /run` — structured task → JSON (critique / script+humanize / diagnosis); persisted to long-term memory
- `POST /note` — write-gate; only durable, signal-bearing facts reach long-term
- `POST /memory/search` — semantic recall (cosine, RLS-scoped)
- `GET /agents`, `GET /memory/ping`

## Deploy
1. `cp .env.example .env` and `cp Hermes/.env.example Hermes/.env`, then fill in the keys
2. `docker compose up -d --build`
3. Migrations: `cat Hermes/migrations/00{1,2,3,4}*.sql | docker exec -i agnt-postgres psql -U agnt -d agnt`
4. Health: `curl localhost:7778/health`

> Design contract: Obsidian **Р-31** — AGNT SCALE × Hermes (Agents, Memory, Orchestration) ADR.
> Note: some files (`legal.py`, `visual_worker.py`, `fal_video.py`, `knowledge/`, `skills/`) are inherited
> MAO scaffolding, currently dormant under path-A. Kept for reference; safe to prune later.
