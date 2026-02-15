# tool-time Roadmap

**Version:** 0.3.1
**Last updated:** 2026-02-14

## v0.1 — Prove Value

Passive event collection + suggest-only analysis. No MCP, no CLI, no config.

- [x] Shell hook logging (PreToolUse, PostToolUse, SessionStart, SessionEnd)
- [x] JSONL event store (`~/.claude/tool-time/events.jsonl`)
- [x] Session-end Python analyzer (7-day window)
- [x] Pattern detection: Edit-without-Read (session-scoped), error rates
- [x] User rejection filtering (separate from real tool errors)

## v0.1.1 — Historical Backfill

- [x] Claude Code transcript parser (`~/.claude/projects/<name>/<id>.jsonl`)
- [x] Codex CLI transcript parser (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`)
- [x] `backfill.py` — parse all historical transcripts, emit unified events
- [x] File path capture in both parsers

## v0.2 — Agent-Driven Analysis (current)

Replace hardcoded heuristics with agent reasoning. Code prepares data; agent analyzes.

- [x] `summarize.py` — compute tool stats, write `stats.json`
- [x] Session-scoped edit-without-read detection
- [x] Post-parse project filtering (correctness over string matching)
- [x] `/tool-time` skill — agent reads stats + CLAUDE.md/AGENTS.md, reasons about gaps, offers fixes
- [x] Codex skill variant (runs backfill first)
- [x] SessionEnd hook calls `summarize.py` (replaces `optimize.py`)
- [x] `optimize.py` and `pending-suggestions.json` removed
- [x] `test_summarize.py` with 18 tests
- [x] Marketplace listing + plugin skills published

## v0.3 — Deferred

Add when proven useful:

- [ ] `--project`, `--global`, `--days` CLI flags
- [ ] Trends / previous period comparison
- [ ] Installed skills scanner (compare available vs. used)
- [ ] MCP resource (`tool-time://stats`)
- [ ] Incremental transcript parsing
- [ ] Per-project storage
- [ ] Retention policy

## Deferred Indefinitely

| Feature | Condition to Reconsider |
|---------|------------------------|
| ML-based pattern mining | Months of data + clear patterns agent reasoning misses |
| Cross-project insights | Multiple projects with enough data to compare |
| Auto-apply CLAUDE.md changes | Agent-driven suggestions prove consistently good |
| Tool output logging | Only if users explicitly opt in (privacy default: off) |
