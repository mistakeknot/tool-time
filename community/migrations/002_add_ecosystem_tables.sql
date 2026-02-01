-- Ecosystem observatory tables: skills, MCP servers, plugins

CREATE TABLE skill_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) > 0 AND length(name) <= 100),
  calls INTEGER NOT NULL DEFAULT 0,
  UNIQUE(submission_id, name)
);

CREATE TABLE mcp_server_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) > 0 AND length(name) <= 100),
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  UNIQUE(submission_id, name)
);

-- Plugin aggregate (no per-submission linkage — privacy protection against fingerprinting)
CREATE TABLE plugin_usage_aggregate (
  plugin_name TEXT PRIMARY KEY CHECK (length(plugin_name) > 0 AND length(plugin_name) <= 100),
  install_count INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT
);

CREATE INDEX idx_skill_stats_submission ON skill_stats(submission_id);
CREATE INDEX idx_mcp_server_stats_submission ON mcp_server_stats(submission_id);
