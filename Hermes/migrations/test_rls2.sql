-- Isolation test under role mem_app (RLS enforced). Run against agnt-postgres.
\set ON_ERROR_STOP off

-- seed as superuser agnt (bypasses RLS — fixture only)
INSERT INTO agent_memory(account_id,ad_account_id,agent_id,scope,kind,content) VALUES
 ('acctA','act_1','creative_strategic','long','fact','A-creative-1'),
 ('acctA','act_1','ad_setting','long','fact','A-adsetting-1'),
 ('acctB','act_9','creative_strategic','long','fact','B-creative-1');

-- drop privileges to mem_app → RLS now applies
SET ROLE mem_app;

-- T1: acctA/creative sees ONLY own (expect 1)
SET app.account_id='acctA'; SET app.agent_id='creative_strategic';
SELECT 'T1 acctA/creative' AS test, count(*) AS got, 1 AS expect FROM agent_memory;

-- T2: acctA/orchestrator sees ALL acctA agents (expect 2), NOT acctB
SET app.agent_id='orchestrator';
SELECT 'T2 acctA/orchestrator' AS test, count(*) AS got, 2 AS expect FROM agent_memory;

-- T3: acctB/creative sees ONLY acctB (expect 1) — cross-account blocked
SET app.account_id='acctB'; SET app.agent_id='creative_strategic';
SELECT 'T3 acctB/creative' AS test, count(*) AS got, 1 AS expect FROM agent_memory;

-- T4: orchestrator CANNOT write another agent's row (expect: RLS ERROR)
SET app.account_id='acctA'; SET app.agent_id='orchestrator';
INSERT INTO agent_memory(account_id,agent_id,scope,kind,content) VALUES('acctA','ad_setting','long','fact','orch-illegal');
SELECT 'T4: an RLS error should appear directly above this line' AS note;

-- cleanup
RESET ROLE; RESET app.account_id; RESET app.agent_id;
TRUNCATE agent_memory;
SELECT 'cleanup rows:' AS note, count(*) FROM agent_memory;
