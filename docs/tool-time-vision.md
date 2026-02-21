# tool-time: Vision

**Version:** 0.3.1
**Last updated:** 2026-02-14

## What tool-time Is

tool-time is an observability layer for AI-assisted development. It captures every tool call across Claude Code, Codex CLI, and OpenClaw sessions, surfaces patterns invisible to the humans and agents making those calls, and feeds insights back into the system — closing the loop between what agents do and what they should do differently.

It is also, by design, a public observatory. Anonymized community data reveals what skills, MCP servers, and plugins real people actually use — turning private telemetry into shared ecosystem intelligence.

## Core Conviction

You can't improve what you can't see. AI agents make hundreds of tool calls per session, but today that activity is a black box — no metrics, no trends, no feedback loop. tool-time makes agent behavior observable, measurable, and improvable, without requiring the human to watch every call.

The highest-ROI optimization is passive: updating CLAUDE.md and AGENTS.md with data-driven hints. Skills require the agent to choose to invoke them. Passive context is always present. tool-time's primary output is the passive layer.

## Audience

1. **Solo practitioners.** Engineers using Claude Code, Codex CLI, or OpenClaw who want to understand and improve their agent workflows. The local experience — personal analytics, pattern detection, concrete fix suggestions — is the primary product.

2. **Plugin and skill authors.** Ecosystem builders who want to understand adoption — which tools, skills, and MCP servers people actually use, which combinations cluster together, and where the gaps are.

3. **The ecosystem.** A public signal of what matters in AI-assisted development. npm has download counts. tool-time has revealed preferences — what people use, not what they say they use.

## Operating Principles

### 1. Data prepares, agents reason

Code collects and aggregates. Agents analyze and recommend. `summarize.py` computes stats with zero opinions; the `/tool-time` skill reads those stats alongside CLAUDE.md and proposes fixes. This separation keeps the data layer fast, testable, and deterministic while letting agent reasoning evolve with model capabilities.

### 2. Privacy is non-negotiable

Nothing leaves the machine without explicit opt-in. Community sharing is anonymized: tool names and counts only, no file paths, no project names, no error messages, no skill arguments. The privacy threshold of 10 submitters is never lowered, even during cold start. Aggregate-only plugin data with no per-submission linkage.

### 3. Passive over active

CLAUDE.md modifications (always present, zero decision overhead) beat skill invocations (require the agent to choose) beat notifications (require the human to act). This aligns with research showing passive context achieves higher pass rates than active skill invocation.

### 4. Incremental, back-to-front

Ship each layer independently: data collection first, then aggregation, then analysis, then visualization. Each phase is useful on its own. Don't build the dashboard before the data pipeline is proven.

### 5. Cross-agent by design

MCP is the lingua franca. The same MCP servers appear across Claude Code, Codex CLI, and OpenClaw — tool-time treats them as a shared observable layer, not client-specific features. Data is tagged by source but analyzed together.

### 6. Local-first, community-second

The local experience must be compelling before community features matter. Personal analytics, session classification, tool chain analysis, time-of-day patterns — these are the product. Community data is a bonus that emerges when enough people find the local experience worth using.

## Scope

| Layer | What It Does | Status |
|---|---|---|
| **Collection** | Hooks capture every tool call; transcript parsers backfill historical data from Codex CLI and OpenClaw | Shipped (v0.1) |
| **Aggregation** | 7-day rolling stats per project — calls, errors, rejections, edit-without-read, skills, MCP servers, installed plugins | Shipped (v0.2) |
| **Agent analysis** | `/tool-time` skill reads stats + project docs, spots patterns, proposes concrete fixes | Shipped (v0.2) |
| **Community observatory** | Anonymized upload, leaderboard dashboard (tools, skills, MCP servers, plugins), GDPR deletion | Shipped (v0.3) |
| **Deep analytics** | Session classification, tool chains, trends over time, time-of-day patterns, cross-client comparison, local D3 dashboard | Designed (v0.4) |
| **Recommendations** | "People who use X also use Y", ecosystem map, correlation data | Future (v0.5) |

## Current State

- **330K+ events** spanning 5 months across 3 AI clients and 10+ projects
- **v0.3** published to interagency-marketplace
- Community dashboard live at [tool-time.org](https://tool-time.org)
- Worker API at tool-time-api.mistakeknot.workers.dev
- 1 community submitter (cold-start phase — strategy: make local experience so compelling it becomes marketing)

## Direction

### Near-term: Deep personal analytics (v0.4)

The richest unexplored territory is in the data we already have. 330K events contain workflow patterns, session types, productivity rhythms, and efficiency differences across clients — all invisible in flat 7-day counts. `analyze.py` mines these dimensions; a local D3 dashboard makes them explorable; the updated skill narrates the highlights.

### Medium-term: Cross-agent observatory (v0.5)

As OpenClaw ships tool lifecycle hooks, tool-time becomes the single pane of glass for MCP adoption across the entire AI coding agent ecosystem. The community dashboard evolves from leaderboards to an ecosystem map with recommendation engine.

### Long-term: Feedback loop automation

With enough data and demonstrated accuracy, tool-time could auto-apply safe CLAUDE.md hints (tier 1 optimization from the original brainstorm). The prerequisite is months of agent-driven suggestions proving consistently good — automation earned through measurement, not assumed.

## Ecosystem Position

tool-time occupies the **observability** slot in the inter-* constellation:

| Companion | Relationship |
|---|---|
| **Clavain** | Hub — tool-time measures the effectiveness of Clavain's workflows |
| **interflux** | Review engine — tool-time tracks which review agents run and their error rates |
| **interphase** | Phase tracking — tool-time could classify sessions by project phase |
| **interline** | Statusline — tool-time stats could surface in the status bar |
| **interpath** | Artifact generation — tool-time provides data for product artifacts |
| **interwatch** | Doc freshness — tool-time detects when CLAUDE.md hints go stale |

tool-time is uniquely cross-cutting: it observes all other plugins without depending on them. This independence is intentional — the observatory must work even when no other inter-* plugins are installed.

## Key Bets

1. **Revealed preferences beat stated preferences.** What people actually use (measured by tool-time) is more valuable than what they say they want (surveys, GitHub stars). This data becomes the ecosystem's ground truth.

2. **Agent reasoning will keep getting better.** By separating data preparation from analysis, tool-time automatically benefits from model improvements without code changes. Today's analysis is bounded by agent capability; tomorrow's won't require a tool-time update.

3. **The cold-start problem solves itself.** If the local experience is genuinely useful, community sharing follows. The growth strategy is product quality, not marketing.

4. **Cross-agent is the durable moat.** Individual AI coding tools will build their own analytics. tool-time's value is in the cross-agent view — the only place you can compare your Claude Code, Codex, and OpenClaw workflows side by side.

## What tool-time Is Not

**Not a profiler.** tool-time measures what tools are called and whether they succeed, not how long they take or how many tokens they consume. Latency and token profiling are different problems.

**Not a linter.** tool-time detects patterns (edit-without-read, high error rates) but doesn't block tool execution. It is observational, not prescriptive. Suggestions are always opt-in.

**Not a replacement for CLAUDE.md.** tool-time augments project documentation with data-driven hints. It doesn't generate documentation from scratch or replace human-authored project instructions.

**Not a social network.** The community dashboard shows aggregate data, not individual profiles. There are no usernames, no leaderboards of individuals, no competitive features. The unit of observation is the ecosystem, not the person.

---

*Synthesized from brainstorms (2026-01-29 through 2026-02-14), roadmap, and production experience with 330K+ events across 3 AI clients.*
