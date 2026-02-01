---
name: tool-time
description: Analyze tool usage patterns and suggest improvements to your workflow
triggers:
  - tool stats
  - tool usage
  - tool-time
  - show patterns
  - analyze tools
  - delete my data
  - remove my data
  - forget me
user_invocable: true
---

# tool-time: Tool Usage Analysis

You are analyzing 7 days of tool usage data for the current project. Your job is to find problems and offer to fix them — not narrate numbers.

## Data

Run `python3 $CLAUDE_PLUGIN_ROOT/summarize.py` then read `~/.claude/tool-time/stats.json`.

The file contains:
- `total_events`: total tool calls in the period
- `tools`: per-tool `{calls, errors, rejections}` — rejections are user denials, errors are tool failures
- `edit_without_read_count`: how many times Edit was called on a file not previously Read in that session

## Analysis

Look for these signals (not an exhaustive list — use your judgment):

- **Tools with error rates above ~10%** — what's failing and why?
- **Tools with high rejection rates** — the agent is doing something the user doesn't want
- **Edit-without-read > 0** — the agent is editing files it hasn't read, leading to blind edits
- **Bash dominance (>50% of calls)** — but only a concern if the Bash calls are doing file reads/searches (cat, grep, find, head, tail) that dedicated tools handle better. Bash calls for git, test runners, script execution, and deployment are legitimate and should not be flagged.
- **Low tool diversity** — are available tools being underutilized?

Then read the project's CLAUDE.md (if it exists):
- Does it already address the problems you found? If so, the rules aren't working.
- Is it missing guidance that would prevent the patterns you see?

Optionally check AGENTS.md for similar gaps.

## Output

Present findings as a short bulleted list. For each finding:
- What the data shows (concrete numbers)
- Why it matters
- What to do about it

Then offer to apply fixes. Use Edit to update CLAUDE.md or AGENTS.md directly, with user approval. Don't just suggest — propose the exact text.

If the data looks healthy, say so briefly and stop. Don't invent problems.

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
4. Run: `curl -s -X DELETE "https://tool-time-api.mistakeknot.workers.dev/v1/api/user/<token>"`
5. Report the result
6. Offer to also set `community_sharing` to `false` in config.json to stop future uploads
