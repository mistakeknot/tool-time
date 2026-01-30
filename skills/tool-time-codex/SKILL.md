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

Analyze tool usage patterns from your coding sessions. Codex CLI has no hooks, so data comes from transcript parsing.

## Steps

1. Run `python3 /root/projects/tool-time/backfill.py` to parse recent transcripts
2. Run `python3 /root/projects/tool-time/summarize.py` to refresh stats
3. Read `~/.claude/tool-time/stats.json`
4. Analyze the data and explain what you see
5. If relevant, check the project's CLAUDE.md and AGENTS.md for gaps
6. Offer to apply fixes with user approval
