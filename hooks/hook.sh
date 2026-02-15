#!/usr/bin/env bash
# tool-time event logger
# Reads hook JSON from stdin, appends one JSONL line to events.jsonl
# On SessionEnd, runs summarize.py then upload.py (background)
set -euo pipefail

DATA_DIR="$HOME/.claude/tool-time"
EVENTS_FILE="$DATA_DIR/events.jsonl"
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$DATA_DIR"

# Read stdin once
INPUT=$(cat)

# Extract all fields in a single jq call (newline-delimited for safe parsing)
FIELDS=$(echo "$INPUT" | jq -r '
  [
    (.hook_event_name // ""),
    (.tool_name // ""),
    (.session_id // ""),
    (.cwd // ""),
    (.tool_input.skill // ""),
    (.tool_input.file_path // .tool_input.path // ""),
    (.model // "")
  ] | .[]')

# Read into array (preserves values with spaces)
mapfile -t F <<< "$FIELDS"
EVENT="${F[0]:-}"
TOOL="${F[1]:-}"
SESSION_ID="${F[2]:-}"
CWD="${F[3]:-}"
SKILL="${F[4]:-}"
FILE_PATH="${F[5]:-}"
MODEL="${F[6]:-}"

# Sequence counter per session (atomic via file)
SEQ_FILE="$DATA_DIR/.seq-${SESSION_ID}"
if [ -f "$SEQ_FILE" ]; then
  SEQ=$(( $(cat "$SEQ_FILE") + 1 ))
else
  SEQ=1
fi
echo "$SEQ" > "$SEQ_FILE"

# Extract error info for PostToolUse
ERROR="null"
if [ "$EVENT" = "PostToolUse" ]; then
  HAS_ERROR=$(echo "$INPUT" | jq -r 'if .tool_result and (.tool_result | tostring | test("error|Error|ERROR"; "g")) then "true" else "false" end')
  if [ "$HAS_ERROR" = "true" ]; then
    ERROR=$(echo "$INPUT" | jq -r '.tool_result | tostring | gsub("\n"; " ") | .[0:200]')
    ERROR=$(echo "$ERROR" | jq -Rs '.')
  fi
fi

# Build JSONL line
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ID="${SESSION_ID}-${SEQ}"

# Build JSON — include skill and file only when non-empty
LINE=$(jq -nc \
  --arg v "1" \
  --arg id "$ID" \
  --arg ts "$TS" \
  --arg event "$EVENT" \
  --arg tool "$TOOL" \
  --arg project "$CWD" \
  --argjson error "$ERROR" \
  --arg skill "$SKILL" \
  --arg file "$FILE_PATH" \
  --arg model "$MODEL" \
  --arg source "claude-code" \
  '{v:($v|tonumber), id:$id, ts:$ts, event:$event, tool:$tool, project:$project, error:$error, source:$source}
   + (if $skill != "" then {skill:$skill} else {} end)
   + (if $file != "" then {file:$file} else {} end)
   + (if $model != "" then {model:$model} else {} end)')

# Atomic single-line write (under PIPE_BUF)
echo "$LINE" >> "$EVENTS_FILE"

# On SessionEnd, run optimize.py and clean up seq file
if [ "$EVENT" = "SessionEnd" ]; then
  rm -f "$SEQ_FILE"
  if python3 "$PLUGIN_ROOT/summarize.py" 2>/dev/null; then
    python3 "$PLUGIN_ROOT/upload.py" </dev/null >/dev/null 2>&1 &
  fi
fi

# --- Agent output redirect for multi-agent workflows ---
# When a multi-agent skill (deepen-plan, plan_review, etc.) launches Task agents,
# inject "save to file, return summary" instructions to prevent context exhaustion.
# This MUST run in the last PreToolUse hook due to Claude Code bug #15897:
# updatedInput from earlier hooks gets silently overwritten by later hooks.
if [ "$EVENT" = "PreToolUse" ] && [ "$TOOL" = "Task" ]; then
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

  if [ "$MULTI_AGENT" = "true" ]; then
    # Skip if prompt already has file-save instructions
    if ! echo "$PROMPT" | grep -qi 'write.*output.*to.*file\|save.*results\|save.*analysis\|write.*analysis.*to\|docs/research/' 2>/dev/null; then
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
    fi
  fi
fi
