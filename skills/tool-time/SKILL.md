---
name: tool-time
description: Analyze tool usage patterns and suggest improvements to your workflow
triggers:
  - tool stats
  - tool usage
  - tool-time
  - show patterns
  - analyze tools
user_invocable: true
---

# tool-time: Tool Usage Analysis

Analyze your tool usage patterns and help improve your workflow.

## Steps

1. Run `python3 $CLAUDE_PLUGIN_ROOT/summarize.py` to refresh stats
2. Read `~/.claude/tool-time/stats.json`
3. Analyze the data and explain what you see
4. If relevant, check the project's CLAUDE.md and AGENTS.md for gaps
5. Offer to apply fixes with user approval
