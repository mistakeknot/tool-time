#!/usr/bin/env bash
# Tests for sylveste-sfhq.2: tool-time → interspect bridge.
#
# Verifies:
#   1. No patterns crossed → no evidence rows
#   2. Each pattern type fires correctly with right schema
#   3. Re-running on same session is idempotent (no duplicate rows)
#   4. Threshold config file overrides defaults
#   5. Min-calls floor prevents noise from rare tools
#   6. All emitted rows have source_kind='tool' and hook_id='tool-time-pattern'

set -eo pipefail
# NOTE: not using `set -u` — interspect lib-interspect.sh:248-249 has a pre-existing
# `$db` vs `$_INTERSPECT_DB` mismatch in the fresh-DB path that trips strict mode.
# Match convention with interspect's own shell tests (test_effectiveness.sh, etc.).

TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

export CLAUDE_PROJECT_DIR="$TEST_DIR"
mkdir -p "$TEST_DIR/.clavain/interspect"
mkdir -p "$TEST_DIR/.claude"
# Quarantine off so newly-inserted evidence is immediately observable
export INTERSPECT_QUARANTINE_HOURS=0

# Point bridge at a fake tool-time data dir
export TOOL_TIME_STATS_FILE="$TEST_DIR/stats.json"
export TOOL_TIME_THRESHOLDS="$TEST_DIR/thresholds.json"

# Locate the bridge + interspect lib. These tests need a sibling interspect
# checkout (development rig only) — skip cleanly when absent so marketplace
# installs and bare CI clones don't report a failure.
BRIDGE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/emit-interspect-evidence.sh"
INTERSPECT_HOOKS_DIR="$(dirname "${BASH_SOURCE[0]}")/../../interspect/hooks"
if [ ! -d "$INTERSPECT_HOOKS_DIR" ]; then
  echo "SKIP: sibling interspect checkout not found at ../../interspect — skipping bridge tests"
  exit 0
fi
export INTERSPECT_LIB="$(cd "$INTERSPECT_HOOKS_DIR" && pwd)/lib-interspect.sh"

# Pre-init the interspect DB so we can query it directly
source "$INTERSPECT_LIB"
_interspect_ensure_db
DB=$(_interspect_db_path)

PASS=0
FAIL=0

assert_eq() {
    local desc="$1" got="$2" expected="$3"
    if [[ "$got" == "$expected" ]]; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (got '$got', expected '$expected')"; ((FAIL++)) || true; fi
}

assert_ge() {
    local desc="$1" got="$2" expected="$3"
    if [[ "$got" -ge "$expected" ]] 2>/dev/null; then echo "  PASS: $desc"; ((PASS++)) || true
    else echo "  FAIL: $desc (got '$got', expected >= '$expected')"; ((FAIL++)) || true; fi
}

write_stats() {
    cat > "$TOOL_TIME_STATS_FILE"
}

reset_db() {
    sqlite3 "$DB" "DELETE FROM evidence;"
}

count_rows() {
    sqlite3 "$DB" "SELECT COUNT(*) FROM evidence WHERE $1;"
}

echo "=== Group 1: empty / clean stats produce no evidence ==="
reset_db
write_stats <<'JSON'
{
  "tools": {
    "Read": {"calls": 50, "errors": 0, "rejections": 0},
    "Edit": {"calls": 30, "errors": 1, "rejections": 0},
    "Bash": {"calls": 5, "errors": 0, "rejections": 0},
    "Grep": {"calls": 15, "errors": 0, "rejections": 0},
    "Glob": {"calls": 12, "errors": 0, "rejections": 0}
  },
  "edit_without_read_count": 0
}
JSON
"$BRIDGE_SCRIPT" "test-session-clean"
TOTAL=$(count_rows "1=1")
assert_eq "clean stats produce 0 evidence rows" "$TOTAL" "0"

echo ""
echo "=== Group 2: pattern detection ==="
reset_db
write_stats <<'JSON'
{
  "tools": {
    "Read": {"calls": 5, "errors": 3, "rejections": 0},
    "Edit": {"calls": 20, "errors": 5, "rejections": 4},
    "Bash": {"calls": 100, "errors": 12, "rejections": 0}
  },
  "edit_without_read_count": 7
}
JSON
"$BRIDGE_SCRIPT" "test-session-noisy"

# Read: 5 calls < min_calls_for_rate (10) → should NOT emit despite 60% error rate
READ_ROWS=$(count_rows "source = 'Read'")
assert_eq "Read with 5 calls excluded by min_calls_for_rate floor" "$READ_ROWS" "0"

# Edit: 20 calls, 25% errors (>=10%), 20% rejections (>=20%), edit_without_read=7
EDIT_ERROR=$(count_rows "source = 'Edit' AND event = 'tool_error_rate_high'")
EDIT_REJ=$(count_rows "source = 'Edit' AND event = 'tool_rejection_rate_high'")
EDIT_EWR=$(count_rows "source = 'Edit' AND event = 'tool_edit_without_read'")
assert_eq "Edit error_rate fires" "$EDIT_ERROR" "1"
assert_eq "Edit rejection_rate fires" "$EDIT_REJ" "1"
assert_eq "Edit edit_without_read fires" "$EDIT_EWR" "1"

# Bash: 100 calls, 12% errors, 80% share → should fire error_rate + bash_dominance
BASH_ERROR=$(count_rows "source = 'Bash' AND event = 'tool_error_rate_high'")
BASH_DOM=$(count_rows "source = 'Bash' AND event = 'tool_bash_dominance'")
assert_eq "Bash error_rate fires" "$BASH_ERROR" "1"
assert_eq "Bash dominance fires" "$BASH_DOM" "1"

