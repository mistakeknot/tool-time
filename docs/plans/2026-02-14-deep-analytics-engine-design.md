# Deep Analytics Engine — Design Document

**Date**: 2026-02-14
**Status**: Draft (from brainstorm)
**Version**: v0.4 — "Deep Analytics"

## Problem Statement

tool-time has 330K+ events spanning 5 months across 3 AI clients and 10+ projects, but the analysis layer only produces flat 7-day snapshots (tool counts, error counts). The interesting questions — workflow patterns, session types, time-of-day effects, client comparison — are invisible in aggregate counts.

Additionally, with only 1 community submitter, the community dashboard shows no ecosystem data. The best growth strategy is making the local experience so compelling it becomes its own marketing.

## Design Decisions

- **Privacy threshold stays at ≥10** — non-negotiable
- **New analysis is local-only** — `analysis.json` is never uploaded to the community API
- **`summarize.py` stays untouched** — it runs on every SessionEnd and must remain fast (~100ms)
- **New `analyze.py` runs on-demand** — called by the skill or local dashboard, not by hooks
- **D3.js for local dashboard** — full visualization flexibility, separate from community dashboard
- **Rule-based classification** — no ML (insufficient labeled data, transparency preferred)

## Architecture

```
events.jsonl (existing, unchanged)
       │
       ├──→ summarize.py (existing, unchanged)
       │         └──→ stats.json → upload.py → community API
       │
       └──→ analyze.py (NEW, on-demand)
                 └──→ analysis.json → local dashboard + /tool-time skill
```

## Component 1: analyze.py

### Input
- `~/.claude/tool-time/events.jsonl` (all events, not just 7-day window)
- Optional filters: `--project`, `--source`, `--since`, `--until`

### Output: analysis.json

```json
{
  "generated": "2026-02-14T...",
  "period": {"start": "2026-01-19", "end": "2026-02-14"},
  "filters": {"project": null, "source": null},

  "sessions": {
    "total": 758,
    "avg_duration_minutes": 42.3,
    "avg_tools_per_session": 436,
    "median_tools_per_session": 312,
    "classifications": {
      "building": 312,
      "exploring": 198,
      "debugging": 145,
      "planning": 67,
      "reviewing": 36
    }
  },

  "tool_chains": {
    "bigrams": [
      {"from": "Read", "to": "Edit", "count": 8234, "pct": 12.1},
      {"from": "Read", "to": "Read", "count": 7100, "pct": 10.4}
    ],
    "trigrams": [
      {"sequence": ["Glob", "Read", "Edit"], "count": 456},
      {"sequence": ["Read", "Edit", "Bash"], "count": 389}
    ],
    "retry_patterns": [
      {"tool": "Edit", "avg_retries": 1.3, "max_retries": 7, "sessions_with_retries": 89}
    ]
  },

  "trends": {
    "weekly": [
      {
        "week": "2026-W03",
        "events": 12340,
        "sessions": 45,
        "error_rate": 0.028,
        "tools": {"Read": 2100, "Bash": 1890, "Edit": 1200},
        "classifications": {"building": 18, "exploring": 15, "debugging": 8, "planning": 4}
      }
    ]
  },

  "time_patterns": {
    "by_hour": [
      {"hour": 0, "events": 12340, "error_rate": 0.045, "avg_session_tools": 520}
    ],
    "by_day_of_week": [
      {"day": "Monday", "events": 54000, "sessions": 112, "error_rate": 0.031}
    ],
    "peak_hour": 14,
    "peak_day": "Monday",
    "most_error_prone_hour": 3
  },

  "by_source": {
    "claude-code": {
      "events": 33421,
      "sessions": 245,
      "avg_tools_per_session": 136,
      "error_rate": 0.023,
      "top_tools": ["Read", "Bash", "Edit"],
      "classification_mix": {"building": 100, "exploring": 75, "debugging": 45, "planning": 25}
    },
    "codex": {
      "events": 178629,
      "sessions": 380,
      "avg_tools_per_session": 470,
      "error_rate": 0.031,
      "top_tools": ["shell", "shell_command", "Read"],
      "classification_mix": {"building": 230, "exploring": 85, "debugging": 50, "planning": 15}
    }
  },

  "projects": {
    "shadow-work": {
      "events": 139788,
      "sessions": 280,
      "top_tools": ["shell", "Read", "shell_command"],
      "primary_classification": "building",
      "error_rate": 0.029
    }
  }
}
```

