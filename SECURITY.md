# Security model

Not a compliance checklist — the actual trust boundaries of this runtime, what an attacker
gets at each one, and the code that closes it. `ISOLATION.md` covers the shared-host
contract; this file covers the runtime itself.

## Trust boundaries

| Boundary | Threat | Mitigation | Enforced in |
|---|---|---|---|
| **Public ingress** (Tailscale Funnel :8443) | Anyone on the internet reaching the agent API | Every non-health route requires `X-Internal-Token`, compared constant-time (`secrets.compare_digest`). In production an **empty** configured token refuses all traffic (503) instead of failing open. `/docs` and `/openapi.json` are disabled in production — a public Funnel must not leak the API schema. | `Hermes/main.py` |
| **Tenant boundary** | One workspace reading another's agent memory | Postgres RLS with `FORCE ROW LEVEL SECURITY` under a non-superuser role (`mem_app`); `SET LOCAL` scoping per transaction, so a leaked connection reverts on commit. `account_id` fails closed — no request without a workspace, no silent `_global` fallback. | `migrations/002_rls.sql`, `003_role.sql`, `services/agnt_memory.py`, `routers/agnt_agent.py` |
| **Ad-spend boundary** | A model output, bug, or prompt injection spending money | Writes are dry-run **at the tool layer** — with default arguments no write coroutine can reach the network; the live path requires `approve=true` on the endpoint after a human reviewed the plan. Everything is created `PAUSED`; activation is a separate human act. Budget changes are bounded (+20% step). | `services/meta/tools.py`, `services/meta/client.py`, `routers/agnt_agent.py`, tested in `Hermes/tests/test_meta_tools.py` |
| **Credentials** | Server compromise leaking long-lived ad-account access | Hermes stores **no** Meta OAuth tokens. The app DB holds them encrypted and passes one decrypted `meta_token` per request; the brain is stateless on credentials. | `docs/meta-oauth-hermes-bridge.md`, `routers/agnt_agent.py` |
| **Data egress** | Client memory text leaving the server | Embeddings are computed locally (`fastembed`, CPU) — memory content is never sent to an embedding API. LLM calls carry only the context assembled for the specific request. | `services/embeddings.py` |
| **Prompt injection** | Untrusted text (ad copy, insights, memory) steering an agent into an action | LLM output is parsed into structured JSON and can only ever produce *proposals*; there is no code path from model text to a live write. Applying a proposal is a separate, human-triggered request with its own validation. | `routers/agnt_agent.py`, `services/meta/optimize_contract.py` |
| **Shared host** | Cross-project blast radius | Dedicated compose project, network, ports, and paths; data services are **not** host-published, so they only answer inside the AGNT network. Container memory limits and `cpu_shares` prevent resource starvation of the neighbour project. | `ISOLATION.md`, `docker-compose.yml` |

## Non-goals

- Hermes does not implement its own user auth — identity lives in the app layer; Hermes
  trusts the proxy exactly as far as the shared token.
- The MCP bridge (`meta_mcp.py`) is stdio-only and opens no port; it authenticates to
  Hermes the same way the app does.

## Reporting

Found something? Message me on
[LinkedIn](https://www.linkedin.com/in/raphael-kuldashev/?locale=en) — I would genuinely
like to know.
