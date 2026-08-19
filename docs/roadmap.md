# Roadmap — where this goes next

This runtime is a working foundation, not a finished product. What follows is the plan I
would execute on it, ordered by how much each step is worth relative to the work it takes.

Two themes run through all of it:

- **Hermes Agent** — turning agents from prompt-plus-injected-context into agents that
  choose and call tools inside a fenced, approval-gated loop.
- **A custom agent base** — making a new agent a *manifest plus an eval set* instead of a
  code change.

---

## Phase 1 — Close the loops that already half-exist

Each item here connects two pieces of code that are already written.

### 1.1 Closed creative loop

`script_writer` produces hook / body / CTA. `tools.py` already has `upload_ad_image` and
`upload_ad_video`. `create_ad_creative` already accepts `image_hash` and `video_id`. The
missing link is generation.

```
script_writer → media generation (Kling / Nano Banana / Higgsfield)
  → upload_ad_image | upload_ad_video → create_ad_creative → create_ad (PAUSED)
  → insights → creative_strategic scores it → agent_memory
  → next script generation reads that memory
```

Once memory carries "hook cluster X scored 8/10 at $0.42 CPC in this niche", the creative
agent stops guessing. This is the single highest-value item on the list because it turns a
set of agents into a system that learns.

### 1.2 Offline eval harness on mock mode

`META_MOCK=1` already returns fixtures for the whole Meta surface. That makes a golden-set
eval harness nearly free:

- fixture accounts covering the cases that matter — spender with zero conversions, a real
  winner, a low-volume false positive, a learning-limited ad set
- assert the **deterministic** layer exactly (verdict, reason, budget step)
- assert the **LLM** layer on structure and direction, not wording
- run it in CI on every prompt or threshold change

This is the prerequisite for everything in Phase 2. No eval harness, no safe prompt
evolution.

### 1.3 Scheduled optimize tick

APScheduler is already wired in `main.py` for memory maintenance. Extend it (or drive it
from n8n) to a daily per-cabinet run:

```
cron → /agent/campaign/optimize → proposals queue → notify (UI / Telegram)
     → human approves → apply → write the outcome to agent_memory
```

The approval gate stays exactly where it is. What changes is that the operator stops having
to remember to look.

### 1.4 Attribution bridge

Meta-reported conversions are not truth. Ingest CRM or store data (webhook or Sheets), join
on click id, and let the optimizer judge on real CPA and real revenue. Kill and Scale
verdicts get materially better the moment the denominator is honest.

---

## Phase 2 — The custom agent base

Today the roster lives in `agents.py`: a Python dict of `{model, system}`. That is the right
call for eight agents and the wrong one for eighty. The upgrade is to make agents **data**.

### 2.1 Agent manifests

```
agents/
  registry.yaml                  # roster + which prompt version is live
  optimizer/
    manifest.yaml
    system.v4.md                 # versioned prompt, semver + changelog
    evals/golden.jsonl
```

```yaml
# agents/optimizer/manifest.yaml
id: optimizer
name: Optimizer
tier: sonnet                     # a tier, never a hardcoded slug
prompt: system.v4.md

memory:
  scope: [long]
  reads: own                     # own | account  (orchestrator only)
  cabinet_scoped: true           # narrows to ad_account_id + workspace rows

tools:                           # allowlist — the agent cannot reach anything else
  - get_insights
  - list_campaigns
  - update_budget
  - update_status

output_contract: schemas/optimize_proposal.json
approval: required               # any tool with side effects
evals:
  set: evals/golden.jsonl
  min_pass_rate: 0.9
```

What this buys:

- **Versioned prompts with rollback.** Today a prompt change is a code deploy with no
  changelog. With manifests it is a version bump gated by `min_pass_rate`.
- **Tool allowlists per agent.** `creative_strategic` should never be able to call
  `update_budget`. Right now that is true because no agent calls tools directly; the moment
  a tool loop exists (2.2), it has to be enforced declaratively.
- **A new agent stops being a code change.** Manifest, prompt file, golden set, reload.
- **The roster becomes introspectable.** `GET /agent/registry` returns capabilities,
  tools, memory scope, and live prompt version — which is also what a UI needs to render an
  agent picker honestly.

### 2.2 Hermes Agent — a real tool-calling loop

Agents currently receive context that the router assembled for them. The next step is
letting them ask:

```
user goal
  → agent plans
  → calls a tool from its manifest allowlist
  → reads the result
  → iterates (bounded: max steps, max spend-impacting calls = 0 without approval)
  → returns a result + a full trace
```

Non-negotiables carried over from the current design:

- **Side-effecting tools are intercepted, never executed inline.** A write attempt produces
  a proposal and suspends the run. The existing `dry_run` default and `apply` contract
  already model this.
- **Bounded loops.** Step cap, token budget, wall-clock cap per run.
- **Full trace persisted** under a `run_id`, so any verdict can be reconstructed later.

### 2.3 Agent-to-agent handoff

The orchestrator synthesises from shared memory today. A handoff protocol makes the chain
explicit and traceable:

```
objective_interpreter → campaign_architect → ad_setting (dry-run plan)
                                          ↘ creative_strategic (pre-flight critique)
```

One `run_id` across the chain, each hop writing its rationale to memory. When a client asks
"why this budget", the answer is retrievable rather than reconstructed.

### 2.4 Eval-gated self-improvement

The loop described in `Hermes/FUTURE_WORK.md`, now with somewhere to stand:

```
production failures → cluster root causes → propose prompt edit (vN+1)
  → run golden set → HUMAN APPROVAL → promote → changelog → rollback available
```

The hard rule stays: **prompt changes never auto-deploy to accounts moving real budget.**

---

## Phase 3 — Platform bets

### 3.1 Hermes as a first-class MCP server

`meta_mcp.py` is a stdio bridge for a single local client. Serving MCP over streamable HTTP
from Hermes itself — same `X-Internal-Token` auth, same workspace scoping — turns the whole
agent roster into a tool surface any MCP client can use. The custom agent base (2.1) is what
makes this safe: tool allowlists already exist per agent.

### 3.2 Channel adapters behind one contract

`optimize_contract.py` already separates the internal dry-run shape from the app-facing
proposal shape. That is the seam a second channel plugs into:

```
services/telegram_ads/  →  same {action, summary, reason, dry_run, apply} contract
services/google_ads/    →  same contract
```

The UI, the approval flow, and the memory schema stay unchanged. Only the adapter is new.

### 3.3 Per-agent observability

Cost, latency, cache-hit rate, and approval-rate per agent, per workspace. `cache_usage()`
already extracts the cache metrics from every response — they are simply not stored yet.
Without this there is no honest answer to "what does this client cost us to serve".

### 3.4 Enable memory maintenance on evidence

TTL, dedup, and per-agent caps are implemented and dormant. The activation criteria I would
set: cross a few thousand rows per workspace, or observe recall quality dropping from
near-duplicate rows crowding the top-k. Turn on dry-run first, read the report, then enable
deletion. Contradiction resolution — a newer fact superseding an older one — is the piece
still genuinely missing.

---

## What I would not add

Recording this because roadmap discipline is part of the design.

- **Autonomous spend, at any confidence level.** The approval gate is the product.
- **A second memory store.** One isolation boundary, not two.
- **More agents for coverage's sake.** Eight agents with clear contracts beat twenty with
  overlapping ones. The manifest system exists to make each new agent *earn* its place with
  an eval set — not to make adding agents easy.
