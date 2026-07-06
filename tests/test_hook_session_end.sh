#!/usr/bin/env bash
# Regression test for sylveste-63ha: hook.sh must handle SessionEnd correctly.
#
# Bug: hook.sh:22 case statement allowed Stop but not SessionEnd, so the
# SessionEnd handler at the bottom (rm seq + summarize + upload) was dead code.
# Functional impact: stats.json never auto-refreshed on session end.
#
# Fix: SessionEnd is now handled in an early-return branch that refreshes
# stats and queues upload, then exits before the per-tool JSONL write.

set -eo pipefail

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SCRIPT="$PLUGIN_ROOT/hooks/hook.sh"

# Redirect HOME so the hook writes to a tmp ~/.claude/tool-time
export HOME="$TEST_DIR"
mkdir -p "$HOME/.claude"
DATA_DIR="$HOME/.claude/tool-time"

PASS=0
FAIL=0

assert_eq() {
    local desc="$1" got="$2" expected="$3"
    if [[ "$got" == "$expected" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (got '$got', expected '$expected')"; ((FAIL++)) || true; fi
}

assert_file() {
    local desc="$1" path="$2"
    if [[ -f "$path" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (file missing: $path)"; ((FAIL++)) || true; fi
}

assert_no_file() {
    local desc="$1" path="$2"
    if [[ ! -f "$path" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (file exists: $path)"; ((FAIL++)) || true; fi
}

echo "=== Group 1: SessionEnd refreshes stats.json ==="
SESSION_ID="test-session-$$"

# Seed an existing stats.json with a known stale marker
mkdir -p "$DATA_DIR"
echo '{"stale_marker":"true","tools":{}}' > "$DATA_DIR/stats.json"
STALE_HASH=$(sha256sum "$DATA_DIR/stats.json" | cut -d' ' -f1)

# Fire SessionEnd
echo "{\"hook_event_name\":\"SessionEnd\",\"session_id\":\"$SESSION_ID\",\"cwd\":\"$TEST_DIR\"}" | bash "$HOOK_SCRIPT"

# stats.json must have been rewritten (hash changed)
assert_file "stats.json exists after SessionEnd" "$DATA_DIR/stats.json"
NEW_HASH=$(sha256sum "$DATA_DIR/stats.json" | cut -d' ' -f1)
if [[ "$NEW_HASH" != "$STALE_HASH" ]]; then
    echo "  PASS: stats.json was rewritten by summarize.py"
    ((PASS++)) || true
else
    echo "  FAIL: stats.json hash unchanged — summarize.py didn't run"
    ((FAIL++)) || true
fi

# stats.json must NOT contain the stale marker (proves summarize ran cleanly)
if grep -q "stale_marker" "$DATA_DIR/stats.json"; then
    echo "  FAIL: stale marker still present"
    ((FAIL++)) || true
else
    echo "  PASS: stale marker removed by fresh summarize"
    ((PASS++)) || true
fi

echo ""
echo "=== Group 2: SessionEnd cleans up seq file ==="
SEQ_FILE="$DATA_DIR/.seq-$SESSION_ID"
echo "42" > "$SEQ_FILE"
assert_file "seq file pre-exists" "$SEQ_FILE"

echo "{\"hook_event_name\":\"SessionEnd\",\"session_id\":\"$SESSION_ID\",\"cwd\":\"$TEST_DIR\"}" | bash "$HOOK_SCRIPT"
assert_no_file "seq file removed by SessionEnd" "$SEQ_FILE"

echo ""
echo "=== Group 3: SessionEnd does NOT pollute events.jsonl with empty-tool entries ==="
# Reset events.jsonl
> "$DATA_DIR/events.jsonl"
PRE_LINES=$(wc -l < "$DATA_DIR/events.jsonl" | tr -d ' ')

echo '{"hook_event_name":"SessionEnd","session_id":"clean-session","cwd":"/tmp"}' | bash "$HOOK_SCRIPT"

POST_LINES=$(wc -l < "$DATA_DIR/events.jsonl" | tr -d ' ')
assert_eq "events.jsonl unchanged by SessionEnd (no empty-tool entry written)" "$POST_LINES" "$PRE_LINES"

echo ""
echo "=== Group 4: PreToolUse still works (regression — non-SessionEnd path unchanged) ==="
> "$DATA_DIR/events.jsonl"
echo '{"hook_event_name":"PreToolUse","session_id":"tool-session","tool_name":"Bash","cwd":"/tmp","tool_input":{}}' | bash "$HOOK_SCRIPT"
EVENT_LINES=$(wc -l < "$DATA_DIR/events.jsonl" | tr -d ' ')
assert_eq "PreToolUse still writes to events.jsonl" "$EVENT_LINES" "1"

# Verify the event line has the tool name
if grep -q '"tool":"Bash"' "$DATA_DIR/events.jsonl"; then
    echo "  PASS: PreToolUse JSONL line contains tool name"
    ((PASS++)) || true
else
    echo "  FAIL: PreToolUse JSONL line missing tool name"
    ((FAIL++)) || true
fi

echo ""
echo "=== Group 5: SessionStart still bypasses (no JSONL write, no summarize) ==="
> "$DATA_DIR/events.jsonl"
rm -f "$DATA_DIR/stats.json"
echo '{"hook_event_name":"SessionStart","session_id":"start-session","cwd":"/tmp"}' | bash "$HOOK_SCRIPT"

POST_LINES=$(wc -l < "$DATA_DIR/events.jsonl" | tr -d ' ')
assert_eq "SessionStart writes nothing to events.jsonl" "$POST_LINES" "0"
assert_no_file "SessionStart does NOT trigger summarize" "$DATA_DIR/stats.json"

echo ""
echo "=== Group 6: summarize.py failure must NOT skip maintenance (digest tripwire) ==="
# Regression: seq GC and maintain.py used to run only inside
# 'if python3 summarize.py; then' — when summarize.py itself failed, the
# digest tripwire that is supposed to report a broken pipeline never ran.
# Maintenance must run unconditionally; only upload.py stays gated on
# summarize.py success.
MARKER_DIR="$TEST_DIR/markers"
mkdir -p "$MARKER_DIR" "$TEST_DIR/bin"
REAL_PYTHON3=$(command -v python3)
export MARKER_DIR REAL_PYTHON3

# python3 shim: summarize.py fails; maintain.py/upload.py drop invocation markers
cat > "$TEST_DIR/bin/python3" <<'SHIM'
#!/usr/bin/env bash
case "$1" in
  */summarize.py) touch "$MARKER_DIR/summarize.invoked"; exit 1 ;;
  */maintain.py)  touch "$MARKER_DIR/maintain.invoked";  exit 0 ;;
  */upload.py)    touch "$MARKER_DIR/upload.invoked";    exit 0 ;;
  *) exec "$REAL_PYTHON3" "$@" ;;
esac
SHIM
chmod +x "$TEST_DIR/bin/python3"

# Stale orphan seq file (>7 days) that the GC must delete despite the failure
echo "3" > "$DATA_DIR/.seq-orphan-after-failure"
OLD_TS=$("$REAL_PYTHON3" -c "import datetime; print((datetime.datetime.now()-datetime.timedelta(days=9)).strftime('%Y%m%d%H%M'))")
touch -t "$OLD_TS" "$DATA_DIR/.seq-orphan-after-failure"

rm -f "$DATA_DIR/stats.json"
echo '{"hook_event_name":"SessionEnd","session_id":"fail-session","cwd":"/tmp"}' \
  | PATH="$TEST_DIR/bin:$PATH" bash "$HOOK_SCRIPT"
sleep 1  # let any (wrongly) backgrounded upload land before asserting

assert_file "summarize.py was invoked (and failed)" "$MARKER_DIR/summarize.invoked"
assert_no_file "stats.json not written by failing summarize" "$DATA_DIR/stats.json"
assert_file "maintain.py STILL runs when summarize.py fails" "$MARKER_DIR/maintain.invoked"
assert_no_file "orphan seq GC STILL runs when summarize.py fails" "$DATA_DIR/.seq-orphan-after-failure"
assert_no_file "upload.py stays gated on summarize success" "$MARKER_DIR/upload.invoked"

echo ""
echo "=== Group 7: hooks.json wires summarize.py only via hook.sh (no parallel race) ==="
# Regression: the second SessionEnd command used to run its own summarize.py
# in parallel with hook.sh's, racing on stats.json. The interspect-evidence
# bridge (with its INPUT capture) must remain intact.
HOOKS_JSON="$PLUGIN_ROOT/hooks/hooks.json"
SUMMARIZE_REFS=$(grep -c 'summarize\.py' "$HOOKS_JSON" || true)
assert_eq "no direct summarize.py invocation in hooks.json" "$SUMMARIZE_REFS" "0"
BRIDGE_REFS=$(grep -c 'emit-interspect-evidence\.sh' "$HOOKS_JSON" || true)
assert_eq "interspect evidence bridge still registered" "$BRIDGE_REFS" "1"
INPUT_CAPTURE=$(grep -c 'INPUT=\$(cat)' "$HOOKS_JSON" || true)
assert_eq "evidence command still captures stdin INPUT" "$INPUT_CAPTURE" "1"

echo ""
echo "─────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
echo "─────────────────────────"
[[ $FAIL -eq 0 ]]
