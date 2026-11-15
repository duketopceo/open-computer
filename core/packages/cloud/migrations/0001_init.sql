-- Open Computer cloud core: first schema. Applied by `wrangler d1 migrations apply` (never run by CI against a real database).

-- One account-wide audit chain. The unique sequence number is a backstop; LedgerDO serializes the appends.
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  task_id TEXT,
  kind TEXT NOT NULL,
  data TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);

-- Spend: one row per model step, keyed by step id so a retried step replaces its own row.
CREATE TABLE IF NOT EXISTS spend (
  step_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  day TEXT NOT NULL,
  amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_task ON spend (task_id);
CREATE INDEX IF NOT EXISTS spend_day ON spend (day);

CREATE TABLE IF NOT EXISTS task_cap_extra (
  task_id TEXT PRIMARY KEY,
  amount REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Memory tier 1: the two always-loaded blocks.
CREATE TABLE IF NOT EXISTS memory_blocks (
  name TEXT PRIMARY KEY,
  body TEXT NOT NULL
);

-- Memory tier 2: one summary per finished task, with a full-text index for keyword recall.
CREATE TABLE IF NOT EXISTS memory_history (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_history_fts USING fts5(id UNINDEXED, title, summary);
