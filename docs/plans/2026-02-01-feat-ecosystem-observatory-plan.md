---
title: "feat: Add ecosystem observatory (skills, MCP servers, plugins)"
type: feat
date: 2026-02-01
brainstorm: docs/brainstorms/2026-02-01-ecosystem-observatory-brainstorm.md
deepened: 2026-02-01
reviewed: 2026-02-01
---

# feat: Add ecosystem observatory (skills, MCP servers, plugins)

## Review Summary

**Reviewed by:** dhh-rails-reviewer, code-simplicity-reviewer, kieran-rails-reviewer

### Fixes Applied
1. **Cut MCP drill-down** — no data pipeline feeds per-tool rows; defer to v2
2. **Cut `mcp_tool` and `plugin_installed` dimensions** — dead code in CHECK constraint
3. **Cut `?dimensions=` query param** — single consumer, always wants everything
4. **Cut extra indexes** — 1 index sufficient at <1K submissions/week
5. **Standardized naming** — `calls` everywhere (not `invocations`), `dimensions` namespace in API response
6. **Simplified plugin scanner** — removed fallback file, removed `AttributeError` catch
7. **Simplified Zod validation** — removed `.refine()` duplicate check on plugins, simplified batch limits
8. **Cut skill error tracking** — no mechanism to detect skill errors in events

---

## Overview

Extend tool-time's community dashboard from a tool call counter into an ecosystem observatory showing which skills, MCP servers, and plugins real people use. Phase 1 covers Claude Code ecosystem data end-to-end.

## Problem Statement

tool-time already captures skill invocations and MCP tool calls in `events.jsonl`, but **discards this data** before it reaches the community. Plugin/skill authors have no visibility into adoption. Users choosing what to install have no popularity signal. The data exists — we just need to surface it.

## Proposed Solution

Extend every layer of the pipeline: summarize → upload → API → dashboard.

**New dimensions tracked:**
- **Skills**: Which Claude Code skills are invoked (already in events.jsonl `skill` field)
- **MCP servers**: Parsed from tool names matching `mcp__<server>__<tool>` pattern
- **Installed plugins**: Read from `~/.claude/settings.json` → `enabledPlugins` at summarize time

## Technical Approach

### Architecture

No new infrastructure. Same pipeline, wider data:

```
hooks → events.jsonl → summarize.py → stats.json → upload.py → Worker API → D1 → Dashboard
                        ↑ new aggregations        ↑ new fields   ↑ new tables  ↑ new charts
```

### Key Architectural Decision: Separate Simple Tables

Use **two new tables** (`skill_stats` and `mcp_server_stats`) plus `plugin_usage_aggregate`. Each has a trivial schema with obvious queries — no `WHERE dimension = ?` filter needed.

**Plugins use aggregate-only tracking** (no per-submission linkage) to prevent fingerprinting via unique plugin combinations.

```sql
skill_stats (submission_id, name, calls)
mcp_server_stats (submission_id, name, calls, errors)
plugin_usage_aggregate (plugin_name, install_count, last_seen)
```

### Ship as 3 PRs

- **PR 1**: `summarize.py` + tests — skills, MCP servers, plugins in stats.json
- **PR 2**: `upload.py` + Worker API + migration — store and serve the data
- **PR 3**: Dashboard — charts for all three dimensions

### PR 1: summarize.py — aggregate new dimensions

**Files**: `summarize.py`, `test_summarize.py`

Extend `compute_tool_statistics()` (currently lines 64–131):

1. **Skills** — collect `ev.get("skill")` into `skill_counts: Counter`:
   ```python
   skill_name = ev.get("skill")
   if skill_name:
       skill_counts[skill_name] += 1
   ```

2. **MCP servers** — parse tool names starting with `mcp__`:
   ```python
   if tool.startswith("mcp__"):
       parts = tool.split("__", 2)
       if len(parts) >= 3 and parts[1]:  # Guard against empty server name
           server_name = parts[1]
           mcp_server_stats[server_name]["calls"] += 1
   ```
   Use `defaultdict(lambda: {"calls": 0, "errors": 0})` for MCP stats. Track errors on PostToolUse (same pattern as existing tool error tracking).

