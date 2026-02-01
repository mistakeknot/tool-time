# Ecosystem Observatory: Revealed Preferences for Skills, MCP Servers, and Plugins

**Date**: 2026-02-01
**Status**: Brainstorm

## What We're Building

Turn tool-time's community dashboard from a "tool call counter" into an **ecosystem observatory** — a public resource showing what skills, MCP servers, and plugins real people actually use across Claude Code and Codex CLI. Think npm download counts meets "people who use X also use Y" recommendations.

### Audiences
- **Plugin/skill authors**: What's popular? What gaps exist?
- **Claude Code users**: Discovery — which plugins/MCP servers should I install?
- **Anthropic/ecosystem**: Health metrics — what integrations matter?
- **Codex CLI users**: Same data pool, tagged by source

## The Gap: What We Have vs. What We Need

### Already captured but discarded

| Data | Captured in events.jsonl? | In stats.json? | Submitted to API? | On dashboard? |
|------|---------------------------|-----------------|-------------------|---------------|
| Skill invocations | ✅ (`skill` field) | ❌ | ❌ | ❌ |
| MCP server names | ✅ (parseable from `mcp__<server>__<tool>`) | ❌ | ❌ (raw tool names only) | ❌ |
| Source (claude-code vs codex) | ✅ (`source` field) | ❌ | ❌ | ❌ |
| Installed plugins | ❌ | ❌ | ❌ | ❌ |
| Skill-to-tool correlations | ❌ (data exists but not correlated) | ❌ | ❌ | ❌ |

### What needs to change at each layer

**1. summarize.py** — aggregate new dimensions:
- `skills`: `{ "superpowers:brainstorming": { invocations: N, errors: N } }`
- `mcp_servers`: `{ "chrome-devtools": { tools_used: [...], calls: N, errors: N } }` — parsed from `mcp__<server>__<tool>` prefix
- `installed_plugins`: list of plugin names from `~/.claude/plugins/` manifests
- `source`: `"claude-code"` or `"codex"` (already in events, just surface it)

**2. upload.py** — add new fields to anonymized payload:
- `skills`: skill name → count (no arguments, no project context)
- `mcp_servers`: server name → call count (no tool-level detail? or with tool names?)
- `installed_plugins`: list of plugin names (not paths)
- `source`: tag each submission
- `skill_tool_pairs`: optional — `[["brainstorming", "Task"], ["brainstorming", "Read"]]` with counts for correlation data

**3. Community API (index.ts)** — new tables and endpoints:
- `skill_usage` table (submission_id, skill_name, invocations, errors)
- `mcp_server_usage` table (submission_id, server_name, calls, errors)
- `plugin_installs` table (submission_id, plugin_name)
- `skill_tool_correlations` table (submission_id, skill_name, tool_name, calls)
- Add `source` column to submissions table
- New API: `GET /v1/api/skills`, `GET /v1/api/mcp-servers`, `GET /v1/api/plugins`, `GET /v1/api/correlations`

**4. Dashboard** — three layers:
- **Default: Leaderboards** — top skills, top MCP servers, top plugins by adoption count
- **Tab 2: Ecosystem map** — which skills go with which tools, which MCP servers cluster together
- **Tab 3: Recommendations** — "people who use X also use Y"

**5. backfill.py / parsers.py** — ensure Codex transcript parsing extracts the same new fields

## Privacy Considerations

- Skill names: **safe** — they're public identifiers (e.g., `superpowers:brainstorming`)
- MCP server names: **safe** — extracted from tool name prefix, no custom server paths exposed
- Plugin names: **safe** — public registry names
- Skill-to-tool correlations: **slightly richer** than current data — reveals workflow patterns, not personal data
- Source field: **safe** — just "claude-code" or "codex"
- **NOT shared**: skill arguments, file paths, project names, error messages (same bar as today)

## Key Decisions

1. **Same community API, not separate** — Codex and Claude Code data in one pool, filterable by `source`
2. **Plugin detection via manifest scan** — read `~/.claude/plugins/` at summarize time, not hook time
3. **MCP server names parsed from tool names** — no new hook instrumentation needed
4. **Correlation data is opt-in to the "slightly richer" tier** — skill-to-tool pairs submitted alongside counts
5. **Dashboard layers**: leaderboards (default) → ecosystem map → recommendations

## Resolved Questions

- **MCP server granularity**: Both — server-level totals in leaderboard, per-tool breakdown on drill-down. Submit per-tool stats, aggregate on display.
- **Plugin detection**: Both sources — SessionStart hook captures installed plugins for freshness, summarize.py scans as fallback/reconciliation.
- **Correlation data**: Skip for v1. Ship leaderboards first, add "people who use X also use Y" when there's enough data volume.
- **Dashboard tech**: Chart.js only for v1. Defer ecosystem map/D3 until we have real correlation data to visualize.
- **Migration path**: Additive only — new tables, new nullable columns. Existing submissions unaffected.
- **Codex CLI submission**: Same upload.py, different `source` tag. backfill.py needs upload support added.

