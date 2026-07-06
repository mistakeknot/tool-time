#!/usr/bin/env bash
# tool-time event logger
# Reads hook JSON from stdin, appends one JSONL line to events.jsonl
# On SessionStart, emits the maintenance digest (if fresh) as session context
# On SessionEnd, refreshes stats, always runs maintenance, uploads on success
set -uo pipefail
trap 'exit 0' ERR

DATA_DIR="$HOME/.claude/tool-time"
EVENTS_FILE="$DATA_DIR/events.jsonl"
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$DATA_DIR"

# Read stdin once
INPUT=$(cat)

EVENT_NAME=$(echo "$INPUT" | jq -r '.hook_event_name // ""')

# SessionEnd has no tool to log — handle it specially: refresh stats, run
# maintenance (orphan seq GC + digest refresh), queue the upload, then exit
# before the per-tool JSONL write below. Without this branch, SessionEnd would
# either be dropped (pre-sylveste-63ha bug) or pollute events.jsonl with
# empty-tool entries.
if [ "$EVENT_NAME" = "SessionEnd" ]; then
  SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')
  [ -n "$SESSION_ID" ] && rm -f "$DATA_DIR/.seq-${SESSION_ID}"
  # Only the community upload is gated on a successful stats refresh
  if python3 "$PLUGIN_ROOT/summarize.py" 2>/dev/null; then
    python3 "$PLUGIN_ROOT/upload.py" </dev/null >/dev/null 2>&1 &
  fi
  # Maintenance runs UNCONDITIONALLY: maintain.py owns the digest tripwire
  # that reports a broken pipeline, so gating it on summarize.py success would
  # silence the very failure it exists to surface.
  # GC seq files orphaned by sessions that never fired SessionEnd
  find "$DATA_DIR" -maxdepth 1 -name '.seq-*' -mtime +7 -delete 2>/dev/null || true
  # Refresh digest.txt (served back by the SessionStart branch below)
  python3 "$PLUGIN_ROOT/maintain.py" 2>/dev/null || true
  exit 0
fi

# SessionStart has no tool to log either — instead, surface the maintenance
# digest as session context (SessionStart hook stdout is attached to the new
# session). Only a fresh (<48h), non-empty digest is worth attaching.
if [ "$EVENT_NAME" = "SessionStart" ]; then
  DIGEST_FILE="$DATA_DIR/digest.txt"
  if [ -s "$DIGEST_FILE" ] && [ -n "$(find "$DIGEST_FILE" -mmin -2880 2>/dev/null)" ]; then
    cat "$DIGEST_FILE"
  fi
  exit 0
fi

# Skip remaining non-tool events early — they have no tool to log and tripping
# the SEQ-file logic on those caused stderr noise (saves ~789b of
# session-prefix attachment per session).
case "$EVENT_NAME" in
  PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop) ;;
  *) exit 0 ;;
esac

# Session id is the only field that must transit a shell variable (seq-file
# logic below); extracting a single field has no shift risk. Everything else
# stays inside jq — the old multi-field newline-delimited extract collapsed
# consecutive delimiters on empty fields via 'read -a', shifting later values
# into the wrong keys (Read file paths landed in "skill" in production).
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

# Sequence counter per session. Read-increment-write is NOT atomic: ids are
# unique only under serial hook execution — parallel tool calls can produce
# duplicate ids, and downstream consumers tolerate collisions, so no locking.
SEQ_FILE="$DATA_DIR/.seq-${SESSION_ID}"
if [ -f "$SEQ_FILE" ]; then
  SEQ=$(( $(cat "$SEQ_FILE") + 1 ))
else
  SEQ=1
fi
echo "$SEQ" > "$SEQ_FILE"

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ID="${SESSION_ID}-${SEQ}"

# Build the complete JSONL line in a SINGLE jq pass over the original hook
# JSON. skill/file/model keys are included only when non-empty; the PostToolUse
# error heuristic (stringified result matching error|Error|ERROR -> first
# 200 chars, newlines to spaces; else null) lives here too so no logged field
# ever transits the shell. Claude Code PostToolUse payloads carry the result
# in tool_response; tool_result is kept as a fallback for other clients.
LINE=$(echo "$INPUT" | jq -c \
  --arg id "$ID" \
  --arg ts "$TS" \
  '{v:1, id:$id, ts:$ts,
    event:(.hook_event_name // ""),
    tool:(.tool_name // ""),
    project:(.cwd // ""),
    error:((.tool_response // .tool_result) as $tr |
           if .hook_event_name == "PostToolUse" and $tr
              and (($tr | tostring) | test("error|Error|ERROR"))
           then (($tr | tostring) | gsub("\n"; " ") | .[0:200])
           else null end),
    source:"claude-code"}
   + (if (.tool_input.skill // "") != "" then {skill:.tool_input.skill} else {} end)
   + (if (.tool_input.file_path // .tool_input.path // "") != "" then {file:(.tool_input.file_path // .tool_input.path)} else {} end)
   + (if (.model // "") != "" then {model:.model} else {} end)')

# Atomic single-line write (under PIPE_BUF)
echo "$LINE" >> "$EVENTS_FILE"

# (SessionStart and SessionEnd are handled by the early-return branches above
# — sylveste-63ha)
