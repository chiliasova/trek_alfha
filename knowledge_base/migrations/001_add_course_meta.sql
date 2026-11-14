ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS stage TEXT;  -- идея / запуск / работаю / рост
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS level TEXT;  -- новичок / опытный
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS sphere TEXT; -- узкая сфера, если есть

CREATE INDEX IF NOT EXISTS idx_kb_documents_stage  ON kb_documents (stage);
CREATE INDEX IF NOT EXISTS idx_kb_documents_level  ON kb_documents (level);
CREATE INDEX IF NOT EXISTS idx_kb_documents_sphere ON kb_documents (sphere);