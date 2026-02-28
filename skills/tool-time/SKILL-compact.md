# tool-time: Tool Usage Analysis (compact)

Analyze tool usage patterns across 7-90 days to find problems and offer fixes.

## When to Invoke

User says "tool stats", "tool usage", "tool-time", "analyze tools", "dashboard", "show charts", or asks to delete data.

## Workflow

1. **Gather Data** — run `summarize.py` and `analyze.py --timezone America/Los_Angeles`, read `stats.json` + `analysis.json`, then run `upload.py`.

2. **Auto-Detect Mode** — check `analysis.json` event_count:
   - >=500: deep analysis (full instructions below)
   - <500: basic mode (stats.json only)
   - <100: tell user to accumulate more sessions

3. **Dashboard Shortcut** — if trigger is "dashboard"/"show charts": run `serve.sh`, print URL, stop.

4. **Deep Analysis** (priority order, skip sections with no findings):
   - **Retry patterns** — tools retried >2x avg indicate first-attempt failures
   - **Tool chain problems** — anti-patterns in bigrams (Read->Bash, Edit->Edit loops)
   - **Session classification** — flag debugging >30% or exploring >40%
   - **Source comparison** — if multiple sources, flag >2x error rate differences
   - **Time patterns** — error spikes >2x average at specific hours
   - **Trends** — week-over-week error rate, tool mix, session count changes

5. **Basic Stats Signals** (both modes): error rates >10%, high rejections, edit-without-read, Bash dominance (only if doing file reads), low diversity.

6. **CLAUDE.md Review** — existing rules not working? Missing guidance?

7. **Output** — bulleted findings with numbers, impact, fix. Offer Edit to apply. End with dashboard prompt in deep mode.

## Community Comparison / Skill Recommendations / Data Deletion

Same as Codex variant — compare to community API, suggest playbooks.com skills, handle GDPR deletion via submission_token.

---
*For full deep analysis field references and API endpoints, read SKILL.md.*
