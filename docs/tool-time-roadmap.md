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
- [TTM-N1] **Feature roadmap reset for token bands** — add `--project`, `--global`, and `--days` CLI slices.

## P2 — Usage and trend intelligence

- [TTM-P1] **Segmented query slices** — add `--project`, `--global`, `--days` CLI filters.
- [TTM-P2] **Trend comparison** — support previous-period comparisons for session analytics.

## P3 — Deferred

- [TTM-P3] **Installed skills usage matrix** — surface available-vs-used tool/skill mismatch.
- [TTM-P4] **MCP resource endpoint** — expose `tool-time://stats` as a runtime resource.
- [TTM-P5] **Incremental transcript parsing** — process deltas to avoid full reingest costs.
- [TTM-P6] **Per-project storage** — isolate analytics by project namespace.
- [TTM-P7] **Retention controls** — add configurable retention and summarization windows.

## P4 — Deferred indefinitely

- [TTM-P8] **ML-based pattern mining** — if months of data reveal stable high-signal classes.
- [TTM-P9] **Cross-project insights** — once data across projects supports comparative learning.
- [TTM-P10] **Auto-apply policy patches** — allow automated CLAUDE.md updates only after high-confidence suggestions.
- [TTM-P11] **Tool output logging** — enable only with explicit opt-in to preserve defaults.

## From Interverse Roadmap

Items from the [Interverse roadmap](../../../docs/roadmap.json) that involve this module:

No monorepo-level items currently reference this module.
