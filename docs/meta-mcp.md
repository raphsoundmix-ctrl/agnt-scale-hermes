# AGNT Meta Ads — custom MCP server

`meta_mcp.py` is a thin **MCP** bridge that exposes the Hermes Meta endpoints as MCP
tools for Claude Code / any MCP client. Heavy logic + memory + tokens stay on the Hermes
server; this is a stdio bridge.

## Security / transport

| Property | Value |
|----------|-------|
| **Transport** | **stdio** (`mcp.run()`) — no listening TCP port on the MCP process |
| **Upstream** | HTTP client → Hermes `HERMES_URL` (default `http://localhost:7778`) |
| **Auth to Hermes** | `X-Internal-Token: $HERMES_INTERNAL_TOKEN` on every POST |
| **Workspace scope** | `META_WORKSPACE` injected as `account_id` on all calls |
| **Consumers** | External MCP clients (Claude Code / Cursor MCP config) — **not** wired into Hermes orchestrator |
| **Port exposure** | MCP itself exposes **no port**. Hermes listens on AGNT **7778** (see `ISOLATION.md`); Funnel :8443 is token-gated |

## Tools
| Tool | Hermes endpoint | Does |
|------|-----------------|------|
| `meta_read` | `POST /agent/meta` | accounts / insights / campaigns / adsets / ads / pixels / interests |
| `campaign_plan` | `POST /agent/campaign/plan` | goal → blueprint + DRY-RUN plan |
| `campaign_execute` | `POST /agent/campaign/execute` | approved blueprint → campaign + ad sets + optional ads/creatives (PAUSED) |
| `campaign_optimize` | `POST /agent/campaign/optimize` | insights → Kill/Hold/Scale → DRY-RUN proposals |
| `meta_learn` | `POST /agent/meta/learn` | API version check + platform learnings |

## Use in Claude Code

```bash
pip install "mcp[cli]" httpx
```

Add to `.mcp.json` (or `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agnt-meta-ads": {
      "command": "python",
      "args": ["C:/Users/raphs/agnt-scale-hermes/meta_mcp.py"],
      "env": {
        "HERMES_URL": "https://ai-agents-by-raph.tail3c773d.ts.net:8443",
        "HERMES_INTERNAL_TOKEN": "<the X-Internal-Token from ~/Container2/Hermes/.env>",
        "META_WORKSPACE": "mcp"
      }
    }
  }
}
```

Writes stay dry-run + approval-gated; live create needs a Meta token with `ads_management`.
