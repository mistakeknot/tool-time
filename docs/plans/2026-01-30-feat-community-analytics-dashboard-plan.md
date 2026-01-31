---
title: "feat: Community analytics dashboard"
type: feat
date: 2026-01-30
brainstorm: docs/brainstorms/2026-01-30-community-analytics-brainstorm.md
deepened: 2026-01-30
---

# Community Analytics Dashboard

Anonymized, aggregated tool/skill/MCP/model usage data across Claude Code users. Static dashboard + serverless ingestion API + MCP resource for agent access.

## Enhancement Summary

**Deepened on:** 2026-01-30
**Reviewers:** security-sentinel, architecture-strategist, agent-native-reviewer, code-simplicity-reviewer, performance-oracle

### Key Changes from Review
1. **Simplified architecture**: Single combined `/v1/api/stats` endpoint + precomputed daily aggregates instead of 5 live query endpoints
2. **Non-blocking upload**: Background process, never blocks SessionEnd
3. **Rotating tokens**: Monthly rotation instead of stable token (re-identification defense)
4. **Removed "paste your token" feature**: Privacy risk outweighs value (deferred)
5. **Agent-first design**: MCP resources defined before dashboard, with explicit schemas
6. **Retention policy promoted to launch requirement**: 90-day raw data, aggregates kept indefinitely

### New Risks Discovered
- Stable submission tokens enable re-identification via timing + tool combination correlation
- D1 write quota can be exhausted by frequent short sessions (need client-side batching)
- Blocking uploads on SessionEnd create bad UX (P99 >10s on slow connections)

## Overview

tool-time currently operates locally — each user sees only their own stats. This feature adds opt-in community sharing: anonymized usage data uploads to a Cloudflare Worker, gets aggregated in D1, and is displayed on a public dashboard. Agents can also query community trends via HTTP API or MCP resource.

Three audiences:
1. **Plugin/skill authors** — which tools/skills/MCP servers people actually use
2. **Users** — compare their workflow to the community
3. **Platform team** — ecosystem health (error rates, adoption curves)

## Proposed Solution

**Stack:** Cloudflare Pages (static dashboard) + Cloudflare Worker (ingestion + query API) + D1 (SQLite storage)

**Data flow:**
```
SessionEnd hook
  → summarize.py writes stats.json (existing)
  → upload.py reads stats.json, strips identifiers, queues locally
  → background: POST to Worker (non-blocking, fire-and-forget)
  → Worker validates, rate-limits, writes to D1
  → Hourly cron: precompute daily_aggregates table
  → Dashboard loads precomputed stats.json from Worker
  → MCP resource reads same data (cached 5min)
```

## Acceptance Criteria

### Privacy & Consent
- [ ] Opt-in consent prompt on first `/tool-time` skill invocation (not SessionEnd — user should see tool-time first)
- [ ] Consent stored in `~/.claude/tool-time/config.json` with version tracking
- [ ] No data transmitted until user explicitly opts in
- [ ] Anonymization allowlist: only tool/skill/MCP names + numeric counts + model name survive
- [ ] Everything else stripped: file paths, project names, error messages, skill arguments
- [ ] Submission token rotates monthly (not stable — prevents correlation attacks)
- [ ] Timestamp precision reduced to hour (not second — prevents timing correlation)
- [ ] Metrics with <10 unique submitters suppressed from public endpoints
- [ ] Retention: raw submissions deleted after 90 days, daily aggregates kept indefinitely
- [ ] Privacy policy published at public URL, linked from consent prompt

### Client (upload.py)
- [ ] Reads stats.json, applies anonymization allowlist, writes to local queue
- [ ] Background upload: never blocks SessionEnd (`&` in hook.sh)
- [ ] Token rotation: regenerates `submission_token` monthly in config.json
- [ ] Deduplication: `generated` timestamp (hour precision) + token prevents double-counting
- [ ] Upload batching: only upload if >24h since last upload OR >100 events in session
- [ ] Failed uploads logged to `~/.claude/tool-time/upload.log`, retried next session

