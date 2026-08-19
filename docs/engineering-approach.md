# How I build this — approach, principles, and the things I refused to build

This document exists because the code alone does not show the reasoning. Every rule below
is enforced somewhere in this repository; the file reference is the proof.

---

## The thesis

**An agent may think freely. It may not spend freely.**

AGNT SCALE runs on live Meta ad budgets. That single fact drives almost every design
decision here: what is deterministic vs. what is LLM-driven, where the approval gate sits,
what the memory boundary is, and why prompts do not deploy themselves.

---

## How I scope a project

The order matters. I have watched too many AI projects invert it and end up with an
impressive demo that cannot be turned on.

1. **Where does money move?** Find every action that changes spend. That set becomes the
   approval surface. Nothing else is negotiable afterwards.
2. **What is the isolation boundary?** For a multi-tenant ad tool, it is the workspace.
   Written down before the first table (`ISOLATION.md`, `migrations/002_rls.sql`).
3. **What must be deterministic?** Anything a client will argue with — a Kill verdict, a
   budget step, a confidence bound. Those go in Python, not a prompt.
4. **What actually needs a model?** Creative judgment, goal interpretation, blueprint
   design, synthesis. That is a short list, and it stays short.
5. **What does it cost per call, at scale?** Model tier, cache hit rate, embedding cost.
   If I cannot answer this, the feature is not designed yet.
6. **What breaks when the platform changes?** Meta ships breaking API versions. The system
   has to notice on its own (`services/meta/watcher.py`).

---

## Principles, with the code that enforces them

### 1. Approval is structural, not a prompt instruction

An LLM told "ask before spending" will eventually not ask. So the write path is closed by
default in the tool layer: every write builds a *proposed action* dict and returns it.
Reaching the live Graph API requires `dry_run=False`, which requires an approved execute
path, which requires `approve=true` on the endpoint.

> `services/meta/tools.py` · `services/meta/client.py` (`graph_post` docstring) ·
> `routers/agnt_agent.py::campaign_execute`

Every object the executor creates is created `PAUSED`. Approving a plan and starting spend
are two separate human acts.

### 2. Tenant isolation belongs in the database, not in the query

App-level `WHERE account_id = ...` is one forgotten filter away from a cross-tenant leak.
Agent memory runs under a non-superuser role (`mem_app`) with `FORCE ROW LEVEL SECURITY`
and transaction-scoped `SET LOCAL app.account_id / app.agent_id`. A buggy query returns
nothing instead of returning someone else's data.

The orchestrator is the one deliberate exception: RLS lets it **read** every agent inside
its own account, and still **write** only as itself.

> `migrations/002_rls.sql` · `services/agnt_memory.py::_scope`

### 3. Fail closed on identity

`account_id` has no default. An agent write without a workspace raises `400` rather than
falling back to a shared `_global` bucket. A silent fallback is how one client's data ends
up in another client's context window.

> `routers/agnt_agent.py::_require_account_id`

### 4. Secrets do not get copied

Hermes never stores Meta OAuth tokens. The app holds them encrypted and decrypts one per
request, passing `meta_token` in the body. The brain stays stateless on credentials — if
this server is compromised, no long-lived ad-account access leaks with it.

> `docs/meta-oauth-hermes-bridge.md` · `routers/agnt_agent.py::MetaReadRequest`

### 5. Determinism where determinism is cheap

Kill / Hold / Scale is arithmetic, not opinion:

- below `$50` spend → no verdict at all (the sample is noise)
- `$50+` spent with `0` conversions → **KILL**
- ROAS under half of target at `$50+` → **KILL**
- **SCALE** only when the target is met **and** there are `≥10` conversions behind it
- scaling step is `+20%`, because larger jumps re-enter the learning phase

Rate metrics get a Wilson lower confidence bound, so 2 clicks on 40 impressions never
reads as a 5% CTR winner.

The same numbers produce the same verdict every time, and I can defend each one to a
client. An LLM writes the explanation; it does not cast the vote.

> `services/meta/optimizer.py` · `services/engine/wilson.py`

### 6. Cost is a design constraint, not an afterthought

- **Tiering.** Haiku for the conversational assistant (latency and price), Sonnet for
  reasoning, Opus reachable through the router but never the default.
- **Prompt caching.** The system message is split: the static persona carries
  `cache_control: ephemeral`, the per-request context (business profile, memory, locale,
  platform knowledge) is appended as an uncached suffix. Cache-hit metrics are read back
  from the response.
