-- AGNT SCALE — agent memory (Phase 1). Per-account / per-agent / orchestrator-aggregate.
-- account_id = workspace (isolation boundary). ad_account_id = optional Meta cabinet (workspace has many).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS agent_memory (
  id            BIGSERIAL PRIMARY KEY,
  account_id    TEXT NOT NULL,
  ad_account_id TEXT,
  agent_id      TEXT NOT NULL,
  scope         TEXT NOT NULL CHECK (scope IN ('short','long')),
  kind          TEXT NOT NULL,
  content       TEXT NOT NULL,
  embedding     VECTOR(1536),
  meta          JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_am_acct_agent_scope ON agent_memory (account_id, agent_id, scope);
CREATE INDEX IF NOT EXISTS idx_am_acct_adacct      ON agent_memory (account_id, ad_account_id);
CREATE INDEX IF NOT EXISTS idx_am_acct_created     ON agent_memory (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_am_embed            ON agent_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Isolation note (enforced in the memory service in the next step):
--   * every query filters account_id (per-workspace isolation)
--   * an agent reads/writes only its own agent_id within its account
--   * orchestrator (agent_id='orchestrator') may READ all agent_id within its own account_id
-- RLS policies will be added with the memory service (it SETs app.account_id / app.agent_id per session).