### Server (Cloudflare Worker + D1)
- [ ] `POST /v1/api/submit` — validates with Zod, writes to D1 (versioned API from day one)
- [ ] `GET /v1/api/stats` — single combined endpoint returning all aggregated data
- [ ] Rate limiting: 10 submissions/day per token + 50/day per IP (Cloudflare Rate Limiting API)
- [ ] Input validation: tool name regex `^[a-zA-Z0-9_.-]+$`, count bounds 0-100000
- [ ] Reject future timestamps or timestamps >7 days old
- [ ] CORS configured for dashboard domain
- [ ] Cron trigger: daily aggregation into `daily_aggregates` table
- [ ] `DELETE /v1/api/user/:token` — data deletion endpoint (GDPR Article 17)

### Dashboard (Cloudflare Pages)
- [ ] Top tools by usage (bar chart)
- [ ] Error rate distribution by tool
- [ ] Skill adoption (which skills are actually used)
- [ ] MCP server popularity
- [ ] Model distribution (opus vs sonnet vs haiku)
- [ ] All rendering uses textContent (not innerHTML — XSS prevention)
- [ ] Fetches from single `/v1/api/stats` endpoint (precomputed, fast)

### MCP Resources (agent-first design)
- [ ] `tool-time://community/overview` → `{total_submissions, unique_submitters, date_range}`
- [ ] `tool-time://community/tools` → `[{tool_name, unique_submitters, total_calls, error_rate}]`
- [ ] `tool-time://community/skills` → `[{skill_name, invocation_count}]`
- [ ] `tool-time://community/mcp` → `[{mcp_name, usage_count}]`
- [ ] `tool-time://community/models` → `[{model, percentage}]`
- [ ] 5-minute cache on all MCP resource reads

### Agent Tools
- [ ] Agent can compare local stats.json to community averages via MCP resources
- [ ] Skill system prompt documents all available community resources
- [ ] Agent can help user configure consent via config.json guidance in skill

### Hook Changes
- [ ] Add model name to events.jsonl (if available in hook input)
- [ ] Add model field to stats.json output
- [ ] SessionEnd hook calls upload.py as background process: `python3 upload.py &`

## Implementation

### Phase 1: Client-side anonymization + upload

**Files:**
- Create: `upload.py`
- Create: `test_upload.py`
- Edit: `summarize.py` (add model field to stats.json)
- Edit: `hooks/hook.sh` (call upload.py in background on SessionEnd)

**config.json schema:**

```python
{
  "community_sharing": false,          # true after user opts in
  "submission_token": "hex-token",     # random, rotated monthly
  "token_created_at": "2026-01-30T...",
  "consented_at": "2026-01-30T...",
  "consent_version": "v1",
  "last_upload_at": null,              # for batching logic
  "api_endpoint": "https://tool-time-api.workers.dev/v1/api/submit"  # configurable
}
```

**Anonymization** (strict allowlist — only these fields survive):

```python
def anonymize(stats: dict, config: dict) -> dict:
    # Rotate token if >30 days old
    token = maybe_rotate_token(config)

    # Reduce timestamp to hour precision
    generated = stats["generated"][:13] + ":00:00Z"

    return {
        "submission_token": token,
        "generated": generated,
        "total_events": stats["total_events"],
        "tools": {
            name: {"calls": t["calls"], "errors": t["errors"], "rejections": t["rejections"]}
            for name, t in stats.get("tools", {}).items()
        },
        "edit_without_read_count": stats.get("edit_without_read_count", 0),
        "model": stats.get("model"),
    }
```

**Non-blocking upload in hook.sh:**

```bash
# On SessionEnd
if python3 "$PLUGIN_ROOT/summarize.py" 2>/dev/null; then
  python3 "$PLUGIN_ROOT/upload.py" </dev/null >/dev/null 2>&1 &
fi
```

### Phase 2: Cloudflare Worker + D1

**New subdirectory:** `community/` (Cloudflare Worker project)

**Files:**
- Create: `community/wrangler.jsonc`
- Create: `community/src/index.ts` (Hono + Zod)
- Create: `community/migrations/001_create_tables.sql`
- Create: `community/migrations/002_create_daily_aggregates.sql`

**D1 schema:**

```sql
CREATE TABLE submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_token TEXT NOT NULL,
  generated_at DATETIME NOT NULL,
  total_events INTEGER NOT NULL,
  edit_without_read INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(submission_token, generated_at)
);

CREATE TABLE tool_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id),
  tool_name TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  rejections INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_submissions_time ON submissions(submitted_at DESC);
CREATE INDEX idx_submissions_token ON submissions(submission_token);
CREATE INDEX idx_tool_stats_submission ON tool_stats(submission_id);
CREATE INDEX idx_tool_stats_name ON tool_stats(tool_name);

-- Precomputed daily aggregates (populated by cron trigger)
CREATE TABLE daily_aggregates (
  date DATE NOT NULL,
  tool_name TEXT NOT NULL,
  unique_submitters INTEGER NOT NULL,
  total_calls INTEGER NOT NULL,
  total_errors INTEGER NOT NULL,
  total_rejections INTEGER NOT NULL,
  PRIMARY KEY (date, tool_name)
);
```

