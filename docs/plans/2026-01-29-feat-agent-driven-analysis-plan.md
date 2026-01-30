---
title: "feat: Agent-driven analysis replacing hardcoded heuristics"
type: feat
date: 2026-01-29
brainstorm: docs/brainstorms/2026-01-29-agent-driven-analysis-brainstorm.md
deepened: 2026-01-29
reviewed: 2026-01-29
---

# Agent-Driven Analysis

Replace `optimize.py`'s 4 hardcoded heuristics with agent-driven reasoning. Code prepares data (`summarize.py`); the agent analyzes it.

## Proposed Solution

`summarize.py` crunches `events.jsonl` into a compact `stats.json` (tool counts, errors, rejections). The `/tool-time` skill tells the agent to run it, read the output, and reason about what's wrong. The agent can then offer to fix CLAUDE.md/AGENTS.md directly.

## Acceptance Criteria

- [x] `summarize.py` reads `events.jsonl`, writes `stats.json` to `~/.claude/tool-time/`
- [x] `stats.json` includes: generated timestamp, total events, per-tool counts/errors/rejections, edit-without-read count (session-scoped)
- [x] `stats.json` does NOT include opinions, suggestions, or thresholds
- [x] SessionEnd hook calls `summarize.py` silently (replaces `optimize.py`)
- [x] `/tool-time` skill updated: agent reasons about stats + docs, offers to apply fixes
- [x] `optimize.py` deleted
- [x] `pending-suggestions.json` no longer produced
- [x] Project filtering uses post-parse comparison (not string matching)
- [x] Edit-without-read detection is session-scoped
- [x] `test_summarize.py` covers `load_events()` and `compute_tool_statistics()`

## Implementation

### Phase 1: `summarize.py`

Create `summarize.py` by extracting stats computation from `optimize.py`.

**Keep from optimize.py:**
- `load_events()` (lines 37-52) — refactor to filter by project after JSON parse
- `analyze()` (lines 65-135) — simplify to just tool counts + session-scoped edit-without-read
- `is_user_rejection()` (lines 30-34)

**Delete from optimize.py:**
- `generate_suggestions()` (lines 138-186) — all heuristics
- `print_summary()` (lines 189-207)
- `pending-suggestions.json` writing (lines 221-224)
- `parse_mcp_server()` (lines 55-62) — unnecessary aggregation

**`stats.json` schema:**

```json
{
  "generated": "2026-01-29T18:30:00Z",
  "total_events": 12500,
  "tools": {
    "Bash": {"calls": 3000, "errors": 45, "rejections": 12},
    "Read": {"calls": 2800, "errors": 2, "rejections": 0},
    "Edit": {"calls": 1500, "errors": 30, "rejections": 5}
  },
  "edit_without_read_count": 8
}
```

**Key implementation details:**
- Filter by project AFTER `json.loads()`, not via string matching (correctness over micro-optimization)
- Edit-without-read resets `files_read` set per session ID, not globally
- Use `read_text().splitlines()` — 0.69s on 207k events is fine, memory irrelevant on server
- No CLI flags — always uses CWD project, 7-day window
- No dataclasses — plain dicts for JSON-in/JSON-out code

**Files:**
- Create: `summarize.py`
- Create: `test_summarize.py`
- Delete: `optimize.py` (after tests pass)

### Phase 2: Update skill + hook

**Update `skills/tool-time/SKILL.md`:**

```markdown
## Steps

1. Run `python3 $CLAUDE_PLUGIN_ROOT/summarize.py` to refresh stats
2. Read `~/.claude/tool-time/stats.json`
3. Analyze the data and explain what you see
4. If relevant, check the project's CLAUDE.md and AGENTS.md for gaps
5. Offer to apply fixes with user approval
```

No leading questions. Trust the agent.

**Update `skills/tool-time-codex/SKILL.md`:**
Same, but run `backfill.py` first (Codex has no hooks).

**Update `hooks/hook.sh` (line 80):**
`python3 "$PLUGIN_ROOT/optimize.py"` → `python3 "$PLUGIN_ROOT/summarize.py"`

**Files:**
- Edit: `skills/tool-time/SKILL.md`
- Edit: `skills/tool-time-codex/SKILL.md`
- Edit: `hooks/hook.sh`

### Phase 3: Cleanup

- Delete `optimize.py`
- Delete references to `pending-suggestions.json`
- Update `docs/ROADMAP.md`

## Deferred

- `--project`, `--global`, `--days` flags (YAGNI)
- Trends / previous period comparison
- Installed skills scanner
- MCP resource (`tool-time://stats`)
- `diagnostics.json` structured output
- Hook error logging (stale timestamp is sufficient signal)
- Incremental counters (stateless recomputation is simpler)

## References

- Brainstorm: `docs/brainstorms/2026-01-29-agent-driven-analysis-brainstorm.md`
- Current: `optimize.py:65-135` (analyze), `optimize.py:138-186` (heuristics to delete)
- Hook: `hooks/hook.sh:78-81`