3. **Installed plugins** — new function `scan_installed_plugins()`:
   ```python
   def scan_installed_plugins(
       settings_file: Path | None = None,
   ) -> list[str]:
       """Read installed plugins from Claude settings."""
       if settings_file is None:
           settings_file = Path.home() / ".claude" / "settings.json"

       if settings_file.exists():
           try:
               settings = json.loads(settings_file.read_text())
               plugins = settings.get("enabledPlugins", {})
               if isinstance(plugins, dict):
                   return sorted(plugins.keys())
           except (json.JSONDecodeError, OSError):
               pass

       return []
   ```
   Path parameter enables testability without mocking filesystem.

**Output** — stats.json gains new top-level keys:
```json
{
  "generated": "...",
  "total_events": 1234,
  "tools": { ... },
  "edit_without_read_count": 5,
  "model": "claude-sonnet-4-5",
  "skills": { "superpowers:brainstorming": { "calls": 10 } },
  "mcp_servers": { "chrome-devtools": { "calls": 15, "errors": 2 } },
  "installed_plugins": ["tool-time@interagency-marketplace", "superpowers@superpowers-marketplace"]
}
```

**Tests to add**:
- Skill aggregation from events with `skill` field
- MCP server parsing: standard `mcp__server__tool`, edge cases (`mcp____tool` empty server, `mcp__server` only 2 parts)
- MCP server error tracking (PostToolUse with error on mcp__ tool)
- Plugin scanning: normal settings.json, missing file, malformed JSON, empty enabledPlugins, non-dict enabledPlugins
- Plugin scanning with custom path parameter (testability)

### PR 2: upload.py + Worker API + migration

#### 2a. upload.py — extend anonymized payload

**Files**: `upload.py`, `test_upload.py`

Extend `anonymize()` (currently lines 47–80) allowlist:

```python
payload = {
    # ... existing fields ...
    "skills": {
        name: s.get("calls", 0)
        for name, s in stats.get("skills", {}).items()
    },
    "mcp_servers": {
        name: {"calls": m.get("calls", 0), "errors": m.get("errors", 0)}
        for name, m in stats.get("mcp_servers", {}).items()
    },
    "installed_plugins": stats.get("installed_plugins", []),
}
```

### Privacy Notes

**Plugin fingerprinting risk**: Full plugin lists create quasi-identifiers. The server stores plugins in an **aggregate-only table** (no per-submission linkage), so individual plugin lists can't be reconstructed from the database.

**Skill names are safe**: Public identifiers (e.g., `superpowers:brainstorming`).
**MCP server names are safe**: Extracted from tool name prefix, no custom paths exposed.
**No new PII**: No arguments, file paths, project names, or error messages added.

**Tests to add**:
- New fields present in anonymized output
- Missing fields gracefully default to empty
- No leakage of non-allowlisted fields

#### 2b. D1 migration — new tables

**File**: `community/migrations/002_add_ecosystem_tables.sql`

```sql
-- Skill usage stats (per-submission)
CREATE TABLE skill_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) > 0 AND length(name) <= 100),
  calls INTEGER NOT NULL DEFAULT 0,
  UNIQUE(submission_id, name)
);

-- MCP server usage stats (per-submission)
CREATE TABLE mcp_server_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) > 0 AND length(name) <= 100),
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  UNIQUE(submission_id, name)
);

-- Plugin aggregate (no per-submission linkage — privacy protection)
CREATE TABLE plugin_usage_aggregate (
  plugin_name TEXT PRIMARY KEY CHECK (length(plugin_name) > 0 AND length(plugin_name) <= 100),
  install_count INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT
);

-- One index per table for the aggregation JOIN
CREATE INDEX idx_skill_stats_submission ON skill_stats(submission_id);
CREATE INDEX idx_mcp_server_stats_submission ON mcp_server_stats(submission_id);
```

**Data integrity notes:**
- **UNIQUE constraints** prevent duplicate rows that would corrupt aggregation.
- **ON DELETE CASCADE** handles GDPR deletion for skills/MCP data.
- **No per-submission plugin linkage**: `plugin_usage_aggregate` is a counter table. GDPR deletion can't remove individual plugin contributions (acceptable — aggregates aren't PII).
- All additive — existing data untouched.

#### 2c. API routes — extend submit + GET /stats

**File**: `community/src/index.ts`

**Extend SubmitSchema** (line ~23):
```typescript
const SKILL_NAME_RE = /^[a-zA-Z0-9_.\-:]+$/;
const MCP_SERVER_NAME_RE = /^[a-zA-Z0-9_.\-:]+$/;
const PLUGIN_NAME_RE = /^[a-zA-Z0-9_.\-:/@]+$/;

skills: z.record(
  z.string().regex(SKILL_NAME_RE).max(100),
  z.number().int().min(0).max(100000)
).max(50).optional(),

mcp_servers: z.record(
  z.string().regex(MCP_SERVER_NAME_RE).max(100),
  z.object({
    calls: z.number().int().min(0).max(100000),
    errors: z.number().int().min(0).max(100000),
  })
).max(50).optional(),

installed_plugins: z.array(z.string().regex(PLUGIN_NAME_RE).max(100))
  .max(100)
  .optional(),
```

