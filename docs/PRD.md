# tool-time: Product Requirements Document

**Version:** 0.3.5
**Last updated:** 2026-02-14

## Problem

AI coding agents make hundreds of tool calls per session, but developers have no visibility into these patterns. There is no way to answer basic questions: Which tools fail most? Am I using the right tools for the job? How does my workflow differ across clients? What skills and MCP servers do other people use?

Without observability, agents repeat the same mistakes, developers can't tune their configurations, and the ecosystem has no signal for what actually matters.

## Solution

tool-time is a Claude Code plugin that passively captures tool usage, aggregates it into actionable stats, and uses agent reasoning to propose concrete workflow improvements. Optionally, anonymized data feeds a public community dashboard showing ecosystem-wide adoption patterns.

## Users

### Primary: Solo developer using AI coding agents

- Uses Claude Code daily, possibly Codex CLI or OpenClaw too
- Wants to improve agent effectiveness without manual monitoring
- Cares about practical fixes ("add this line to CLAUDE.md"), not abstract metrics
- Privacy-conscious — won't share data without understanding exactly what leaves the machine

### Secondary: Plugin/skill author

- Wants to understand adoption of their tools
- Needs discovery signal: what categories of tools are underserved?
- Interested in co-occurrence patterns ("people who use X also use Y")

## Features

### F1: Passive Event Collection (shipped, v0.1)

**What:** Four lifecycle hooks (PreToolUse, PostToolUse, SessionStart, SessionEnd) log every tool call to `~/.claude/tool-time/events.jsonl`.

**Why:** Collection must be invisible — zero user interaction, <20ms per hook, no configuration required.

**Acceptance criteria:**
- Events include: tool name, timestamp, session ID, project path, error (if any), skill name, file path, model
- Hook budget: <20ms for Pre/PostToolUse, <50ms for SessionStart, <500ms for SessionEnd
- No tool outputs or inputs logged (privacy default)

### F2: Historical Backfill (shipped, v0.1.1)

**What:** Transcript parsers for Codex CLI (`~/.codex/sessions/`) and OpenClaw (`~/.openclaw/agents/`, `~/.moltbot/agents/`, `~/.clawdbot/agents/`) extract tool usage from historical sessions.

**Why:** Users switching from Codex or OpenClaw shouldn't start from zero. Historical data enables immediate insights.

**Acceptance criteria:**
- Codex parser handles `rollout-*.jsonl` format
- OpenClaw parser handles all three directory names (rebranding history)
- Sessions deduplicated across OpenClaw directories
- Output format identical to hook-captured events

### F3: Stats Aggregation (shipped, v0.2)

**What:** `summarize.py` reads events.jsonl, computes per-project stats over a 7-day rolling window, writes `stats.json`.

**Why:** Raw JSONL is too large for agent context windows. Aggregated stats are compact and immediately useful.

**Acceptance criteria:**
- Per-tool: calls, errors, rejections
- Edit-without-read count (session-scoped detection)
- Skill usage (names + invocation counts)
- MCP server usage (parsed from `mcp__<server>__<tool>` prefixes)
- Installed plugins (from `~/.claude/settings.json`)
- Runs in <500ms, called automatically on SessionEnd
- Zero opinions or thresholds — pure data preparation

### F4: Agent-Driven Analysis (shipped, v0.2)

**What:** `/tool-time` skill triggers `summarize.py`, then the agent reads `stats.json` alongside CLAUDE.md and AGENTS.md, reasons about patterns, and proposes specific fixes.

**Why:** Hardcoded heuristics catch only anticipated patterns. Agent reasoning adapts to context — it can read project docs, compare available vs. used skills, and generate fixes tailored to the specific project.

**Acceptance criteria:**
- Agent detects: high error rates, high rejection rates, edit-without-read, Bash dominance (only when doing file operations), low tool diversity
- Agent reads project CLAUDE.md/AGENTS.md and identifies gaps
- Agent proposes specific text additions, not vague suggestions
- Agent recommends relevant skills from playbooks.com based on project language
- Codex skill variant runs backfill first

### F5: Community Observatory (shipped, v0.3)

**What:** Opt-in anonymized upload to Cloudflare Worker + D1. Public dashboard at tool-time.org shows ecosystem leaderboards.

**Why:** Individual analytics are useful but limited. Ecosystem data answers: what do experienced users use? What's popular? What MCP servers cluster together?

**Acceptance criteria:**
- Opt-in via `config.json` (`community_sharing: true`)
- Strict allow-list of shared fields: submission token, hour-precision timestamp, per-tool stats, edit-without-read count, model, skill usage, MCP server usage, installed plugins (public names only)
- NOT shared: file paths, project names, error messages, skill arguments
- GDPR deletion via `/tool-time delete my data`
- Privacy threshold: data only shown publicly when ≥10 unique submitters
- Dashboard tabs: Tools, Skills, MCP Servers, Plugins
- Plugin usage stored as aggregate-only (no per-submission linkage)

