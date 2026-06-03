# 🔒 AGNT SCALE — Server Isolation Contract

The server (`raph_97_ai@ai-agents-by-raph.tail3c773d.ts.net`) hosts **multiple projects**.
AGNT SCALE is an **isolated space**. Binding for anyone (human or AI) working on AGNT.

## Rules
1. **Touch ONLY AGNT resources.** Never modify another project's containers, files,
   docker-compose, networks, env, or Tailscale Funnel/serve config.
2. **AGNT keeps its OWN dedicated, reserved ports** (table below). Never reuse a host
   port another project published. New AGNT services pick a free, AGNT-reserved port.
3. **Everything AGNT lives under** `~/Container2` (stack) + `~/agnt-scale-hermes-git`
   (git clone) + compose project **`agnt-scale`** + network **`agnt-scale_agnt-internal`**.
4. **Data services stay internal-only** (NOT host-published) — namespaced by the AGNT
   network, so they never collide with another project's same-numbered ports.
5. Before adding anything: verify the port / name / path is free AND AGNT-namespaced.

## AGNT reserved (others must not take these)
| Purpose           | Port / name                                | Exposure             |
|-------------------|--------------------------------------------|----------------------|
| Hermes gateway    | host **7778** → container 7777             | host-published       |
| Hermes public     | Tailscale Funnel **:8443** → 127.0.0.1:7778 | public (token-gated) |
| Postgres (memory) | 5432 (container only)                      | internal-only        |
| Qdrant            | 6333 / 6334 (container only)               | internal-only        |
| Redis             | 6379 (container only)                      | internal-only        |
| Compose project   | `agnt-scale`                               | —                    |
| Network           | `agnt-scale_agnt-internal`                 | —                    |
| Containers        | `agnt-hermes` · `agnt-postgres` · `agnt-qdrant` · `agnt-redis` | — |
| Paths             | `~/Container2` · `~/agnt-scale-hermes-git` | —                    |

## OTHER projects on this server — NEVER TOUCH
- **MAO** — containers `mao-*` + `traefik`; paths `/srv/apps/mao`, `/srv/infra/traefik`;
  networks `backend_mao-internal`, `proxy`, `traefik_socket-proxy`;
  Tailscale Funnel `:443` → localhost:8000;
  host ports **7777, 8000, 5432, 6333, 6379, 9000, 9001** (these are MAO's — AGNT avoids them on the host).

> AGNT's postgres/qdrant/redis intentionally are NOT host-published — they answer only
> inside `agnt-scale_agnt-internal`, so the shared numbers (5432/6333/6379) never clash
> with MAO's host-published ones. Keep it that way.
