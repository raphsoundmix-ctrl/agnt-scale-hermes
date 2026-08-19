# Toolchain — what I use and why

Split into three layers, so it is clear what is verifiable in this repository and what is
part of the wider stack I work in.

---

## 1. In this repository

| Layer | Tool | Why this one |
|---|---|---|
| API | **FastAPI** (async) | Every call is IO-bound — Graph API, Postgres, OpenRouter. Async is the whole workload. Pydantic models double as the request contract. |
| LLM access | **OpenRouter** | One key, one billing surface, provider-swappable slugs. Model choice becomes an env var instead of a code change. |
| Models | **Claude Sonnet 4.6 / Haiku 4.5** | Sonnet for reasoning and structured JSON, Haiku for the conversational assistant where latency and price dominate. Opus reachable via the router for the rare expensive decision. |
| Fallback | **Hermes-3 Llama 70B** | Non-Anthropic fallback so an OpenRouter outage on one provider degrades quality instead of returning 502. |
| Memory | **Postgres 16 + pgvector** | Rows and their embeddings in one table means one isolation mechanism (RLS) instead of two. |
| Isolation | **Postgres RLS** (`FORCE`, non-superuser role) | Tenant boundary enforced by the database, not by remembering a `WHERE` clause. |
| Embeddings | **fastembed** — `bge-small-en-v1.5`, 384-dim | CPU-only ONNX inside the container. No API key, no per-token cost, memory text never leaves the server. |
| Ads platform | **Meta Marketing API** (Graph `v22.0`) | The sanctioned automation channel. No UI bots, no scraping — that is how ad accounts get banned. |
| Interop | **MCP** (`mcp[cli]`, stdio) | `meta_mcp.py` exposes the Hermes campaign surface to any MCP client (Claude Code, Cursor) without duplicating logic or secrets. |
| Reliability | **tenacity** | Exponential backoff on 429/5xx/timeout at the OpenRouter boundary, plus Meta's own transient error codes handled in `services/meta/client.py`. |
| Scheduling | **APScheduler** | In-process memory-maintenance tick. Off by default; enabled by env when volume justifies it. |
| Containers | **Docker Compose** | Reproducible four-service stack. Memory limits and `cpu_shares` set explicitly because the host is shared. |
| Networking | **Tailscale Funnel** | Public ingress to a private host without opening a port or standing up a reverse proxy. Token-gated behind it. |
| Cache / queue | **Redis** | Present for the async surface. |
| Vector store | **Qdrant** | In the stack for a dormant retrieval path. Deliberately unused by agent memory — see `docs/engineering-approach.md` §7. |
| UI surface | **Next.js on Vercel** | Separate repo. Holds auth, Meta OAuth, encrypted tokens, dashboards; proxies to Hermes and contains no agent logic. |
| Scheduling context | **Cal.com** | Booking context injected into planning agents. Replaced a heavier analytics integration that was removed rather than kept "just in case". |

---

## 2. How I build — AI-assisted engineering

| Tool | How I use it |
|---|---|
| **Claude Code** | Primary development loop: reading unfamiliar code, refactors, migrations, deploy scripts. The model drafts; I own the architectural calls. |
| **Custom Claude Skills** | My Meta Ads operating playbooks encoded as reusable skills — launch protocol, account audit, AI-automation patterns, copy humanisation. The point is that the assistant argues from my standards instead of generic best practice. Several of them are the same rules the agents in this repo enforce. |
| **MCP servers** | How agents reach real systems. `meta_mcp.py` here is the custom one I wrote; I also work against GitHub, Supabase, Vercel, Figma, and n8n MCP servers. |
| **Git discipline** | Small, scoped commits with a `feat/fix/docs/chore(scope)` prefix. `deploy.sh` is idempotent, never touches `.env`, and refuses to run destructive migrations on its own. |
| **Smoke harnesses** | Post-deploy scripts that hit the live surface (`scripts/sprint2_deploy_smoke.py`) instead of trusting a green build. |

---

## 3. Marketing production stack

The domain side — this is where the operating rules encoded in the agents come from.

| Area | Tools |
|---|---|
| Paid social | Meta Ads Manager, Business Suite, Events Manager (pixel + Conversions API, Event Match Quality), Telegram Ads |
| Measurement | Meta attribution settings, Wilson-bounded rate metrics, CRM-side truth as the tiebreaker over platform-reported conversions |
| Creative production | Higgsfield (image and video pipelines, character/product consistency), Kling (image-to-video), Nano Banana / Gemini image models, Motion for templated video |
| Automation | n8n for webhook and cron orchestration, this runtime for anything that needs judgment |
| Product surface | Next.js, Vercel, Supabase, Figma |

---

## Model economics

The part that usually decides whether an AI product survives contact with a real bill.

- **Route by task, not by habit.** `services/llm_router.py` picks a tier from an explicit
  `complexity` argument or a heuristic over the input. Cheap by default; it escalates only
  on real signals of difficulty.
- **Split the system prompt.** Static persona is cached (`cache_control: ephemeral`);
  per-request context — business profile, retrieved memory, locale, platform knowledge — is
  appended uncached. Cache hit and write counts are read back from the response via
  `cache_usage()`.
- **Do not embed what you will not search.** Short-term conversation turns skip embedding
  entirely; only durable long-scope rows pay for a vector.
- **Keep failure cheap.** Retry with backoff, then a non-Anthropic fallback model, then
  surface the error. No silent degradation.
- **Keep the provider swappable.** Every model is a slug in an env var. Switching provider
  is a deploy, not a rewrite.
