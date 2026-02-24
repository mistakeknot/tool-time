# tool-time

Tool usage analytics for Claude Code, Codex CLI, and OpenClaw.

## What This Does

Hooks capture every tool call in your Claude Code sessions. Transcript parsers extract tool usage from Codex CLI and OpenClaw sessions. A Python script aggregates 7 days of stats. An AI agent reads those stats, spots patterns (high error rates, edit-without-read, Bash overuse), checks your CLAUDE.md for gaps, and proposes concrete fixes.

Optionally, share anonymized stats to a community dashboard at [tool-time.org](https://tool-time.org) and compare your patterns against other users.

## Installation

First, add the [interagency marketplace](https://github.com/mistakeknot/interagency-marketplace) (one-time setup):

```bash
/plugin marketplace add mistakeknot/interagency-marketplace
```

Then install the plugin:

```bash
/plugin install tool-time
```

## Usage

```
/tool-time
```

The agent regenerates your stats, analyzes tool usage patterns for the current project, flags issues, reads your CLAUDE.md/AGENTS.md, and proposes specific edits. It also recommends relevant skills from [playbooks.com](https://playbooks.com) based on your project language.

### What Gets Flagged

- **High error rates** — tools failing 10%+ of the time
- **High rejection rates** — tools the user frequently denies
- **Edit-without-read** — editing files that weren't read first (a surprisingly common source of bugs)
- **Bash dominance** — Bash >50% of calls, but only when Bash is doing file reads (cat, grep, find). Git, test runners, and deployment scripts are legitimate Bash usage.
- **Low tool diversity** — underutilizing available tools

## How It Works

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
  │
  ├──▶ skill (agent analysis)       ← reads stats + CLAUDE.md, proposes fixes
  │
  └──▶ upload.py (opt-in)           ← anonymized submission to community API
         │
         ▼
       Cloudflare Worker + D1       ← community dashboard
```

`summarize.py` is pure data preparation — no opinions, no thresholds. The agent does the analysis. This split keeps the data pipeline honest and the recommendations adaptable.

## Community Sharing (Opt-In)

Enable in `~/.claude/tool-time/config.json`:

```json
{ "community_sharing": true }
```

**What's shared** (strict allow-list): submission token (random hex, not your identity), timestamp (truncated to hour), per-tool call/error/rejection counts, model name, skill usage, MCP server usage, installed plugins.

**What's NOT shared**: file paths, project names, error messages, skill arguments.

**GDPR deletion**: `/tool-time delete my data` — looks up your token, confirms, deletes all server-side data, offers to disable future uploads.

## Codex CLI + OpenClaw Support

Codex CLI doesn't support hooks, so `backfill.py` parses historical session transcripts from `~/.codex/sessions/`. OpenClaw transcripts are parsed from `~/.openclaw/agents/`, `~/.moltbot/agents/`, and `~/.clawdbot/agents/` (the app was rebranded multiple times; sessions are deduplicated across directories).

## Development

```bash
uv run --with pytest pytest test_summarize.py test_upload.py -v
python3 summarize.py          # Refresh stats manually
python3 backfill.py           # Parse historical transcripts
cd community && npm run dev   # Worker dev server
cd community && npm run deploy # Deploy worker
```

## Credits

Dashboard live at [tool-time.org](https://tool-time.org). API at `tool-time-api.mistakeknot.workers.dev`.

## License

MIT
