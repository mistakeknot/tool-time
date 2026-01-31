# tool-time

> See `AGENTS.md` for full development guide.

## Overview
Claude Code plugin that analyzes tool usage patterns and suggests workflow improvements.

## Status
v0.2 (Agent-Driven Analysis) — published to interagency-marketplace

## Quick Commands
```bash
# Test
uv run --with pytest pytest test_summarize.py -v

# Refresh stats manually
python3 summarize.py

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
