# Agent-Driven Analysis for tool-time

**Date:** 2026-01-29
**Status:** Design complete, ready for planning

## What We're Building

Replace hardcoded heuristics in `optimize.py` with agent-driven analysis. The code prepares data (summarize); the agent reasons about it (analyze). The agent can detect missing skills, ineffective CLAUDE.md rules, AGENTS.md gaps, and recurring cross-session patterns — things no fixed heuristic set can cover.

## Why This Approach

Hardcoded heuristics are brittle:
- They catch only what you anticipated (4 patterns today)
- Thresholds are arbitrary (30% error rate, 50% Bash usage)
- No context awareness (can't read CLAUDE.md to check if a suggestion already exists)
- Can't reason about skill gaps (don't know what skills are installed)

The agent already knows what good tool usage looks like, what skills do, and how CLAUDE.md should be structured. Give it data and context, let it reason.

## Key Decisions

### 1. Data access: All three layers
- **Skill + shell**: `/tool-time` skill tells agent to run `summarize.py` via Bash, read output, reason
- **MCP resource**: `tool-time://stats` exposes summary for richer integrations
- **Inline**: Skill prompt includes the output in context for tightest loop

### 2. Context scope: Full diagnostic snapshot
Summary includes:
- Tool usage stats (counts, error rates, rejection rates, per-tool breakdowns)
- Installed skills list (scan plugin directories)
- Current project's CLAUDE.md content
- Current project's AGENTS.md content
- Agent compares what's used vs. available, what's documented vs. observed

### 3. Scope: Both per-project and global
- **Default (per-project)**: Most useful in-session. Looks at tool patterns for current project, reads that project's docs
- **Global mode** (`--global`): Cross-project comparison. Available via flag for periodic review

### 4. Migration: Clean break
- Delete `generate_suggestions()` and all heuristics from `optimize.py`
- Rename/refactor to `summarize.py` — pure data preparation, no opinions
- Remove `pending-suggestions.json` as an output (agent produces diagnostics instead)

### 5. Output: Structured JSON + conversation
- Agent writes findings to `~/.claude/tool-time/diagnostics.json`
- Agent also explains findings conversationally
- Other tools can consume the JSON (hookify, interdoc, etc.)

## Architecture

```
events.jsonl ──→ summarize.py ──→ stats.json (compact summary)
                                       │
                    ┌──────────────────┤
                    ▼                  ▼
              MCP resource        /tool-time skill
           (tool-time://stats)    (runs via Bash)
                    │                  │
                    └──────┬───────────┘
                           ▼
                    Agent reasoning
                    (reads stats.json + CLAUDE.md + AGENTS.md + installed skills)
                           │
                           ▼
                    diagnostics.json + conversational output
```

### summarize.py output shape (stats.json)

```json
{
  "generated": "2026-01-29T18:30:00Z",
  "scope": "project",
  "project": "/root/projects/shadow-work",
  "period_days": 7,
  "sessions": 42,
  "total_events": 12500,
  "tools": {
    "Bash": {"calls": 3000, "errors": 45, "rejections": 12},
    "Read": {"calls": 2800, "errors": 2, "rejections": 0},
    "Edit": {"calls": 1500, "errors": 30, "rejections": 5}
  },
  "patterns": {
    "edit_without_read": {"count": 8, "files": ["src/main.rs", "lib.rs"]},
    "tool_sequences": [["Read", "Grep", "Edit"], ["Bash", "Bash", "Bash"]],
    "repeated_errors": {"Edit": ["old_string not found", "old_string not found"]}
  },
  "skills": {
    "invoked": {"brainstorming": 5, "commit": 3},
    "available": ["brainstorming", "commit", "interdoc", "interpeer", "tool-time", "..."]
  },
  "context": {
    "claude_md_path": "/root/projects/shadow-work/CLAUDE.md",
    "claude_md_excerpt": "first 200 lines or null",
    "agents_md_path": "/root/projects/shadow-work/AGENTS.md",
    "agents_md_exists": true
  }
}
```

## Resolved Questions

### Diagnostics accumulation → Overwrite
`diagnostics.json` overwrites each run. Trend data lives in `stats.json` via a `trends` section that compares current period vs. previous period (computed fresh by `summarize.py`). The raw audit trail is `events.jsonl` itself — re-runnable at any time.

### MCP server design → Resource only
`tool-time://stats` serves `stats.json`. Stateless, simple. No `tool_time_analyze` tool needed — the `/tool-time` skill handles on-demand re-summarize via Bash. Add tools later if a real consumer emerges.

### SessionEnd hook → Silent summarize
Hook runs `summarize.py` silently, writes `stats.json`, no output. Agent reads it next session via skill or MCP resource. No more 3-line printed summary or `pending-suggestions.json`.