### Session Classification Algorithm

Rule-based classifier using tool distribution vectors per session:

| Type | Rule |
|------|------|
| **Planning** | `EnterPlanMode` OR `ExitPlanMode` present, OR skill in {brainstorm, writing-plans, strategy} |
| **Building** | `(Edit + Write) / total > 0.25` AND not Planning |
| **Debugging** | `error_count / total > 0.15` OR `(Bash / total > 0.4 AND error_count > 3)` |
| **Exploring** | `(Read + Glob + Grep) / total > 0.55` AND `Edit / total < 0.10` |
| **Reviewing** | `Read / total > 0.50` AND `(Edit + Write) == 0` |
| **Other** | Default fallback |

Priority order: Planning > Debugging > Building > Reviewing > Exploring > Other

### Tool Chain Analysis

**Bigrams**: For each session, sort events by timestamp. For consecutive pairs (A, B), increment transitions[A][B]. Normalize. Filter to count > 10.

**Trigrams**: Sliding window of 3 over session tool sequences. Count and rank.

**Retry detection**: Within a session, find sequences where the same tool is called on the same file with an error followed by another call. A "retry chain" = `[Tool(file, error), ..., Tool(file, success|error)]`.

### Tool Name Normalization

Codex uses different tool names (`shell` = `Bash`, `shell_command` = `Bash`, `exec_command` = `Bash`). Normalize for cross-client comparison:

```python
TOOL_ALIASES = {
    "shell": "Bash",
    "shell_command": "Bash",
    "exec_command": "Bash",
    "write_stdin": "Write",
    "update_plan": "TaskUpdate",
}
```

Apply normalization only in `by_source` comparisons and cross-source trends. Keep raw names in project-specific breakdowns.

## Component 2: Local Dashboard

### Tech Stack
- D3.js v7 (full visualization library)
- Static HTML served locally (`python3 -m http.server` or embedded in skill)
- Reads `analysis.json` from `~/.claude/tool-time/`

### Views

1. **Overview** — KPI cards + session classification donut
2. **Tool Chains** — Sankey diagram (top 20 transitions), retry patterns table
3. **Trends** — Stacked area chart (weekly tool usage), line chart (error rate over time)
4. **Time Patterns** — Heatmap (hour × day of week, colored by event density), error rate overlay
5. **Source Comparison** — Grouped bar charts (Claude Code vs Codex vs OpenClaw)
6. **Projects** — Sortable table with sparklines, click to drill down

### Directory Structure

```
local-dashboard/
├── index.html          # Single page, section-based navigation
├── dashboard.js        # D3 rendering logic
├── style.css           # Dark theme (consistent with community dashboard)
└── serve.sh            # Helper: python3 -m http.server + open browser
```

## Component 3: Updated /tool-time Skill

**Change**: Skill runs `analyze.py` instead of (or in addition to) `summarize.py`.

**Agent narrative priorities** (what to highlight):
1. Session classification summary — how you spend your time
2. Tool chain insights — most common workflows, retry rates
3. Source comparison — efficiency differences across clients
4. Time-of-day patterns — peak productivity, error-prone hours
5. Week-over-week trends — what's changing
6. Anomalies — anything 2x+ different from your average

## Data Quality Notes

Known issues in current events.jsonl:
- 117,279 events (35%) have `source: null` — backfilled events pre-source tracking
- `model` field is often null in hook-captured events
- Codex tool names differ from Claude Code (addressed by normalization)
- Some sessions have only 1-2 events (likely interrupted/aborted)

## Implementation Order

1. `analyze.py` with tests
2. Local dashboard (D3)
3. Skill update
4. Data quality fixes (backfill source for older events if possible)

## Non-Goals

- No changes to `summarize.py`, `upload.py`, or community API
- No ML/clustering (rule-based classification is sufficient)
- No real-time streaming (batch analysis on-demand is fine)
- No mobile dashboard (desktop-first)
