-- Replace inflatable aggregate counter with per-token dedup table
CREATE TABLE plugin_installs (
  plugin_name TEXT NOT NULL,
  submission_token TEXT NOT NULL,
  last_seen TEXT,
  UNIQUE(plugin_name, submission_token)
);
CREATE INDEX idx_plugin_installs_name ON plugin_installs(plugin_name);

-- Migrate existing data (best-effort: each plugin gets 1 install)
INSERT OR IGNORE INTO plugin_installs (plugin_name, submission_token, last_seen)
  SELECT plugin_name, 'legacy', last_seen FROM plugin_usage_aggregate;

DROP TABLE plugin_usage_aggregate;
