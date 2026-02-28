# tool-time: Tool Usage Analysis — Codex CLI (compact)

Analyze 7 days of Codex CLI tool usage to find problems and offer fixes.

## When to Invoke

User says "tool stats", "tool usage", "tool-time", "show patterns", "analyze tools", or asks to delete their data.

## Workflow

1. **Gather Data**
   - Run `python3 /root/projects/tool-time/backfill.py` (parse transcripts)
   - Run `python3 /root/projects/tool-time/summarize.py` (refresh stats)
   - Read `~/.claude/tool-time/stats.json`

2. **Analyze** — look for actionable signals:
   - Error rates >10% — what's failing?
   - High rejection rates — agent doing unwanted things
   - Edit-without-read >0 — blind edits
   - Bash dominance >50% — should use Read/Edit/Grep instead?
   - Low tool diversity — underutilized tools

3. **Review CLAUDE.md** — does it already address problems found? Missing guidance?

4. **Output** — short bulleted list per finding: data (numbers), why it matters, what to do. Offer to apply fixes via Edit. If healthy, say so and stop.

## Community Comparison (if enabled)

Check `~/.claude/tool-time/config.json` for `community_sharing: true`. Compare local error rates to `https://tool-time-api.mistakeknot.workers.dev/v1/api/stats`. Flag >2x community average.

## Skill Recommendations

Detect project language, build search queries from patterns, fetch `https://playbooks.com/api/skills?search=<query>&limit=5`, present relevant results.

## Data Deletion

Triggers: "delete my data", "forget me", "GDPR delete". Read config for `submission_token`, confirm, DELETE via API, offer to disable `community_sharing`.

---
*For language detection details and full API endpoints, read SKILL.md.*
