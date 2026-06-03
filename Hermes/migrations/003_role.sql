-- Non-superuser role the memory service runs as (RLS applies only to non-superusers).
-- Hermes connects as superuser 'agnt' (its own runtime DB), then per memory request does:
--   SET ROLE mem_app;  SET LOCAL app.account_id=...; SET LOCAL app.agent_id=...;  <query>  RESET ROLE;
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='mem_app') THEN
    CREATE ROLE mem_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO mem_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_memory TO mem_app;
GRANT USAGE, SELECT ON SEQUENCE agent_memory_id_seq TO mem_app;
