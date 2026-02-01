# tool-time

> See `AGENTS.md` for full development guide.

## Overview
Claude Code plugin that analyzes tool usage patterns and suggests workflow improvements.

## Status
v0.3 (Ecosystem Observatory) — published to interagency-marketplace
- Dashboard live at https://tool-time.org
- Worker at https://tool-time-api.mistakeknot.workers.dev

## Quick Commands
```bash
# Test
uv run --with pytest pytest test_summarize.py -v
uv run --with pytest pytest test_upload.py -v

# Refresh stats manually
python3 summarize.py

# Upload to community API
python3 upload.py

# Deploy worker
cd community && npm run deploy

# Parse historical transcripts
python3 backfill.py
```

## Design Decisions (Do Not Re-Ask)
- Agent analyzes data, not hardcoded heuristics
- summarize.py is pure data preparation — no opinions or thresholds
- Session-scoped edit-without-read detection (resets per session ID)
- Post-parse project filtering (correctness over string matching)
- Use `uv` for running tests, not pip
- No CLI flags (always uses CWD project, 7-day window)
- Separate tables for ecosystem data (skill_stats, mcp_server_stats, plugin_usage_aggregate)
- Plugin usage is aggregate-only (no per-submission linkage) for privacy
- Admin endpoints use bearer token via Cloudflare Worker secret, not in source
