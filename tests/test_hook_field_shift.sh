#!/usr/bin/env bash
# Regression test for the field-shift bug: hook.sh used to extract 7 fields via
# one newline-delimited jq call and read them back with "IFS=$'\n' read -a".
# read -a collapses CONSECUTIVE delimiters, so any EMPTY field (e.g. no skill
# on a Read event) shifted all later fields left — Read file paths were
# recorded under "skill" in production events.jsonl.
#
# Fix: the JSONL line is now built in a single jq pass over the hook JSON, so
# no logged field ever transits a shell variable.

set -eo pipefail

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SCRIPT="$PLUGIN_ROOT/hooks/hook.sh"

# Redirect HOME so the hook writes to a tmp ~/.claude/tool-time
export HOME="$TEST_DIR"
mkdir -p "$HOME/.claude"
DATA_DIR="$HOME/.claude/tool-time"
EVENTS_FILE="$DATA_DIR/events.jsonl"

PASS=0
FAIL=0

assert_eq() {
    local desc="$1" got="$2" expected="$3"
    if [[ "$got" == "$expected" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (got '$got', expected '$expected')"; ((FAIL++)) || true; fi
}

# jq helper: evaluate an expression against the last events.jsonl line
last_line_jq() {
    tail -n 1 "$EVENTS_FILE" | jq -r "$1"
}

echo "=== Group 1: PostToolUse Read with EMPTY skill must not shift fields ==="
echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Read","cwd":"/tmp/proj","model":"claude-opus-4","tool_input":{"skill":"","file_path":"/tmp/proj/notes.txt"}}' | bash "$HOOK_SCRIPT"

assert_eq "event is PostToolUse" "$(last_line_jq '.event')" "PostToolUse"
assert_eq "tool is Read" "$(last_line_jq '.tool')" "Read"
assert_eq "project is cwd" "$(last_line_jq '.project')" "/tmp/proj"
assert_eq "file is the file_path (not shifted)" "$(last_line_jq '.file')" "/tmp/proj/notes.txt"
assert_eq "model survives the empty skill field" "$(last_line_jq '.model')" "claude-opus-4"
assert_eq "NO skill key when skill is empty" "$(last_line_jq 'has("skill")')" "false"
assert_eq "error is null (no error text in result)" "$(last_line_jq '.error')" "null"
assert_eq "schema version is 1" "$(last_line_jq '.v')" "1"
assert_eq "source is claude-code" "$(last_line_jq '.source')" "claude-code"

echo ""
echo "=== Group 2: Skill event records skill correctly ==="
echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Skill","cwd":"/tmp/proj","tool_input":{"skill":"commit"}}' | bash "$HOOK_SCRIPT"

assert_eq "skill recorded from tool_input.skill" "$(last_line_jq '.skill')" "commit"
assert_eq "no file key when no path in tool_input" "$(last_line_jq 'has("file")')" "false"
assert_eq "no model key when model absent" "$(last_line_jq 'has("model")')" "false"
assert_eq "seq counter advanced (id = session-seq)" "$(last_line_jq '.id')" "shift-session-2"

echo ""
echo "=== Group 3: PostToolUse error heuristic still works inside jq ==="
# String tool_result with a real newline: must match the error regex, get
# newlines flattened to spaces, and land as a string in the error key
echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Bash","cwd":"/tmp/proj","tool_input":{},"tool_result":"Error: command failed\nline two"}' | bash "$HOOK_SCRIPT"

assert_eq "error is a string when tool_result matches error regex" "$(last_line_jq '.error | type')" "string"
assert_eq "error text captured with newlines flattened" "$(last_line_jq '.error')" "Error: command failed line two"

echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Bash","cwd":"/tmp/proj","tool_input":{},"tool_result":"all good"}' | bash "$HOOK_SCRIPT"
assert_eq "error is null when tool_result has no error text" "$(last_line_jq '.error')" "null"

echo ""
echo "=== Group 4: real Claude Code payloads carry the result in tool_response ==="
# Regression: the heuristic keyed on .tool_result only, but Claude Code
# PostToolUse events use .tool_response — 0 non-null errors across 173k
# production events. tool_response must feed the error heuristic too.
echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Bash","cwd":"/tmp/proj","tool_input":{},"tool_response":"Error: command failed\nstderr says boom"}' | bash "$HOOK_SCRIPT"

assert_eq "error is a string when tool_response matches error regex" "$(last_line_jq '.error | type')" "string"
assert_eq "error captured from tool_response, newlines flattened" "$(last_line_jq '.error')" "Error: command failed stderr says boom"

# Structured (object) tool_response — the common Claude Code shape — must
# stringify and still trip the heuristic, truncated to 200 chars
LONG_MSG=$(printf 'x%.0s' $(seq 1 300))
echo "{\"hook_event_name\":\"PostToolUse\",\"session_id\":\"shift-session\",\"tool_name\":\"Bash\",\"cwd\":\"/tmp/proj\",\"tool_input\":{},\"tool_response\":{\"stdout\":\"\",\"stderr\":\"Error: $LONG_MSG\",\"interrupted\":false}}" | bash "$HOOK_SCRIPT"

assert_eq "object tool_response with error text captured" "$(last_line_jq '.error | type')" "string"
assert_eq "captured error truncated to 200 chars" "$(last_line_jq '.error | length')" "200"

# Clean tool_response -> error stays null
echo '{"hook_event_name":"PostToolUse","session_id":"shift-session","tool_name":"Bash","cwd":"/tmp/proj","tool_input":{},"tool_response":"all good here"}' | bash "$HOOK_SCRIPT"
assert_eq "error is null when tool_response has no error text" "$(last_line_jq '.error')" "null"

echo ""
echo "=== Group 5: every event landed as exactly one JSONL line ==="
LINE_COUNT=$(wc -l < "$EVENTS_FILE" | tr -d ' ')
assert_eq "7 events -> 7 lines" "$LINE_COUNT" "7"
VALID_COUNT=$(jq -c . "$EVENTS_FILE" 2>/dev/null | wc -l | tr -d ' ')
assert_eq "all 7 lines are valid JSON" "$VALID_COUNT" "7"

echo ""
echo "─────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
echo "─────────────────────────"
[[ $FAIL -eq 0 ]]
