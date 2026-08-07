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

# Why edits fail (transcript-derived; the only source that sees failures)
python3 edit_stats.py --days 7
python3 edit_stats.py --tool Edit --ext .jsonl        # drill into an outlier
python3 edit_stats.py --auto-since-path ~/.claude/CLAUDE.md   # before/after a guidance change

# One-time repair of pre-2026-08-07 error values
python3 scripts/quarantine-legacy-errors.py --dry-run
```

## Error Measurement (read before touching anything error-related)
**The hook path cannot measure tool errors.** Claude Code does not fire
PostToolUse when a tool fails, so hooks only ever witness successes. Verified
by deliberately failing an Edit: the preceding Read logged an event, the failed
Edit logged nothing.

The old heuristic in `hooks/hook.sh` inferred failure by stringifying
`tool_response` and testing `/error|Error|ERROR/`. A successful Edit's response
embeds `originalFile` — the whole pre-edit file — so any file containing the
substring "error" was logged as a failure. Across 210,148 historical events:
20,568 non-null `error` values, **0** containing `<tool_use_error>`. That is
where the phantom "Edit error rate is 51%" session digest came from. Real rate,
measured from transcripts over 6,883 sessions / 30d: **2.3%**.

Consequences to preserve:
- `error` is always `null` on the hook path. Do not reintroduce payload sniffing.
- `errors`/`rejections` in stats.json are `int | None`. `None` means *not
  measured*, which is not `0` — never render it as a rate.
- Every error rate's denominator is `error_observed_calls`, never `calls`.
- Error truth comes from transcripts (`is_error`), via `edit_stats.py` or
  `backfill.py`-derived `event: "ToolUse"` records.

## Design Decisions (Do Not Re-Ask)
- Agent analyzes data, not hardcoded heuristics
- summarize.py is pure data preparation — no opinions or thresholds
- An unmeasured population reports None, never 0 (see Error Measurement)
- Session-scoped edit-without-read detection (resets per session ID)
- Post-parse project filtering (correctness over string matching)
- Use `uv` for running tests, not pip
- No CLI flags (always uses CWD project, 7-day window)
- Separate tables for ecosystem data (skill_stats, mcp_server_stats, plugin_usage_aggregate)
- Plugin usage is aggregate-only (no per-submission linkage) for privacy
- Admin endpoints use bearer token via Cloudflare Worker secret, not in source
