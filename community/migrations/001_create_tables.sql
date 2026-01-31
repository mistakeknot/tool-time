-- Community analytics tables
-- Schema uses edit_without_read (not _count) for consistency

CREATE TABLE submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_token TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  total_events INTEGER NOT NULL,
  edit_without_read INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  submitted_at TEXT DEFAULT (datetime('now')),
  UNIQUE(submission_token, generated_at)
);

CREATE TABLE tool_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  rejections INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_submissions_time ON submissions(submitted_at DESC);
CREATE INDEX idx_submissions_token ON submissions(submission_token);
CREATE INDEX idx_tool_stats_submission ON tool_stats(submission_id);
CREATE INDEX idx_tool_stats_name ON tool_stats(tool_name);
