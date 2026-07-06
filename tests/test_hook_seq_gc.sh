#!/usr/bin/env bash
# Test for the SessionEnd orphan-seq GC: sessions that die without firing
# SessionEnd leave .seq-* files behind forever (4,000+ orphans observed in
# production). hook.sh now deletes .seq-* files older than 7 days on
# SessionEnd, while leaving fresh seq files from live sessions alone.

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

echo "=== Group 1: SessionEnd GCs orphaned seq files, spares fresh ones ==="
mkdir -p "$DATA_DIR"

# Orphan from a session that died 9 days ago (past the -mtime +7 cutoff)
echo "7" > "$DATA_DIR/.seq-orphan-session"
OLD_TS=$(python3 -c "import datetime; print((datetime.datetime.now()-datetime.timedelta(days=9)).strftime('%Y%m%d%H%M'))")
touch -t "$OLD_TS" "$DATA_DIR/.seq-orphan-session"

# Fresh seq file belonging to another still-running session
echo "2" > "$DATA_DIR/.seq-fresh-session"

# Seq file for the session that is ending now
echo "5" > "$DATA_DIR/.seq-ending-session"

assert_file "orphan seq file staged" "$DATA_DIR/.seq-orphan-session"

echo '{"hook_event_name":"SessionEnd","session_id":"ending-session","cwd":"/tmp"}' | bash "$HOOK_SCRIPT"

# GC runs unconditionally on SessionEnd; summarize.py also runs there —
# stats.json proves the SessionEnd branch executed
assert_file "summarize ran (SessionEnd branch executed)" "$DATA_DIR/stats.json"
assert_no_file "orphan seq (>7 days old) deleted by GC" "$DATA_DIR/.seq-orphan-session"
assert_file "fresh seq from another live session survives" "$DATA_DIR/.seq-fresh-session"
assert_no_file "ending session's own seq removed" "$DATA_DIR/.seq-ending-session"

echo ""
echo "─────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
echo "─────────────────────────"
[[ $FAIL -eq 0 ]]