All optional for backward compat with old upload.py clients.

**Extend submit route** — single atomic batch:
```typescript
const allStmts: D1PreparedStatement[] = [];

// Existing tool_stats inserts
for (const [name, stats] of Object.entries(data.tools)) {
  allStmts.push(c.env.DB.prepare(
    "INSERT INTO tool_stats (...) VALUES (?, ?, ?, ?, ?)"
  ).bind(row.id, name, stats.calls, stats.errors, stats.rejections));
}

// Skills
if (data.skills) {
  for (const [name, calls] of Object.entries(data.skills)) {
    allStmts.push(c.env.DB.prepare(
      "INSERT INTO skill_stats (submission_id, name, calls) VALUES (?, ?, ?)"
    ).bind(row.id, name, calls));
  }
}

// MCP servers
if (data.mcp_servers) {
  for (const [name, stats] of Object.entries(data.mcp_servers)) {
    allStmts.push(c.env.DB.prepare(
      "INSERT INTO mcp_server_stats (submission_id, name, calls, errors) VALUES (?, ?, ?, ?)"
    ).bind(row.id, name, stats.calls, stats.errors));
  }
}

// Plugins → aggregate table (no submission linkage)
if (data.installed_plugins) {
  for (const plugin of data.installed_plugins) {
    allStmts.push(c.env.DB.prepare(
      `INSERT INTO plugin_usage_aggregate (plugin_name, install_count, last_seen)
       VALUES (?, 1, datetime('now'))
       ON CONFLICT(plugin_name) DO UPDATE SET
         install_count = install_count + 1,
         last_seen = datetime('now')`
    ).bind(plugin));
  }
}

// Single atomic batch
if (allStmts.length > 0) {
  await c.env.DB.batch(allStmts);
}
```

**Extend GET /v1/api/stats** — always return all dimensions:

```typescript
app.get("/v1/api/stats", async (c) => {
  // ... existing tools/overview/models queries ...

  // Skills
  const skills = await c.env.DB.prepare(`
    SELECT ss.name,
           SUM(ss.calls) as total_calls,
           COUNT(DISTINCT s.submission_token) as unique_submitters
    FROM skill_stats ss
    JOIN submissions s ON s.id = ss.submission_id
    WHERE s.submitted_at >= datetime('now', '-7 days')
    GROUP BY ss.name
    HAVING COUNT(DISTINCT s.submission_token) >= 10
    ORDER BY total_calls DESC
    LIMIT 50
  `).all();

  // MCP servers
  const mcpServers = await c.env.DB.prepare(`
    SELECT ms.name,
           SUM(ms.calls) as total_calls,
           SUM(ms.errors) as total_errors,
           COUNT(DISTINCT s.submission_token) as unique_submitters
    FROM mcp_server_stats ms
    JOIN submissions s ON s.id = ms.submission_id
    WHERE s.submitted_at >= datetime('now', '-7 days')
    GROUP BY ms.name
    HAVING COUNT(DISTINCT s.submission_token) >= 10
    ORDER BY total_calls DESC
    LIMIT 50
  `).all();

  // Plugins (aggregate table, enforce ≥10 threshold)
  const plugins = await c.env.DB.prepare(`
    SELECT plugin_name, install_count
    FROM plugin_usage_aggregate
    WHERE install_count >= 10
    ORDER BY install_count DESC
    LIMIT 50
  `).all();

  return c.json({
    overview, tools, models,
    dimensions: {
      skills: skills.results ?? [],
      mcp_servers: mcpServers.results ?? [],
      plugins: plugins.results ?? [],
    },
  });
});
```

**Namespaced under `dimensions`** to prevent key collisions with existing top-level keys (`tools`, `overview`, etc.).

**Error handling** — use `unknown` type:

```typescript
} catch (e: unknown) {
  console.error("POST /v1/api/submit failed:", e);
  if (e instanceof Error && e.message.includes("UNIQUE constraint")) {
    return c.json({ error: "Duplicate submission" }, 409);
  }
  return c.json({ error: "Server error" }, 500);
}
```

### PR 3: Dashboard — single page with sections

