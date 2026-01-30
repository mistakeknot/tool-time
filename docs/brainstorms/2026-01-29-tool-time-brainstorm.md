# tool-time Brainstorm

**Date:** 2026-01-29
**Status:** In Progress

## What We're Building

A Claude Code **plugin + MCP server + standalone CLI** that:

1. **Observes** all tool, skill, MCP, and plugin usage per session — stored globally (`~/.claude/tool-time/`) and per-project (`.claude/tool-time/`)
2. **Optimizes** Claude Code behavior by feeding analytics back into CLAUDE.md/AGENTS.md, skills, and hook rules
3. **Reports** via standalone CLI (`tool-time report/optimize`) and in-session `/tool-time` commands

## Why This Approach

**Key finding from [Vercel research](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals):**
- AGENTS.md (passive, always-present context) achieved **100% pass rate** vs skills at **53%**
- Skills require decision overhead — the agent must choose to invoke them
- Passive doc updates are the highest-ROI optimization target

**Implication for tool-time:** The primary optimization output should be **CLAUDE.md/AGENTS.md modifications** (passive), not just new skills (active). Skills and hooks are secondary optimization targets requiring user approval.

## Architecture: Tiered Optimizer

### Data Collection (4 hooks)

| Hook | Captures | Budget |
|------|----------|--------|
| PreToolUse (`*`) | tool name, params, project, session ID, timestamp | < 20ms |
| PostToolUse (`*`) | result summary, duration, errors | < 20ms |
| SessionStart | enabled plugins, skills, cwd, session metadata | < 50ms |
| SessionEnd | flush events, trigger optimization analysis | < 500ms |

### Storage

```
~/.claude/tool-time/           # Global
  config.json                  # User preferences
  events/YYYY-MM-DD.jsonl      # Raw events (append-only)
  aggregates/
    by-project/<name>.json     # Per-project rollups
    by-tool/<name>.json        # Per-tool rollups
    by-skill/<name>.json       # Per-skill rollups
  analysis/
    insights.json              # Current insights
    suggestions/               # Pending suggestions

<project>/.claude/tool-time/   # Per-project
  stats.json                   # Project-specific aggregates
  insights.json                # Project-specific insights
```

### MCP Server (stdio, Python)

| Tool | Purpose |
|------|---------|
| `tool_time_get_stats` | Query usage stats (by tool, project, date range) |
| `tool_time_get_insights` | Get current optimization insights |
| `tool_time_suggest_optimizations` | Generate actionable suggestions |

Resources: `tool-time://stats/today`, `tool-time://stats/project/<path>`

### CLI

```bash
tool-time report                    # Summary dashboard
tool-time report --project <path>   # Per-project report
tool-time report --tool Bash        # Per-tool deep dive
tool-time optimize                  # Show pending suggestions
tool-time optimize --apply          # Apply safe changes
tool-time insights                  # Current insights
tool-time config                    # Edit configuration
```

### In-Session Commands

- `/tool-time` — Quick stats for current session
- `/tool-time report` — Full report
- `/tool-time optimize` — Interactive optimization with approval

### Optimization Engine (3 Tiers)

**Tier 1 — Auto-Apply (CLAUDE.md/AGENTS.md only):**
- Append tool-use hints based on error patterns (never modify existing content)
- Example: "Bash errors on missing dir" → add "Always verify directory exists before operations"
- Example: "Edit used without Read" → add "Always Read files before Edit"
- Max 10 hints, rotated by relevance

**Tier 2 — Suggest Only (skills, hooks, CLI config):**
- Propose new skills for recurring workflows
- Propose hookify rules for repeated mistakes
- Require explicit user approval

**Tier 3 — Notify Only (trends):**
- Surface at session end: token trends, error rates, unused skills
- Informational, no action taken

### User Configuration

```json
{
  "auto_apply": "safe-only",         // "safe-only" | "suggest-all" | "auto-all"
  "notify_at_session_end": true,
  "track_tool_output": false,        // Privacy: don't log tool outputs by default
  "optimization_threshold": 5,       // Min occurrences before suggesting
  "max_claude_md_hints": 10
}
```

Default: **tiered** (auto-apply CLAUDE.md, suggest skills/hooks, notify trends).

## Key Decisions

1. **Plugin + MCP + CLI** — Plugin for hooks/collection, MCP for in-session queries, CLI for standalone reports
2. **Global + per-project storage** — Aggregates at both levels for cross-project and project-specific insights
3. **CLAUDE.md is primary optimization target** — Aligns with Vercel research (passive > active)
4. **Tiered auto-apply default** — Safe changes auto-applied, risky changes suggested, trends notified
5. **Python for hooks + MCP, Go for CLI** — Hooks/MCP match existing patterns; Go CLI for fast binary with no runtime deps
6. **Sanitized error logging** — Log error messages with paths stripped, tokens hashed, categories preserved. No successful output logged.
6. **Session end triggers optimization** — Natural checkpoint, not disruptive mid-work

## Open Questions

1. **Skill usage tracking** — Skills are loaded via the Skill tool, but how to track which skill *influenced* subsequent actions? (May need UserPromptSubmit hook to detect `/skill` invocations)
2. **MCP tool usage** — ToolSearch is used to load deferred tools; PostToolUse on ToolSearch could track MCP tool discovery
3. **Privacy** — Should tool inputs/outputs be logged? Default: no (only tool names + error/success). Configurable.
4. **Cross-project insights** — How to surface "in project X you used skill Y effectively, consider it here"?
5. **Retention policy** — How long to keep raw JSONL events? Default: 30 days?

## Alternatives Considered

**Minimal Logger (rejected):** Collect-only, no optimization. Missing the key value prop — analytics without action is just data hoarding.

**Full ML Intelligence (deferred):** Pattern mining, error classification, proactive PreToolUse suggestions. Needs months of data from the tiered approach first. YAGNI for now.
