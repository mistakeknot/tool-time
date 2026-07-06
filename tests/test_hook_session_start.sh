#!/usr/bin/env bash
# Test for the SessionStart digest branch: hook.sh must cat digest.txt to
# stdout (which Claude Code attaches as session context) when the digest
# exists, is newer than 48 hours, and is non-empty — and emit nothing (but
# still exit 0) otherwise.

set -eo pipefail

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SCRIPT="$PLUGIN_ROOT/hooks/hook.sh"

# Redirect HOME so the hook reads from a tmp ~/.claude/tool-time
export HOME="$TEST_DIR"
mkdir -p "$HOME/.claude"
DATA_DIR="$HOME/.claude/tool-time"
DIGEST_FILE="$DATA_DIR/digest.txt"
SESSION_START_JSON='{"hook_event_name":"SessionStart","session_id":"start-session","cwd":"/tmp"}'

PASS=0
FAIL=0

assert_eq() {
    local desc="$1" got="$2" expected="$3"
    if [[ "$got" == "$expected" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (got '$got', expected '$expected')"; ((FAIL++)) || true; fi
}

# Fire a SessionStart at the hook; sets OUT (stdout) and RC (exit code)
run_session_start() {
    RC=0
    OUT=$(echo "$SESSION_START_JSON" | bash "$HOOK_SCRIPT") || RC=$?
}

echo "=== Group 1: fresh non-empty digest is emitted on stdout ==="
mkdir -p "$DATA_DIR"
printf 'tool-time digest\nTop tool: Bash (42 calls)\n' > "$DIGEST_FILE"
run_session_start
assert_eq "stdout equals digest content" "$OUT" "$(cat "$DIGEST_FILE")"
assert_eq "exit code 0 with digest" "$RC" "0"

echo ""
echo "=== Group 2: absent digest -> empty stdout, exit 0 ==="
rm -f "$DIGEST_FILE"
run_session_start
assert_eq "stdout empty when digest absent" "$OUT" ""
assert_eq "exit code 0 without digest" "$RC" "0"

echo ""
echo "=== Group 3: stale digest (>48h) is NOT emitted ==="
printf 'stale digest content\n' > "$DIGEST_FILE"
STALE_TS=$(python3 -c "import datetime; print((datetime.datetime.now()-datetime.timedelta(hours=72)).strftime('%Y%m%d%H%M'))")
touch -t "$STALE_TS" "$DIGEST_FILE"
run_session_start
assert_eq "stdout empty for stale digest" "$OUT" ""
assert_eq "exit code 0 for stale digest" "$RC" "0"

echo ""
echo "=== Group 4: empty digest file is NOT emitted ==="
> "$DIGEST_FILE"
run_session_start
assert_eq "stdout empty for zero-byte digest" "$OUT" ""
assert_eq "exit code 0 for zero-byte digest" "$RC" "0"

echo ""
echo "=== Group 5: SessionStart still logs nothing ==="
SEQ_COUNT=$(find "$DATA_DIR" -maxdepth 1 -name '.seq-*' | wc -l | tr -d ' ')
assert_eq "no seq files created by SessionStart" "$SEQ_COUNT" "0"
if [[ -f "$DATA_DIR/events.jsonl" ]]; then
    EVENT_LINES=$(wc -l < "$DATA_DIR/events.jsonl" | tr -d ' ')
else
    EVENT_LINES=0
fi
assert_eq "no events.jsonl lines written by SessionStart" "$EVENT_LINES" "0"

echo ""
echo "─────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
echo "─────────────────────────"
[[ $FAIL -eq 0 ]]
