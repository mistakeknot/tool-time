---
name: tool-time
description: Analyze tool usage patterns and suggest improvements to your workflow (Codex CLI)
triggers:
  - tool stats
  - tool usage
  - tool-time
  - show patterns
  - analyze tools
install: symlink
install_target: ~/.codex/skills/tool-time/SKILL.md
---

# tool-time: Tool Usage Analysis (Codex CLI)

You are analyzing 7 days of tool usage data for the current project. Your job is to find problems and offer to fix them — not narrate numbers.

Codex CLI has no hooks, so data comes from transcript parsing.

## Data

1. Run `python3 /root/projects/tool-time/backfill.py` to parse recent transcripts
2. Run `python3 /root/projects/tool-time/summarize.py` to refresh stats
3. Read `~/.claude/tool-time/stats.json`

The file contains:
- `total_events`: total tool calls in the period
- `tools`: per-tool `{calls, errors, rejections}` — rejections are user denials, errors are tool failures
- `edit_without_read_count`: how many times Edit was called on a file not previously Read in that session

## Analysis

Look for these signals (not an exhaustive list — use your judgment):

- **Tools with error rates above ~10%** — what's failing and why?
- **Tools with high rejection rates** — the agent is doing something the user doesn't want
- **Edit-without-read > 0** — the agent is editing files it hasn't read, leading to blind edits
- **Bash dominance (>50% of calls)** — may indicate the agent should use Read, Edit, Grep instead
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