**Files**: `community/public/index.html`, `community/public/dashboard.js`

Single-page layout with sections. Total payload is <50KB. No tabs, no lazy loading.

**HTML** — add chart sections below existing charts:
```html
<section id="skills-section">
  <h2>Top Skills</h2>
  <canvas id="skills-chart"></canvas>
</section>
<section id="mcp-section">
  <h2>Top MCP Servers</h2>
  <canvas id="mcp-chart"></canvas>
</section>
<section id="plugins-section">
  <h2>Top Plugins</h2>
  <canvas id="plugins-chart"></canvas>
</section>
```

**JS** — single fetch, render all:
```javascript
async function loadDashboard() {
  const resp = await fetch("/v1/api/stats");
  const data = await resp.json();

  renderToolsChart(data.tools);
  renderErrorsChart(data.tools);
  renderModelsChart(data.models);

  if (data.dimensions.skills?.length) renderSkillsChart(data.dimensions.skills);
  if (data.dimensions.mcp_servers?.length) renderMcpChart(data.dimensions.mcp_servers);
  if (data.dimensions.plugins?.length) renderPluginsChart(data.dimensions.plugins);
}
```

Sections hidden if no data. No empty "coming soon" noise.

### Deferred to v2

- **MCP per-tool drill-down** — requires collecting per-tool stats end-to-end; add when there's demand
- **Source field** (claude-code vs codex) — only one source exists; add when Codex upload ships
- **Correlation data** (skill-to-tool pairs) — needs data volume
- **Ecosystem map / D3 visualization** — needs correlation data
- **OpenClaw integration** — needs their tool lifecycle hooks to ship
- **backfill.py upload support** — deferred until Codex data volume justifies it

## Acceptance Criteria

### Functional Requirements
- [ ] `summarize.py` outputs skills, MCP servers, and installed plugins in stats.json
- [ ] `upload.py` includes new fields in anonymized payload
- [ ] Community API accepts submissions with new fields (and without, for backward compat)
- [ ] `GET /v1/api/stats` returns dimensions (skills, MCP servers, plugins) alongside existing data
- [ ] Plugin data stored in aggregate-only table (no per-submission linkage)
- [ ] Dashboard shows skills, MCP servers, and plugins sections
- [ ] Old upload.py clients still work (new fields optional)
- [ ] GDPR deletion cascades for skill_stats/mcp_server_stats, aggregates unaffected
- [ ] All dimensions suppressed below ≥10 unique submitters threshold

### Testing
- [ ] `test_summarize.py`: skill aggregation, MCP parsing (standard + edge cases), plugin scanning (normal + error cases)
- [ ] `test_upload.py`: new fields in payload, backward compat, no field leakage
- [ ] Manual: deploy to staging, submit test data, verify dashboard sections

## Dependencies & Risks

| Risk | Mitigation |
|------|------------|
| `~/.claude/settings.json` schema changes | Read defensively with try/except; test with custom path param |
| MCP tool names with non-standard patterns | Strict `mcp__` prefix + `len(parts) >= 3 and parts[1]` guard; unmatched names stay in regular tool stats |
| Plugin fingerprinting via combinations | Aggregate-only table, no per-submission linkage |
| D1 write quota at scale | Single batch transaction; upgrade to paid plan ($5/mo) when needed |

## Security Checklist

- [ ] Plugin tracking uses aggregate-only table (no per-submission lists stored)
- [ ] All name fields validated with regex before DB insert
- [ ] All public queries have ≥10 submitters HAVING clause
- [ ] Error responses use `unknown` type, not `any`
- [ ] Dashboard renders with `textContent` (no innerHTML)
- [ ] Batch size limits prevent DoS via large payloads

## References

- **Brainstorm**: `docs/brainstorms/2026-02-01-ecosystem-observatory-brainstorm.md`
- **Plugin data source**: `~/.claude/settings.json` (enabledPlugins)
- **Existing patterns**: `summarize.py:64-131` (aggregation), `upload.py:47-80` (anonymization), `community/src/index.ts:34-104` (submit route)
- **Prior plan**: `docs/plans/2026-01-30-feat-community-analytics-dashboard-plan.md` (privacy learnings)
- **Plan review**: dhh-rails-reviewer (cut MCP drill-down, cut dimension filtering, 3 PRs not 7 phases), code-simplicity-reviewer (separate tables, cut skill errors, cut extra indexes), kieran-rails-reviewer (naming fixes, dead CHECK values, namespace API response)