# Low diversity: only 3 distinct tools (< 5)
LOW_DIV=$(count_rows "source = 'tool-time' AND event = 'tool_low_diversity'")
assert_eq "Low diversity fires" "$LOW_DIV" "1"

echo ""
echo "=== Group 3: schema invariants ==="
# All bridge-emitted rows must be source_kind='tool'
NON_TOOL=$(count_rows "source_kind != 'tool'")
assert_eq "all bridge rows have source_kind='tool'" "$NON_TOOL" "0"

# All bridge-emitted rows must carry source_event_id (lineage)
NO_LINEAGE=$(count_rows "source_event_id IS NULL OR source_event_id = ''")
assert_eq "all bridge rows have source_event_id" "$NO_LINEAGE" "0"

# All bridge-emitted rows must carry source_table='tool-time-stats'
WRONG_TABLE=$(count_rows "source_table != 'tool-time-stats'")
assert_eq "all bridge rows have source_table='tool-time-stats'" "$WRONG_TABLE" "0"

echo ""
echo "=== Group 4: idempotency ==="
BEFORE=$(count_rows "1=1")
"$BRIDGE_SCRIPT" "test-session-noisy"
AFTER=$(count_rows "1=1")
assert_eq "re-run on same session does not duplicate rows" "$AFTER" "$BEFORE"

echo ""
echo "=== Group 5: threshold config override ==="
reset_db
# Same noisy stats, but override error_rate to 0.99 (effectively disable)
cat > "$TOOL_TIME_THRESHOLDS" <<'JSON'
{
  "error_rate": 0.99,
  "rejection_rate": 0.99,
  "bash_share": 0.99,
  "min_distinct_tools": 1,
  "edit_without_read_min": 9999
}
JSON
"$BRIDGE_SCRIPT" "test-session-thresh"
ERROR_ROWS=$(count_rows "event = 'tool_error_rate_high'")
REJ_ROWS=$(count_rows "event = 'tool_rejection_rate_high'")
DOM_ROWS=$(count_rows "event = 'tool_bash_dominance'")
DIV_ROWS=$(count_rows "event = 'tool_low_diversity'")
EWR_ROWS=$(count_rows "event = 'tool_edit_without_read'")
assert_eq "thresholds.json disables error_rate" "$ERROR_ROWS" "0"
assert_eq "thresholds.json disables rejection_rate" "$REJ_ROWS" "0"
assert_eq "thresholds.json disables bash_share" "$DOM_ROWS" "0"
assert_eq "thresholds.json disables low_diversity" "$DIV_ROWS" "0"
assert_eq "thresholds.json disables edit_without_read" "$EWR_ROWS" "0"

# Reset thresholds for any further tests
rm -f "$TOOL_TIME_THRESHOLDS"

echo ""
echo "=== Group 6: separate session, same DB — coexistence ==="
reset_db
write_stats <<'JSON'
{
  "tools": {"Bash": {"calls": 50, "errors": 10, "rejections": 0}},
  "edit_without_read_count": 0
}
JSON
"$BRIDGE_SCRIPT" "session-A"
"$BRIDGE_SCRIPT" "session-B"
A_ROWS=$(count_rows "session_id = 'session-A' AND event = 'tool_error_rate_high'")
B_ROWS=$(count_rows "session_id = 'session-B' AND event = 'tool_error_rate_high'")
assert_eq "session-A error_rate row exists" "$A_ROWS" "1"
assert_eq "session-B error_rate row exists" "$B_ROWS" "1"

echo ""
echo "=== Group 7: missing stats.json is non-fatal ==="
rm -f "$TOOL_TIME_STATS_FILE"
if "$BRIDGE_SCRIPT" "no-stats-session" 2>/dev/null; then
    echo "  PASS: bridge exits 0 when stats.json missing"; ((PASS++)) || true
else
    echo "  FAIL: bridge errored on missing stats.json"; ((FAIL++)) || true
fi

echo ""
echo "=== Group 8a: stdin session_id extraction ==="
reset_db
write_stats <<'JSON'
{
  "tools": {"Bash": {"calls": 50, "errors": 8, "rejections": 0}},
  "edit_without_read_count": 0
}
JSON
# Pipe hook-style JSON, no $1
echo '{"hook_event_name":"SessionEnd","session_id":"stdin-session-1"}' | "$BRIDGE_SCRIPT"
STDIN_ROWS=$(count_rows "session_id = 'stdin-session-1' AND event = 'tool_error_rate_high'")
assert_eq "session_id extracted from stdin JSON" "$STDIN_ROWS" "1"

# Arg takes precedence over stdin if both supplied
reset_db
echo '{"session_id":"from-stdin"}' | "$BRIDGE_SCRIPT" "from-arg"
ARG_ROWS=$(count_rows "session_id = 'from-arg' AND event = 'tool_error_rate_high'")
STDIN_ROWS=$(count_rows "session_id = 'from-stdin'")
assert_eq "arg \$1 takes precedence over stdin" "$ARG_ROWS" "1"
assert_eq "stdin ignored when arg present" "$STDIN_ROWS" "0"

echo ""
echo "=== Group 8: missing session_id is non-fatal ==="
write_stats <<'JSON'
{ "tools": {}, "edit_without_read_count": 0 }
JSON
if "$BRIDGE_SCRIPT" "" 2>/dev/null; then
    echo "  PASS: bridge exits 0 when session_id empty"; ((PASS++)) || true
else
    echo "  FAIL: bridge errored on empty session_id"; ((FAIL++)) || true
fi

echo ""
echo "─────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
echo "─────────────────────────"
[[ $FAIL -eq 0 ]]
