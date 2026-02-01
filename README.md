# tool-time

Tool usage analytics for Claude Code — tracks every tool call, detects inefficiencies, and suggests fixes.

## What it does

Hooks capture every tool call in your Claude Code sessions. A Python script aggregates 7 days of stats. An AI agent skill reads those stats, spots patterns (high error rates, edit-without-read, Bash overuse), checks your CLAUDE.md for gaps, and proposes concrete fixes.

Optionally, you can share anonymized stats to a community dashboard and compare your patterns against other users.

## Install

```bash
claude plugin:add github.com/mistakeknot/tool-time
```

## Usage

In any project, invoke the skill:

```
/tool-time
```

The agent will:

1. Regenerate your stats from the last 7 days
2. Analyze tool usage patterns for your current project
3. Flag issues (error rates, rejections, edit-without-read, Bash dominance, low diversity)
4. Read your CLAUDE.md/AGENTS.md and propose specific edits
5. Recommend relevant skills from [playbooks.com](https://playbooks.com) based on your project language

## How it works

```
hooks (PreToolUse, PostToolUse, SessionStart, SessionEnd)
  │
  ▼
~/.claude/tool-time/events.jsonl    ← one JSONL line per event
  │
  ▼
summarize.py                        ← pure data aggregation (7-day window, per-project)
  │
  ▼
~/.claude/tool-time/stats.json      ← calls, errors, rejections per tool
  │                                    edit-without-read count, model
  ├──▶ skill (agent analysis)       ← reads stats + CLAUDE.md, proposes fixes
  │
  └──▶ upload.py (opt-in)           ← anonymized submission to community API
         │
         ▼
       Cloudflare Worker + D1       ← community dashboard
```

### Hooks

Four lifecycle hooks capture tool usage:

| Hook | When | What it logs |
|------|------|-------------|
| `PreToolUse` | Before any tool executes | tool name, project, session, model |
| `PostToolUse` | After tool execution | tool name, errors (first 200 chars) |
| `SessionStart` | Session begins | session ID, project |
| `SessionEnd` | Session ends | triggers stats generation + upload |

Each event is a JSONL line with: version, event ID, timestamp, event type, tool name, project path, error (if any), skill, file path, and model.

Events are stored at `~/.claude/tool-time/events.jsonl`.

### Analysis

The skill runs `summarize.py` to produce `stats.json`, then the agent looks for:

- **High error rates** — tools failing ~10%+ of the time
- **High rejection rates** — tools the user frequently denies
- **Edit-without-read** — editing files that weren't read first in the same session
- **Bash dominance** — Bash >50% of calls, but only if Bash is doing file reads (cat, grep, find, head, tail). Git, test runners, and deployment scripts are legitimate.
- **Low tool diversity** — underutilizing available tools

The agent then reads your project's CLAUDE.md and AGENTS.md, identifies gaps, and proposes specific additions or edits.

### Community sharing (opt-in)

Enable by setting `community_sharing: true` in `~/.claude/tool-time/config.json`:

```json
{
  "community_sharing": true
}
```

**What is shared** (strict allow-list):

- Submission token (random 32-char hex, not tied to your identity)
- Timestamp (truncated to hour precision)
- Total event count
- Per-tool stats: calls, errors, rejections
- Edit-without-read count
- Model name

**What is NOT shared**: file paths, project names, error messages, skill arguments.

**GDPR deletion**: Ask the agent to delete your data:

```
/tool-time delete my data
```

The agent will look up your submission token, confirm with you, delete all server-side data, and offer to disable future uploads. No need to find tokens or call APIs manually.

### Skill recommendations

The skill detects your project language by checking for `package.json`, `pyproject.toml`, `Gemfile`, `go.mod`, `Cargo.toml`, or `Package.swift`, then queries [playbooks.com](https://playbooks.com) for relevant skills.

## Data and privacy

All data lives under `~/.claude/tool-time/`:

| File | Contents |
|------|----------|
| `events.jsonl` | Raw hook events (local only) |
| `stats.json` | Aggregated stats (local only) |
| `config.json` | Settings: `community_sharing`, `submission_token`, `last_upload_at` |
| `.seq-*` | Per-session sequence counters (ephemeral) |

Nothing leaves your machine unless you opt in to community sharing.

## Community dashboard

**URL**: [tool-time-api.mistakeknot.workers.dev](https://tool-time-api.mistakeknot.workers.dev)

Shows aggregated community data:

- Top 20 tools by usage
- Top 15 tools by error rate
- Model distribution

**API**:
- `GET /v1/api/stats` — aggregated community stats (7-day window)
- `POST /v1/api/submit` — submit anonymized stats
- `DELETE /v1/api/user/:token` — delete all your data

## Development

```bash
# Run tests
uv run --with pytest pytest test_summarize.py test_upload.py -v

# Refresh stats manually
python3 summarize.py

# Parse historical transcripts (Codex CLI support)
python3 backfill.py

# Worker dev server
cd community && npm run dev

# Deploy worker
cd community && npm run deploy
```

## File structure

```
├── hooks/
│   ├── hooks.json          # Hook definitions (4 lifecycle events)
│   └── hook.sh             # Event logger → events.jsonl
├── skills/
│   ├── tool-time/SKILL.md  # Claude Code skill
│   └── tool-time-codex/SKILL.md  # Codex CLI variant
├── community/
│   ├── src/index.ts        # Hono API (Cloudflare Worker)
│   ├── public/             # Dashboard UI (Chart.js)
│   └── migrations/         # D1 database schema
├── summarize.py            # Stats aggregation (7-day, per-project)
├── upload.py               # Anonymous community submission
├── backfill.py             # Parse historical Codex CLI transcripts
├── parsers.py              # Transcript parsers
├── test_summarize.py       # Tests
├── test_upload.py          # Tests
└── .claude-plugin/
    └── plugin.json         # Plugin manifest (v0.3.0)
```

## Codex CLI support

Codex CLI doesn't support hooks, so `backfill.py` parses historical session transcripts from `~/.codex-cli/sessions/` instead. The Codex skill variant (`tool-time-codex`) runs `backfill.py` before analysis. Same stats format, same agent analysis.

## License

MIT
