---
title: Community Analytics Dashboard
date: 2026-01-30
status: brainstorm
---

# Community Analytics Dashboard

## What We're Building

A community-wide analytics dashboard that aggregates anonymized tool/skill/MCP usage data across Claude Code and Codex CLI users. Three audiences:

1. **Plugin/skill authors** — see which tools people actually use, prioritize what to build
2. **Users** — compare their usage to the community ("am I using tools effectively?")
3. **Platform team** — understand ecosystem health (what's failing, what's underused)

Agents can also query community data to compare local patterns against global trends.

## Why This Approach

**Static site + serverless ingestion (Cloudflare Pages + Worker + D1)**

- Scales to zero cost when idle
- No server to maintain
- Static dashboard is fast and cacheable
- D1 (SQLite) is sufficient for aggregated counts
- Worker handles ingestion with minimal cold start

Rejected alternatives:
- **Hosted API (FastAPI + Postgres)** — overkill for aggregated counts, costs money 24/7
- **GitHub-based (community repo)** — slow updates, awkward UX, API limits

## Key Decisions

### Data scope
Full stats.json contents plus model info:
- Tool names + calls/errors/rejections
- Skill names + invocation counts
- MCP server names + call counts
- Edit-without-read count
- Model used (opus, sonnet, haiku) — requires adding model to hook

### Privacy / anonymization
Strip everything identifying before data leaves the machine:
- File paths — removed entirely
- Project names — removed entirely
- Error messages — removed entirely
- Skill arguments — removed entirely
- Only tool/skill/MCP *names* and numeric counts survive
- Tool and MCP server names are public (in plugin registries), so safe to share

### Opt-in model
Anonymous by default, identity opt-in:
- Aggregated anonymous stats sent automatically (after initial opt-in to sharing)
- Users can opt in with an identity to see their own data on the dashboard
- No data sent until the user enables community sharing

### Agent access
Both HTTP API and MCP resource:
- HTTP API serves the dashboard and is publicly queryable
- MCP resource (`tool-time://community`) lets agents discover community stats naturally
- Same underlying data, two interfaces

## Open Questions

- **Upload frequency**: On every SessionEnd (real-time) or daily batch?
- **Retention**: How long to keep individual submissions vs. just aggregated totals?
- **Rate limiting**: How to prevent abuse of the ingestion endpoint?
- **User identity**: If opted in, what identifies a user? GitHub handle? Random token?
- **Model capture**: How to get the current model name in the hook? Is it in the hook input JSON?
- **Dashboard framework**: Plain HTML/JS, or something like Chart.js / D3 / Observable?
- **Community stats schema**: What aggregations are most useful? (top tools, error rate percentiles, skill adoption curves?)