## Resolved (Round 2)

- **Plugin data source**: Use `~/.claude/settings.json` → `enabledPlugins` for the canonical list (stable, just `name@marketplace` keys). Enrich with version from `~/.claude/plugins/installed_plugins.json` if available.
- **MCP edge cases**: Built-in MCP tools (`ListMcpResourcesTool`, `ReadMcpResourceTool`, `ToolSearch`) count in both regular tool stats AND as an "MCP Infrastructure" signal. `ToolSearch` especially indicates MCP discovery behavior.
- **Dashboard layout**: Tabs across top — `Tools | Skills | MCP Servers | Plugins`. Each tab is its own leaderboard. MCP Servers tab has drill-down to per-tool breakdown.

## OpenClaw / Cross-Agent Support

### Vision
tool-time becomes a **cross-agent MCP observatory** — tracking skill and MCP server adoption across Claude Code, Codex CLI, and OpenClaw. MCP is the common ground.

### OpenClaw Integration Path

**What OpenClaw has:**
- Hook system (`~/.openclaw/hooks/`) with TypeScript handlers and YAML frontmatter HOOK.md files
- `tool_result_persist` hook (synchronous, can observe tool results)
- Planned tool lifecycle events (pre/post tool, session start/end)
- Skills system (`~/.openclaw/workspace/skills/<skill>/SKILL.md`) — similar to Claude Code skills
- ClawHub skill registry (700+ community skills)
- Usage tracking via `/usage` and `/status` commands (but no JSONL event log like tool-time)
- MCP integration for 100+ third-party services

**v1 approach: Skill-only (no OpenClaw hooks needed)**

OpenClaw's tool lifecycle hooks are still "planned" (not shipped). Instead of waiting, we scan the filesystem:

1. **OpenClaw skill scanner**: Parse `~/.openclaw/workspace/skills/` for installed skills — same approach as Claude Code plugin scanning.
2. **OpenClaw MCP scanner**: Parse `~/.openclaw/openclaw.json` for configured MCP servers.
3. **Separate event files**: `events-claude.jsonl`, `events-codex.jsonl`, `events-openclaw.jsonl`. Merge at summarize time.
4. **Community API `source` field**: Add to submissions table. Dashboard filters by source or shows combined.
5. **Future: OpenClaw hooks**: When OpenClaw ships tool lifecycle events, add a TypeScript hook package (`~/.openclaw/hooks/tool-time/`) for real-time tool call tracking. Until then, we have install/config data only (no call counts).

### What's shared across agents (the MCP observatory angle)
- **MCP server names**: Same servers used across Claude Code and OpenClaw — the adoption data is directly comparable
- **Tool call patterns**: How different agents use the same MCP tools
- **Skill ecosystems**: Claude Code plugins vs. ClawHub skills — what categories are popular in each?

### What stays agent-specific
- Plugin/skill names (different registries)
- Config file locations
- Hook implementation details

## Approach: Incremental, Back-to-Front

Recommended build order:

### Phase 1: Claude Code ecosystem data (v0.3)
1. **summarize.py**: Add skill/MCP/plugin aggregation (pure Python, testable, no API changes)
2. **upload.py**: Extend payload with new fields + `source` tag
3. **D1 migration**: New tables (skill_usage, mcp_server_usage, plugin_installs) + source column
4. **API routes**: New GET endpoints for `/v1/api/skills`, `/v1/api/mcp-servers`, `/v1/api/plugins`
5. **Dashboard**: Tabs — Tools | Skills | MCP Servers | Plugins
6. **backfill.py**: Codex data extraction + upload support

### Phase 2: OpenClaw integration (v0.4)
7. **OpenClaw skill/MCP scanner**: Parse `~/.openclaw/workspace/skills/` and `~/.openclaw/openclaw.json` for installed skills and MCP servers
8. **Separate event files**: Write OpenClaw scan results to `events-openclaw.jsonl`, merge at summarize time
9. **summarize.py**: Add OpenClaw source handling — merge all `events-*.jsonl` files, tag by source
10. **Dashboard**: Add source filter toggle, cross-agent MCP comparison view
11. **Future**: When OpenClaw ships tool lifecycle hooks, add TS hook for real-time tool call tracking

### Phase 3: Recommendations (v0.5)
11. **Correlation data**: Skill-to-tool pairs, cross-agent MCP patterns
12. **"People who use X also use Y"**: Requires sufficient data volume
13. **Ecosystem map**: D3 visualization of relationships

Each phase is independently shippable.