### F6: Deep Analytics Engine (designed, v0.4)

**What:** `analyze.py` mines the full event history for patterns invisible in flat counts: session classification, tool chains, trends over time, time-of-day patterns, cross-client comparison. Local D3 dashboard for exploration.

**Why:** 330K events contain rich workflow intelligence that 7-day aggregates discard. Session types, productivity rhythms, and client efficiency differences are the next layer of insight.

**Acceptance criteria:**
- Session classification: building, exploring, debugging, planning, reviewing (rule-based)
- Tool chain analysis: bigrams, trigrams, retry patterns
- Weekly trends: tool usage, error rates, session counts over time
- Time patterns: by hour, by day of week, peak/error-prone periods
- Cross-source comparison: Claude Code vs. Codex vs. OpenClaw
- Per-project breakdowns with primary classification
- Output: `analysis.json` (local only, never uploaded)
- Local D3 dashboard: 6 views (overview, chains, trends, time, sources, projects)
- Updated `/tool-time` skill narrates highlights from analysis.json

### F7: Ecosystem Recommendations (future, v0.5)

**What:** Correlation data and recommendation engine — "people who use X also use Y."

**Why:** Discovery is the hardest problem in any ecosystem. Revealed co-occurrence patterns are more trustworthy than editorial curation.

**Acceptance criteria:**
- Skill-to-tool pair correlation data
- Co-occurrence recommendations with sufficient data volume
- Ecosystem map visualization (D3 force-directed or similar)
- Minimum data threshold before recommendations are shown

## Non-Functional Requirements

### Performance
- Hook execution: <20ms (PreToolUse, PostToolUse), <50ms (SessionStart), <500ms (SessionEnd)
- `summarize.py`: <500ms on 7 days of data
- `analyze.py`: <5s on full event history (on-demand, not hook-triggered)
- Community API: <200ms response time

### Privacy
- No data leaves the machine without opt-in
- Community sharing uses strict allow-list (not deny-list)
- Privacy threshold ≥10 submitters for public display (non-negotiable)
- Plugin usage stored aggregate-only
- GDPR deletion available
- No tool inputs/outputs logged

### Reliability
- Hooks must not crash Claude Code sessions — fail silently on error
- `summarize.py` must handle malformed JSONL lines gracefully
- Community API must handle duplicate submissions (idempotent on token + timestamp)

### Compatibility
- Claude Code: full support (hooks + skill)
- Codex CLI: backfill parser + skill variant (no hooks)
- OpenClaw: backfill parser, future hook support when available

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent vs. heuristics | Agent-driven analysis | Agents adapt to context; heuristics only catch anticipated patterns |
| summarize.py scope | Zero opinions | Data layer stays fast and testable; reasoning layer evolves separately |
| Privacy default | Nothing shared | Trust is earned incrementally; opt-in is harder but more durable |
| Session classification | Rule-based | 758 sessions, no labels; transparency > accuracy at this scale |
| Community data model | Aggregate plugin usage | Per-submission linkage creates privacy risk with no analytical benefit |
| Local dashboard tech | D3.js | Maximum flexibility for Sankey, heatmaps, force-directed graphs |
| Community dashboard tech | Chart.js | Simpler, sufficient for leaderboard views |
| API infrastructure | Cloudflare Worker + D1 | Scales to zero cost, no server to maintain |
| Cross-agent strategy | Same data pool, source-tagged | One observatory, filterable by client |
| Auto-apply optimization | Deferred | Agent suggestions must prove consistently good before automation |

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| Event capture reliability | >99% of tool calls logged | Compare hook event count vs. transcript event count |
| Analysis actionability | >50% of suggestions lead to CLAUDE.md changes | Track which suggestions users accept (manual observation) |
| Community growth | 10 submitters within 3 months of v0.3 | Dashboard submitter count |
| Cross-client coverage | 3 AI clients supported | Codex + OpenClaw parser tests passing |
| Privacy compliance | Zero data leaks | Allow-list review, no PII in community tables |

## Out of Scope

- **ML/clustering** — Rule-based classification is sufficient at current scale
- **Token profiling** — Different problem, different tool
- **Auto-applying CLAUDE.md changes** — Requires proven track record first
- **Real-time streaming** — Batch analysis on-demand is fine
- **Mobile dashboard** — Desktop-first
- **Individual user profiles on community dashboard** — Observatory, not social network

## Dependencies

| Dependency | Type | Risk |
|------------|------|------|
| Claude Code hook system | Platform | Stable, unlikely to break |
| Codex CLI transcript format | External | May change without notice; parser is defensive |
| OpenClaw hook system | External | Not yet shipped; filesystem scanning as fallback |
| Cloudflare Workers + D1 | Infrastructure | Mature, low risk |
| playbooks.com API | External | Used for skill recommendations; graceful degradation if unavailable |

---

*Derived from brainstorms (2026-01-29 through 2026-02-14), roadmap, AGENTS.md, and production experience.*
