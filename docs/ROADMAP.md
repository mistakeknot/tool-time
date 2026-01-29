# tool-time Roadmap

## v0.1 — Prove Value (current)

Passive event collection + suggest-only analysis. No MCP, no CLI, no config.

- [x] Shell hook logging (PreToolUse, PostToolUse, SessionStart, SessionEnd)
- [x] JSONL event store (`~/.claude/tool-time/events.jsonl`)
- [x] Session-end Python analyzer (7-day window)
- [x] Pattern detection: Edit-without-Read (file-level), error rates, Bash overuse
- [x] User rejection filtering (separate from real tool errors)
- [x] Suggestions written to `pending-suggestions.json` (not auto-applied)

**Success criteria:** Suggestions are actually useful (validated via v0.1.1 backfill).

## v0.1.1 — Historical Backfill (next)

Bootstrap analytics from existing session transcripts instead of waiting for hook data.

- [x] Claude Code transcript parser (`~/.claude/projects/<name>/<id>.jsonl`)
- [x] Codex CLI transcript parser (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`)
- [x] `backfill.py` — parse all historical transcripts from both tools, emit unified events to `events.jsonl`
- [x] File path capture in both parsers (enables file-level pattern detection)
- [ ] Run `optimize.py` against backfilled data to validate suggestions immediately
- [ ] One-time script, not a persistent service

**Why now:** Weeks/months of session transcripts already exist on disk for both tools. No need to wait 2+ weeks for hooks to accumulate data — backfill and validate today.

## v0.2 — Ongoing Transcript Parsing

Make transcript parsing the primary analytics source (not just one-time backfill).

- [ ] Incremental parsing — track last-parsed position, only process new transcripts
- [ ] `optimize.py` runs against transcripts instead of (or in addition to) hook events
- [ ] Richer data from transcripts: full tool inputs/outputs vs hook summaries

**Architecture shift:** Hooks remain for real-time intervention (PreToolUse blocking). Transcript parsing becomes primary for analytics — it's cross-tool and requires no integration from the host tool.

**Risk:** Transcript formats are undocumented internals. Parsers may break on updates.

## v0.3 — Reports & Per-Project

- [ ] Go or Python CLI: `tool-time report`, `tool-time optimize`
- [ ] Per-project storage (`.claude/tool-time/`)
- [ ] Aggregation layer (daily rollups from JSONL)
- [ ] Retention policy (default 30 days)
- [ ] More patterns: unused skills, token trends, recurring error sequences

## v0.4 — In-Session Queries

Add after proving what questions people actually ask about their tool usage.

- [ ] MCP server (stdio, Python) with `tool_time_get_stats`, `tool_time_get_insights`
- [ ] `/tool-time` slash command + skill
- [ ] MCP resources: `tool-time://stats/today`, `tool-time://stats/project/<path>`

## v0.5 — Auto-Apply

Add after suggest-only proves the suggestions are consistently good.

- [ ] Tiered auto-apply: safe CLAUDE.md hints auto-appended, everything else suggested
- [ ] Config system (`~/.claude/tool-time/config.json`)
- [ ] Max hints cap with rotation by relevance
- [ ] Propose hookify rules for repeated mistakes
- [ ] Propose skills for recurring workflows

## v0.6 — Distribution

- [ ] Marketplace listing
- [ ] Plugin README + install docs
- [ ] Privacy controls (opt-out of specific tools, output logging toggle)

## Deferred Indefinitely

| Feature | Condition to Reconsider |
|---------|------------------------|
| ML-based pattern mining | Months of data + clear patterns that heuristics miss |
| Cross-project insights | Multiple projects with enough data to compare |
| Skill usage influence tracking | Need UserPromptSubmit hook to detect `/skill` invocations |
| Tool output logging | Only if users explicitly opt in (privacy default: off) |
