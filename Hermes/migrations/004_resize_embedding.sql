-- AGNT SCALE — resize embedding to local model dim (bge-small-en-v1.5 = 384).
-- Existing rows have NULL embeddings (none written yet), so dropping the column is lossless.
DROP INDEX IF EXISTS idx_am_embed;
ALTER TABLE agent_memory DROP COLUMN IF EXISTS embedding;
ALTER TABLE agent_memory ADD COLUMN embedding vector(384);
CREATE INDEX idx_am_embed ON agent_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
