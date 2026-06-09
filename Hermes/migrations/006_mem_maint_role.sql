-- AGNT SCALE — least-privilege maintenance listing role (#12 follow-up).
-- mem_maint: cross-account bucket enumeration only (SELECT + BYPASSRLS).
-- Per-bucket deletes stay under mem_app + RLS in mem_maintenance.py.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mem_maint') THEN
    CREATE ROLE mem_maint NOLOGIN NOSUPERUSER BYPASSRLS;
  END IF;
END $$;

GRANT SELECT ON agent_memory TO mem_maint;
GRANT mem_maint TO agnt;
