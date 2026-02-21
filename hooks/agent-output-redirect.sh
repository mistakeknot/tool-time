#!/usr/bin/env bash
# Agent output redirect for multi-agent workflows
# Only fires on PreToolUse:Task (via matcher in hooks.json)
# When a multi-agent skill launches Task agents, injects "save to file, return summary"
# instructions to prevent context exhaustion.
# NOTE: Must run as the LAST PreToolUse hook due to Claude Code bug #15897:
# updatedInput from earlier hooks gets silently overwritten by later hooks.
set -euo pipefail

INPUT=$(cat)

TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""')
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // ""')

# Safety: only act on PreToolUse + Task (matcher should guarantee this, but be safe)
[[ "$EVENT" == "PreToolUse" && "$TOOL" == "Task" ]] || exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // ""')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // ""')
PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // ""')
TASK_DESC=$(echo "$INPUT" | jq -r '.tool_input.description // ""')
ORIGINAL_INPUT=$(echo "$INPUT" | jq '.tool_input')

# Detect multi-agent workflow via transcript keywords or marker file
MULTI_AGENT=false
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  if tail -c 5000000 "$TRANSCRIPT" | grep 'deepen-plan\|plan_review\|plan-review\|workflows:review\|flux-drive' >/dev/null 2>&1; then
    MULTI_AGENT=true
  fi
fi
if [ -f "$CWD/docs/research/.active" ]; then
  MULTI_AGENT=true
fi

[[ "$MULTI_AGENT" == "true" ]] || exit 0

# Skip if prompt already has file-save instructions
if echo "$PROMPT" | grep -qi 'write.*output.*to.*file\|save.*results\|save.*analysis\|write.*analysis.*to\|docs/research/' 2>/dev/null; then
  exit 0
fi

# Determine output directory
RESEARCH_DIR="$CWD/docs/research"
if [ -f "$CWD/docs/research/.active" ]; then
  ACTIVE_DIR=$(cat "$CWD/docs/research/.active" 2>/dev/null || echo "")
  if [ -n "$ACTIVE_DIR" ] && [ -d "$CWD/$ACTIVE_DIR" ]; then
    RESEARCH_DIR="$CWD/$ACTIVE_DIR"
  fi
fi

SAFE_NAME=$(echo "$TASK_DESC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-\+/-/g' | sed 's/^-//;s/-$//' | head -c 80)
[ -z "$SAFE_NAME" ] && SAFE_NAME="agent-$(date +%s)"
OUTPUT_FILE="$RESEARCH_DIR/${SAFE_NAME}.md"
mkdir -p "$RESEARCH_DIR"

INJECT="MANDATORY FIRST STEP — You MUST do this BEFORE anything else:
1. Complete your analysis
2. Use the Write tool to save your FULL analysis to: ${OUTPUT_FILE}
3. After writing the file, respond with ONLY a 3-5 line summary that includes the file path and 2-3 key findings.

Your task follows below. Remember: write full output to the file above, return only a summary.
---
"

# updatedInput is REPLACE not merge — must pass ALL original fields
echo "$ORIGINAL_INPUT" | jq \
  --arg prompt "${INJECT}${PROMPT}" \
  '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "allow",
      "updatedInput": (. + {"prompt": $prompt})
    }
  }'
exit 0
