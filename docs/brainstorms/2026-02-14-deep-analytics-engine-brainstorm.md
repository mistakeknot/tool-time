# Deep Analytics Engine Brainstorm

**Date**: 2026-02-14
**Context**: tool-time has been running 1+ week with 330K events across 5 months of backfilled + live data

## Problem

tool-time collects rich data (330K events, 3 AI clients, 10+ projects) but the analysis layer only produces flat 7-day snapshots. The interesting questions — workflow patterns, session types, time effects, client comparison — are invisible in aggregate counts.

Community dashboard has 1 submitter (ourselves). Privacy threshold of ≥10 means public sees zero ecosystem data.

## Data Assessment

### What exists
- **330,712 events** spanning 2025-09-24 to 2026-02-14
- **Sources**: Claude Code (33K), Codex (178K), OpenClaw (1.3K), unknown (117K)
- **Event types**: ToolUse (213K from backfill), Pre/PostToolUse (115K from hooks), Session events (2K)
- **Projects**: shadow-work (42%), Clavain (12%), Autarch (12%), tldr-swinton (6%), plus 6+ smaller
- **Skills**: flux-drive, compound, brainstorm, engineering-docs most popular
- **MCP**: agent-mail, serena most used

### Data quality issues found
- **117K hook events missing `source` field** — hook.sh doesn't write it (bug, not backfill gap)
- **296K events missing `model` field** — hook schema doesn't include model
- **Codex tool names differ** — `shell`/`shell_command` vs Claude Code's `Bash`

## Three Directions Explored

### 1. Personal Depth
Mine the 330K events for patterns invisible in flat counts:
- **Trends over time**: weekly tool usage, error rates, adoption curves
- **Cross-project fingerprints**: different projects have different tool mixes
- **Client comparison**: Claude Code vs Codex efficiency, error rates
- **Session patterns**: duration, tools per session, classification

### 2. Community Growth
Cold-start problem — 1 submitter, privacy threshold suppresses all data.
- **Decision**: Keep privacy threshold at ≥10 (non-negotiable)
- **Strategy**: Make local experience so compelling it becomes marketing
- Demo mode on dashboard for <10 submitters
- Cross-promote via other plugins

### 3. New Data Dimensions
Current data captures WHAT tools are used, not HOW or WHY:
- **Tool chains**: sequences, transitions, retry patterns
- **Session classification**: building, exploring, debugging, planning, reviewing
- **Duration/turn metrics**: session length, tool density, inter-tool gaps
- **Time-of-day**: productivity patterns, error-prone hours

## Decisions

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Privacy threshold | Keep at ≥10 | Non-negotiable, even during cold start |
| Output format | Both: richer skill + local D3 dashboard | Skill for quick insights, dashboard for deep exploration |
| New dimensions | All three (chains, classification, time) | All derivable from existing events, no new collection |
| Visualization | Full D3.js | Maximum flexibility for Sankey, heatmaps, force-directed |
| Classification | Rule-based, not ML | 758 sessions, no labels, transparency preferred |

## Priority Stack

1. **P0**: Fix hook.sh data quality (source + model fields)
2. **P1**: analyze.py — deep analysis engine (chains, sessions, trends, time, sources)
3. **P2**: Local D3 dashboard — 6 views of personal data
4. **P3**: Updated /tool-time skill — agent narrates analysis.json
5. **P4**: Community growth tactics (marketing, not engineering)

## Design Doc
See `docs/plans/2026-02-14-deep-analytics-engine-design.md` for full technical design.
