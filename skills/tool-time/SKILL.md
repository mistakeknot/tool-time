---
name: tool-time
description: Analyze tool usage patterns and suggest improvements to your workflow
triggers:
  - tool stats
  - tool usage
  - tool-time
  - show patterns
  - analyze tools
  - deep analysis
  - dashboard
  - show charts
  - delete my data
  - remove my data
  - forget me
user_invocable: true
---

# tool-time: Tool Usage Analysis

You are analyzing tool usage data to find problems and offer to fix them — not narrate numbers.

## Step 1: Gather Data

Run both analysis scripts, then read the outputs:

```bash
python3 $CLAUDE_PLUGIN_ROOT/summarize.py
python3 $CLAUDE_PLUGIN_ROOT/analyze.py --timezone America/Los_Angeles
```

Then read both files:
- `~/.claude/tool-time/stats.json` (7-day project-scoped stats)
- `~/.claude/tool-time/analysis.json` (90-day deep analytics)

After reading, run `python3 $CLAUDE_PLUGIN_ROOT/upload.py` to upload the latest stats (only sends if community sharing is enabled).

## Step 2: Auto-Detect Mode

Check `analysis.json` field `event_count`:
- **≥500 events**: Use **deep analysis** mode (this file's full instructions)
- **<500 events**: Use **basic mode** — present only stats.json findings (skip deep analysis sections)
- **<100 events**: Tell the user: "Only N events — need more sessions for meaningful analysis. Try again after a few more days of use."

## Step 3: Dashboard Shortcut

If the user's trigger matches "dashboard", "show charts", or "visual":
- Skip text analysis
- Run: `bash $CLAUDE_PLUGIN_ROOT/local-dashboard/serve.sh`
- Print the URL and stop

## Step 4: Deep Analysis (diagnostic-first order)

Present findings in this priority order. Skip any section that has no actionable findings — never pad with filler.

### 4a. Retry Patterns (most actionable)

From `analysis.json → tool_chains.retry_patterns`:
- Report tools with retries: "Edit retried 1.1x avg, max 3 in one session (14 sessions affected)"
- If retries are high (avg >2), suggest CLAUDE.md rules to improve first-attempt success
- If no retries: skip this section entirely (healthy signal, not worth mentioning)

### 4b. Tool Chain Problems

From `analysis.json → tool_chains.bigrams`:
- Look for anti-patterns in transitions:
  - `Read → Bash` (if Bash is doing cat/grep/find → should use Read/Grep/Glob)
  - `Edit → Edit` same file (repeated failures → improve context in CLAUDE.md)
  - High self-loop counts for any tool (indicates iteration/retry loops)
- Report the top 3-5 most interesting transitions with counts
- If bigrams look healthy (diverse, no loops): skip

### 4c. Session Classification

From `analysis.json → sessions.classifications`:
- Report the breakdown: "60% building, 20% debugging, 10% exploring..."
- Only flag if the mix is surprising:
  - Debugging >30% → "A third of sessions are debugging — check for recurring error patterns"
  - Exploring >40% → "Lots of exploration — might benefit from better AGENTS.md docs"
  - Planning very low → normal, don't flag
- If the mix is unremarkable: one sentence summary, move on

### 4d. Source Comparison (only if multiple sources)

From `analysis.json → by_source`:
- If only 1 source (or only "unknown"): skip entirely
- If multiple: compare error rates, avg tools/session across clients
- Flag significant differences (>2x): "Codex sessions have 2.3x higher error rate — check AGENTS.md for Codex-specific instructions"

### 4e. Time Patterns (only if revealing)

From `analysis.json → time_patterns`:
- Only report if there's a clear signal:
  - Error rate at `most_error_prone_hour` is >2x the average → "Error rate spikes 3x at 11pm — you may be tired"
  - Extreme concentration (>80% of events in 4 hours) → notable but not actionable
- If patterns are unremarkable: skip

### 4f. Trends (week-over-week)

From `analysis.json → trends`:
- Report meaningful changes:
  - Error rate trend (improving/worsening over last 4 weeks)
  - Tool mix shifting (e.g., "Bash usage dropped from 60% to 40% — good, using dedicated tools more")
  - Session count trends (ramping up/down)
- If no clear trend: skip

## Step 5: Basic Stats Signals

Also check `stats.json` for the standard signals (these apply in both modes):

- **Error rates above ~10%** — what's failing and why?
- **High rejection rates** — the agent is doing something the user doesn't want
- **Edit-without-read > 0** — the agent is editing files it hasn't read
- **Bash dominance (>50%)** — only flag if Bash is doing file reads/searches. Bash for git, tests, deploy is fine.
- **Low tool diversity** — are available tools being underutilized?

## Step 6: CLAUDE.md/AGENTS.md Review

Read the project's CLAUDE.md (if it exists):
- Does it already address the problems you found? If so, the rules aren't working.
- Is it missing guidance that would prevent the patterns you see?

Optionally check AGENTS.md for similar gaps.

## Step 7: Output

Present findings as a short bulleted list. For each finding:
- What the data shows (concrete numbers)
- Why it matters
- What to do about it

Then offer to apply fixes. Use Edit to update CLAUDE.md or AGENTS.md directly, with user approval. Don't just suggest — propose the exact text.

If the data looks healthy, say so briefly and stop. Don't invent problems.

**Dashboard prompt** (always include at the end if in deep analysis mode):
"For visual exploration (Sankey diagram, heatmap, trend charts), run: `bash $CLAUDE_PLUGIN_ROOT/local-dashboard/serve.sh`"

## Community Comparison (if enabled)

Check `~/.claude/tool-time/config.json` — if `community_sharing` is true, also compare local stats to community baselines:

1. Fetch `https://tool-time-api.mistakeknot.workers.dev/v1/api/stats`
2. Compare local error rates to community averages
3. Flag tools where local error rate is >2x the community average
4. Note tools the community uses heavily that the user doesn't use at all

## Skill Recommendations

After analysis, suggest relevant skills from the playbooks.com directory:

1. Detect the project's primary language by checking for:
   - `package.json` or `tsconfig.json` → TypeScript/JavaScript
   - `pyproject.toml`, `setup.py`, or `requirements.txt` → Python
   - `Gemfile` or `*.gemspec` → Ruby
   - `go.mod` → Go
   - `Cargo.toml` → Rust
   - `*.swift` or `Package.swift` → Swift

2. Build 1-2 search queries combining the language with patterns from the data:
   - If error rates are high → search for "testing" or "debugging" skills
   - If no test runner usage detected → search for testing skills in that language
   - If heavy web/API usage → search for relevant API/fetch skills
   - Default: search for the primary language name

3. Fetch from the API:
   `https://playbooks.com/api/skills?search=<query>&limit=5`

4. Filter results to only show skills that are relevant to the project (use judgment — skip generic or unrelated results).

5. Present as a short list:
   - Skill name and one-line description
   - Install: `playbooks.com/skills/<repoOwner>/<repoName>/<skillSlug>`
   - Why it's relevant based on the user's data

If no relevant skills are found, skip this section entirely. Don't force recommendations.

## Data Deletion

If the user asks to delete their community data (triggers: "delete my data", "remove my data", "forget me", "GDPR delete"):

1. Read `~/.claude/tool-time/config.json` to get the `submission_token`
2. If no token exists, tell the user they have no community data to delete
3. Show the user their token and confirm they want to proceed
4. Run: `curl -s -X POST "https://tool-time-api.mistakeknot.workers.dev/v1/api/user/delete" -H "Content-Type: application/json" -d '{"submission_token": "<token>"}'`
5. Report the result
6. Offer to also set `community_sharing` to `false` in config.json to stop future uploads
