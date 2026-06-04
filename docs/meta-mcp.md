# AGNT Meta Ads — custom MCP server

`meta_mcp.py` is a thin **MCP** bridge that exposes the Hermes Meta endpoints as MCP
tools for Claude Code / any MCP client. Heavy logic + memory + tokens stay on the Hermes
server; this is a stdio bridge.

## Tools
| Tool | Does |
|------|------|
| `meta_read` | accounts / insights / campaigns / adsets / ads / pixels / interests |
| `campaign_plan` | goal → blueprint + DRY-RUN plan (nothing created) |
| `campaign_execute` | approved blueprint → create campaign + ad sets (PAUSED) |
| `campaign_optimize` | insights → Kill/Hold/Scale → DRY-RUN proposals |
| `meta_learn` | API version check + recent platform learnings |

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