**Single combined query endpoint:**

```sql
-- GET /v1/api/stats (reads from precomputed daily_aggregates)
SELECT tool_name, SUM(total_calls) as calls, SUM(total_errors) as errors,
       SUM(total_rejections) as rejections, SUM(unique_submitters) as submitters
FROM daily_aggregates
WHERE date >= DATE('now', '-7 days')
GROUP BY tool_name
HAVING SUM(unique_submitters) >= 10
ORDER BY calls DESC
LIMIT 100;
```

### Phase 3: MCP resources + skill update (agent-first)

**Files:**
- Edit: plugin MCP config to expose `tool-time://community/*` resources
- Edit: `skills/tool-time/SKILL.md` — add community comparison guidance
- Edit: `skills/tool-time-codex/SKILL.md` — same

MCP resources proxy the Worker `/v1/api/stats` endpoint with 5-minute caching. Skill system prompt updated:

```markdown
## Community Comparison (if community sharing is enabled)

After analyzing local stats, check community baselines:
1. Read `tool-time://community/overview` to check data availability
2. Read `tool-time://community/tools` for community tool usage
3. Compare local error rates to community averages
4. Flag tools where local rate is >2x community median
```

### Phase 4: Static dashboard

**Files:**
- Create: `community/public/index.html`
- Create: `community/public/dashboard.js`
- Create: `community/public/style.css`

Vanilla HTML/JS + Chart.js. Fetches from single `/v1/api/stats` endpoint (precomputed, fast). All data rendered with textContent (not innerHTML).

## Alternative Approaches Considered

| Approach | Why Rejected |
|----------|-------------|
| FastAPI + Postgres | Overkill for aggregated counts, costs money 24/7 |
| GitHub-based (shared repo) | Slow updates, awkward UX, API limits |
| Differential privacy | Small population makes noise overwhelming; aggregation + thresholds sufficient |
| Opt-out model | Developer community prefers opt-in (VS Code backlash) |
| Static JSON only (no DB) | Considered seriously (simplicity reviewer). Rejected because D1 enables privacy thresholds and data deletion. If adoption is low, revisit. |
| Stable submission token | Re-identification risk via timing/tool correlation. Monthly rotation is safer. |

## Dependencies & Risks

- **Cloudflare account** required for Worker + D1 + Pages deployment
- **Model name** may not be available in hook input JSON — need to verify; fallback to `null`
- **Privacy risk** if anonymization has gaps — strict allowlist enforced client-side AND server-side
- **Adoption risk** — opt-in means slow initial data; may need to seed with own data
- **Abuse** — rate limiting (10/day per token, 50/day per IP) + Zod validation
- **D1 write quota** — client-side batching (upload only if >24h elapsed) mitigates
- **Re-identification** — monthly token rotation + hour-precision timestamps + k>=10 threshold

## Deferred

- "Your stats vs community" dashboard comparison (requires identity — privacy risk)
- Dashboard authentication (user accounts, personal dashboards)
- Trend analysis over time (week-over-week changes)
- Skill/MCP recommendation engine ("users like you also use X")
- Export/download community data
- Webhook notifications for ecosystem changes
- HMAC submission signing (add if abuse becomes real)
- Granular consent (share tools but not skills)

## References

- Brainstorm: `docs/brainstorms/2026-01-30-community-analytics-brainstorm.md`
- Current stats engine: `summarize.py`
- Hook: `hooks/hook.sh:77-81`
- Cloudflare D1 docs: https://developers.cloudflare.com/d1/
- Cloudflare Workers: https://developers.cloudflare.com/workers/
- Cloudflare Rate Limiting API: https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
- VS Code telemetry model: https://code.visualstudio.com/docs/configure/telemetry
- Cline anonymous telemetry: https://cline.bot/blog/introducing-anonymous-telemetry-in-cline
- GDPR anonymization: https://www.gdprsummary.com/anonymization-and-gdpr/
- Hono framework: https://hono.dev/docs/