- **Local embeddings.** `bge-small-en-v1.5` (384-dim) runs on CPU inside the container.
  No API key, no per-token cost, and memory text never leaves the server.
- **Short-term turns are not embedded at all.** Only durable rows pay the embedding cost.

> `services/llm_router.py` · `services/embeddings.py` · `services/agnt_memory.py::remember`

### 7. One isolation mechanism, not two

Qdrant is in the compose stack for a dormant path. Agent memory deliberately does **not**
use it: vectors live in the same Postgres table as the rows they belong to, so RLS is the
single boundary. Two stores would mean two places to get isolation right.

### 8. Systems must notice when the ground moves

Meta deprecates API versions on its own schedule. Rather than scrape a changelog, the
watcher learns from two robust signals: the pinned API version compared to the last
recorded one, and live error text matching drift patterns (`deprecat`, `unknown field`,
`no longer supported`). Findings are written to global platform knowledge and retrieved
semantically into every agent's context.

> `services/meta/watcher.py` · `services/agnt_memory.py::search_platform_knowledge`

### 9. Prompts do not deploy themselves

Memory updates itself on every interaction. Prompts and logic do not. On accounts moving
real budget, silent prompt drift is an incident with no audit trail. The planned
self-improvement loop is gated on an eval against a golden set **plus** human approval,
with versioned prompts and a rollback path.

> `Hermes/FUTURE_WORK.md`

### 10. Ship dormant, enable on evidence

Memory maintenance (TTL, dedup, per-agent cap, scheduler) is fully implemented and
entirely off. `MEM_MAINT_DRY_RUN=1` by default; every policy is opt-in. At 14 rows of
production memory, turning on pruning would have been theatre. The foundation ships so
enabling it later is a config change, not a project.

> `services/mem_maintenance.py` · `Hermes/FUTURE_WORK.md`

### 11. Write the boundary contract before the code

This runtime shares a server with an unrelated project. `ISOLATION.md` states the reserved
ports, container names, network, and paths, and names what must never be touched — before
any container was added. Container memory limits and `cpu_shares` are set so a runaway
agent process cannot starve the neighbour.

> `ISOLATION.md` · `docker-compose.yml`

---

## What I deliberately did not build

Refusals are a design output. These are the ones I would defend in a review:

| Not built | Why |
|---|---|
| Browser automation of Ads Manager | It is a ban risk and a ToS problem. The Marketing API is the sanctioned automation channel, and it is what the executor uses. |
| Auto-apply on budget and status changes | Removes the only human checkpoint on spend. Proposals carry an `apply` payload; a person triggers it. |
| Self-evolving prompts in production | No eval gate, no rollback, no audit trail. Deferred until all three exist. |
| Hosted embedding API | 384-dim recall over short operator notes does not justify per-token cost or shipping client memory to a third party. |
| TTL and pruning turned on at launch | Premature optimisation against 14 rows. Shipped dormant instead. |
| A second analytics integration | Plausible was added, then removed in favour of Cal.com context only. Fewer moving parts beat a fuller feature list. |
| Agent memory in a vector DB | Splitting rows and vectors across two stores means getting tenant isolation right twice. |

---

## How I actually work

- **Contracts first.** `services/meta/optimize_contract.py` exists so the UI shape and the
  internal dry-run shape can evolve independently. Same instinct behind `ISOLATION.md`.
- **Ported logic gets a parity test.** `services/engine/wilson.py` is a 1:1 port of a
  TypeScript implementation, and `_parity.py` asserts they agree. `deploy.sh` runs it on
  every deploy.
- **Mock mode is a first-class path.** `META_MOCK=1` returns fixtures, so the entire Meta
  surface is exercisable without a token — which also makes an offline eval harness cheap
  to add later.
- **Smoke scripts, not ceremony.** `scripts/sprint2_deploy_smoke.py` and friends check the
  real deployed surface after `deploy.sh`.
- **Honest docs.** The README says which files are dormant scaffolding. `FUTURE_WORK.md`
  says what is deferred and why. A reviewer should not have to discover that on their own.

---

## AI-assisted, not AI-generated

I build with Claude Code as the primary development loop, and I encode my own Meta Ads
operating playbooks as reusable skills so the assistant argues from my standards instead of
generic advice. The rule I hold to: **the model drafts, I own the decisions.** Every
threshold, every gate, and every refusal in this document is a call I made and can defend —
which is exactly why they are written down here rather than left implicit in the code.

See `docs/toolchain.md` for the full stack, and `docs/roadmap.md` for where this goes next.
