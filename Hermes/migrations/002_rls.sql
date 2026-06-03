-- AGNT SCALE — RLS for agent_memory.
-- Memory service sets per request:  SET app.account_id=<workspace>;  SET app.agent_id=<agent>;
-- Isolation: per-account always; agent sees only its own agent_id; orchestrator READS all agents in its account but WRITES only its own.

ALTER TABLE agent_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_memory FORCE ROW LEVEL SECURITY;  -- owner (hermes connects as 'agnt') is also subject

DROP POLICY IF EXISTS mem_read ON agent_memory;
CREATE POLICY mem_read ON agent_memory FOR SELECT USING (
  account_id = current_setting('app.account_id', true)
  AND (
    current_setting('app.agent_id', true) = 'orchestrator'
    OR agent_id = current_setting('app.agent_id', true)
  )
);

DROP POLICY IF EXISTS mem_insert ON agent_memory;
CREATE POLICY mem_insert ON agent_memory FOR INSERT WITH CHECK (
  account_id = current_setting('app.account_id', true)
  AND agent_id = current_setting('app.agent_id', true)
);

DROP POLICY IF EXISTS mem_update ON agent_memory;
CREATE POLICY mem_update ON agent_memory FOR UPDATE
  USING (account_id = current_setting('app.account_id', true) AND agent_id = current_setting('app.agent_id', true))
  WITH CHECK (account_id = current_setting('app.account_id', true) AND agent_id = current_setting('app.agent_id', true));

DROP POLICY IF EXISTS mem_delete ON agent_memory;
CREATE POLICY mem_delete ON agent_memory FOR DELETE
  USING (account_id = current_setting('app.account_id', true) AND agent_id = current_setting('app.agent_id', true));
