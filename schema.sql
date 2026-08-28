-- One 768-dim space for text AND images: nomic-embed-text-v1.5 and
-- nomic-embed-vision-v1.5 are trained aligned, so a text query retrieves
-- images with no captioning step and a single index type.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
  id BIGSERIAL PRIMARY KEY,
  subject TEXT,                      -- NULL = shared with everyone
  content TEXT NOT NULL,
  source TEXT,
  tags TEXT[] DEFAULT '{}',
  embedding vector(768),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memories_vec  ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS memories_subj ON memories (subject);

CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  subject TEXT, uri TEXT, title TEXT,
  chunk_index INT DEFAULT 0,
  content TEXT NOT NULL,
  embedding vector(768),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_vec  ON documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS documents_subj ON documents (subject);

CREATE TABLE IF NOT EXISTS files (
  id BIGSERIAL PRIMARY KEY,
  subject TEXT, bucket TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL, content_type TEXT, bytes BIGINT,
  prompt TEXT, caption TEXT,
  embedding vector(768),             -- same space as text
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS files_vec  ON files USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS files_subj ON files (subject);
