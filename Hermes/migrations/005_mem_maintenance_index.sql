-- AGNT SCALE — maintenance query support (#12).
-- created_at already exists (001); add composite index for per-agent cap/TTL scans.
CREATE INDEX IF NOT EXISTS idx_am_acct_agent_created
  ON agent_memory (account_id, agent_id, created_at ASC);
