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

## The two event sources (read before touching ids or counts)
`events.jsonl` is fed by two paths that record the *same* tool calls:

| | `event` | ids | sees failures? |
|---|---|---|---|
| `hooks/hook.sh` | `PostToolUse` | `<session>-<int>` | never |
| `backfill.py` → `parsers.py` | `ToolUse` | `<session>-t<int>` | yes (`is_error`) |

Two invariants hold this together, and both fail silently if broken:

1. **The id namespaces must stay disjoint.** They were not until 2026-08-07:
   both emitted `<session>-<int>`, from counters that tick on different
   things (the hook's on five hook events, the parser's on tool calls only).
   `backfill.load_existing_ids()` therefore matched a hook id against an
   unrelated parser event and dropped it. Every transcript event for every
   hook-covered session was discarded — which is why `errors` was `None`
   everywhere. The `t` prefix is what keeps them apart.
2. **Never count both sources for one session.** `prefer_transcript_events()`
   (in both `summarize.py` and `analyze.py`) drops hook events for any
   session that has transcript events. Without it, every call in the overlap
   counts twice. The preference is per *session*, because backfill coverage
   is per session — a global rule would erase sessions backfill never reached.

Prefer `session_of(event)` over parsing the id. The `session` field is
authoritative; id surgery is a fallback for hook events only.

Backfill runs from the SessionEnd hook, backgrounded, at most once per 6h
(`--days 2`, ~5s over ~450 transcripts). A session that just ended may not be
parsed until the next run; that lag is immaterial against a 7-day window and
never double-counts.

**Backfill and rotation share `.rotate.lock`.** `rotate_events()` rewrites
events.jsonl read-filter-replace, and its docstring accepts the resulting
loss window as bounded — which was true when hook.sh was the only appender
(one line, microseconds) and false once backfill appended ~50k lines over
seconds from the same SessionEnd. Backfill now takes the lock, covering
`load_existing_ids()` as well as the writes, and declines rather than writing
into a file being rewritten. If you add a third writer, it takes the lock too.

**The digest reports when measurement stops.** Every tripwire skips a tool
whose `errors` is None, which is correct — but applied to all tools it makes
a dead backfill indistinguishable from a clean run. The measurement-down
tripwire fires on that silence (0 error-observable calls above
`UNMEASURED_MIN_CALLS`). Any digest line quoting a rate must also name its
denominator; `test_maintain.py::TestDigestRateAlwaysNamesDenominator`
enforces it.

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
