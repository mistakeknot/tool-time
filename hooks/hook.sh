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
  '{v:($v|tonumber), id:$id, ts:$ts, event:$event, tool:$tool, project:$project, error:$error}
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
