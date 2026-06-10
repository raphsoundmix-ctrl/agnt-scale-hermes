-- AGNT SCALE — per-cabinet memory reads (#12 sprint 2).
-- ad_account_id column exists since 001; add composite index for cabinet-scoped recall.

CREATE INDEX IF NOT EXISTS idx_am_acct_adacct_agent_created
  ON agent_memory (account_id, ad_account_id, agent_id, created_at DESC);
