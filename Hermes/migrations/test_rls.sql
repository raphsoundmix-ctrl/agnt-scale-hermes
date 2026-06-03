-- Isolation test for agent_memory RLS. Run against agnt-postgres.
\set ON_ERROR_STOP off

-- ── seed (3 rows: acctA/creative, acctA/ad_setting, acctB/creative) ──
SET app.account_id='acctA'; SET app.agent_id='creative_strategic';
INSERT INTO agent_memory(account_id,ad_account_id,agent_id,scope,kind,content) VALUES('acctA','act_1','creative_strategic','long','fact','A-creative-1');
SET app.agent_id='ad_setting';
INSERT INTO agent_memory(account_id,ad_account_id,agent_id,scope,kind,content) VALUES('acctA','act_1','ad_setting','long','fact','A-adsetting-1');
SET app.account_id='acctB'; SET app.agent_id='creative_strategic';
INSERT INTO agent_memory(account_id,ad_account_id,agent_id,scope,kind,content) VALUES('acctB','act_9','creative_strategic','long','fact','B-creative-1');

-- ── T1: acctA/creative sees ONLY its own (expect 1) ──
SET app.account_id='acctA'; SET app.agent_id='creative_strategic';
SELECT 'T1 acctA/creative' AS test, count(*) AS got, 1 AS expect FROM agent_memory;

-- ── T2: acctA/orchestrator sees ALL acctA agents (expect 2), NOT acctB ──
SET app.agent_id='orchestrator';
SELECT 'T2 acctA/orchestrator' AS test, count(*) AS got, 2 AS expect FROM agent_memory;

-- ── T3: acctB/creative sees ONLY acctB (expect 1) — cross-account blocked ──
SET app.account_id='acctB'; SET app.agent_id='creative_strategic';
SELECT 'T3 acctB/creative' AS test, count(*) AS got, 1 AS expect FROM agent_memory;

-- ── T4: orchestrator CANNOT write another agent's row (expect: ERROR) ──
SET app.account_id='acctA'; SET app.agent_id='orchestrator';
INSERT INTO agent_memory(account_id,agent_id,scope,kind,content) VALUES('acctA','ad_setting','long','fact','orch-illegal-write');
SELECT 'T4 expected an RLS error above (orchestrator writing ad_setting row)' AS note;

-- ── cleanup seed ──
RESET app.account_id; RESET app.agent_id;
TRUNCATE agent_memory;
SELECT 'cleanup done, rows now:' AS note, count(*) FROM agent_memory;
