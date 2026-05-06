#!/usr/bin/env bash
# emit-interspect-evidence.sh — bridge tool-time stats into interspect evidence (sylveste-sfhq.2)
#
# Reads a tool-time stats.json snapshot, applies configurable thresholds, and emits one
# interspect evidence row per detected pattern. Writes to the interspect SQLite DB at
# $CLAUDE_PROJECT_DIR/.clavain/interspect/interspect.db via _interspect_insert_evidence.
#
# Idempotency: each evidence row carries source_event_id = "<session_id>:<event_type>:<source>".
# Re-running on the same session is a no-op.
#
# Inputs (env / args):
#   $1                    — session_id (required; usually from hook stdin)
#   TOOL_TIME_STATS_FILE  — override path to stats.json (default: ~/.claude/tool-time/stats.json)
#   TOOL_TIME_THRESHOLDS  — override path to thresholds JSON (default: ~/.claude/tool-time/interspect-thresholds.json)
#   INTERSPECT_LIB        — override path to lib-interspect.sh (default: auto-discover)
#   CLAUDE_PROJECT_DIR    — passes through to interspect for DB resolution
#
# Threshold defaults (fall through if file missing or key absent):
#   min_calls_for_rate    = 10
#   error_rate            = 0.10
#   rejection_rate        = 0.20
#   edit_without_read_min = 1
#   bash_share            = 0.50
#   min_distinct_tools    = 5
#
# To disable a pattern, set its threshold to a sentinel that no real value can cross
# (e.g. error_rate: 99 disables that pattern).

set -uo pipefail

SESSION_ID="${1:-}"
# If no arg supplied, try reading hook JSON from stdin.
# This lets the script work both as `emit-... <session_id>` (CLI/test) and
# as a hook command (stdin receives the hook payload).
if [[ -z "$SESSION_ID" && ! -t 0 ]]; then
    STDIN_JSON=$(cat 2>/dev/null || true)
    if [[ -n "$STDIN_JSON" ]]; then
        SESSION_ID=$(echo "$STDIN_JSON" | jq -r '.session_id // ""' 2>/dev/null || echo "")
    fi
fi
if [[ -z "$SESSION_ID" ]]; then
    echo "[emit-interspect-evidence] missing session_id (arg \$1 or stdin .session_id)" >&2
    exit 0  # non-fatal — hook should not block session
fi

DATA_DIR="${HOME}/.claude/tool-time"
STATS_FILE="${TOOL_TIME_STATS_FILE:-${DATA_DIR}/stats.json}"
THRESHOLDS_FILE="${TOOL_TIME_THRESHOLDS:-${DATA_DIR}/interspect-thresholds.json}"

# ─── Locate lib-interspect.sh ────────────────────────────────────────────────

if [[ -z "${INTERSPECT_LIB:-}" ]]; then
    # 1. Plugin cache (production install)
    INTERSPECT_LIB=$(find "${HOME}/.claude/plugins/cache" \
        -path '*/interspect/*/hooks/lib-interspect.sh' \
        -o -path '*/clavain/*/hooks/lib-interspect.sh' \
        2>/dev/null | head -1)
fi
if [[ -z "$INTERSPECT_LIB" || ! -f "$INTERSPECT_LIB" ]]; then
    # 2. Monorepo dev path (sibling plugin in interverse/)
    DEV_PATH="$(cd "$(dirname "$0")/.." 2>/dev/null && cd ../interspect/hooks 2>/dev/null && pwd)/lib-interspect.sh"
    [[ -f "$DEV_PATH" ]] && INTERSPECT_LIB="$DEV_PATH"
fi
if [[ -z "$INTERSPECT_LIB" || ! -f "$INTERSPECT_LIB" ]]; then
    echo "[emit-interspect-evidence] cannot locate lib-interspect.sh; aborting" >&2
    exit 0
fi

# shellcheck source=/dev/null
source "$INTERSPECT_LIB"
_interspect_ensure_db || { echo "[emit-interspect-evidence] interspect DB init failed" >&2; exit 0; }

DB=$(_interspect_db_path)
[[ -f "$DB" ]] || exit 0

# ─── Load stats.json ─────────────────────────────────────────────────────────

if [[ ! -f "$STATS_FILE" ]]; then
    # No stats yet — nothing to emit.
    exit 0
fi
if ! jq empty "$STATS_FILE" 2>/dev/null; then
    echo "[emit-interspect-evidence] $STATS_FILE is not valid JSON" >&2
    exit 0
fi

# ─── Resolve thresholds (file overrides defaults; missing keys fall back) ────

# Defaults; override individually below if config file exists.
T_MIN_CALLS=10
T_ERROR_RATE="0.10"
T_REJECTION_RATE="0.20"
T_EDIT_WITHOUT_READ_MIN=1
T_BASH_SHARE="0.50"
T_MIN_DISTINCT_TOOLS=5

if [[ -f "$THRESHOLDS_FILE" ]] && jq empty "$THRESHOLDS_FILE" 2>/dev/null; then
    _read() { jq -r --arg k "$1" --arg d "$2" '.[$k] // $d' "$THRESHOLDS_FILE"; }
    T_MIN_CALLS=$(_read min_calls_for_rate "$T_MIN_CALLS")
    T_ERROR_RATE=$(_read error_rate "$T_ERROR_RATE")
    T_REJECTION_RATE=$(_read rejection_rate "$T_REJECTION_RATE")
    T_EDIT_WITHOUT_READ_MIN=$(_read edit_without_read_min "$T_EDIT_WITHOUT_READ_MIN")
    T_BASH_SHARE=$(_read bash_share "$T_BASH_SHARE")
    T_MIN_DISTINCT_TOOLS=$(_read min_distinct_tools "$T_MIN_DISTINCT_TOOLS")
fi

# ─── Idempotent insert helper ────────────────────────────────────────────────

# Args: $1=event_type $2=source(tool name) $3=context_json
emit_pattern() {
    local event_type="$1"
    local src="$2"
    local ctx="${3:-{}}"
    local event_id="${SESSION_ID}:${event_type}:${src}"
    local escaped="${event_id//\'/\'\'}"

    local exists
    exists=$(sqlite3 "$DB" "SELECT 1 FROM evidence WHERE source_event_id = '${escaped}' LIMIT 1;" 2>/dev/null)
    if [[ -n "$exists" ]]; then
        return 0  # already emitted for this session
    fi

    # _interspect_insert_evidence args:
    #   1=session_id 2=source 3=event 4=override_reason 5=context_json
    #   6=hook_id    7=source_event_id 8=source_table 9=raw_override_reason 10=source_kind
    _interspect_insert_evidence \
        "$SESSION_ID" "$src" "$event_type" "" "$ctx" \
        "tool-time-pattern" "$event_id" "tool-time-stats" "" "tool"
}

# ─── Pattern detection ───────────────────────────────────────────────────────

EMITTED=0

# Per-tool: error rate + rejection rate
mapfile -t TOOL_LINES < <(jq -r '
    .tools | to_entries[] |
    [.key, (.value.calls // 0), (.value.errors // 0), (.value.rejections // 0)] |
    @tsv
' "$STATS_FILE")

for line in "${TOOL_LINES[@]}"; do
    [[ -z "$line" ]] && continue
    IFS=$'\t' read -r tool calls errors rejections <<< "$line"
    [[ -z "$tool" || "$calls" -lt "$T_MIN_CALLS" ]] && continue

    err_rate=$(awk -v e="$errors" -v c="$calls" 'BEGIN { printf "%.4f", (c>0 ? e/c : 0) }')
    rej_rate=$(awk -v r="$rejections" -v c="$calls" 'BEGIN { printf "%.4f", (c>0 ? r/c : 0) }')

    if awk -v r="$err_rate" -v t="$T_ERROR_RATE" 'BEGIN { exit !(r >= t) }'; then
        ctx=$(jq -nc --arg t "$tool" --argjson c "$calls" --argjson e "$errors" \
              --arg rate "$err_rate" --arg th "$T_ERROR_RATE" \
              '{tool:$t, calls:$c, errors:$e, error_rate:($rate|tonumber), threshold:($th|tonumber)}')
        emit_pattern "tool_error_rate_high" "$tool" "$ctx" && EMITTED=$((EMITTED+1))
    fi

    if awk -v r="$rej_rate" -v t="$T_REJECTION_RATE" 'BEGIN { exit !(r >= t) }'; then
        ctx=$(jq -nc --arg t "$tool" --argjson c "$calls" --argjson rj "$rejections" \
              --arg rate "$rej_rate" --arg th "$T_REJECTION_RATE" \
              '{tool:$t, calls:$c, rejections:$rj, rejection_rate:($rate|tonumber), threshold:($th|tonumber)}')
        emit_pattern "tool_rejection_rate_high" "$tool" "$ctx" && EMITTED=$((EMITTED+1))
    fi
done

# Session-wide: edit-without-read
EWR=$(jq -r '.edit_without_read_count // 0' "$STATS_FILE")
if [[ "$EWR" -ge "$T_EDIT_WITHOUT_READ_MIN" ]]; then
    ctx=$(jq -nc --argjson c "$EWR" --argjson th "$T_EDIT_WITHOUT_READ_MIN" \
          '{count:$c, threshold:$th}')
    emit_pattern "tool_edit_without_read" "Edit" "$ctx" && EMITTED=$((EMITTED+1))
fi

# Session-wide: bash dominance
TOTAL_CALLS=$(jq -r '[.tools[].calls // 0] | add // 0' "$STATS_FILE")
BASH_CALLS=$(jq -r '.tools.Bash.calls // 0' "$STATS_FILE")
if [[ "$TOTAL_CALLS" -ge "$T_MIN_CALLS" ]]; then
    bash_share=$(awk -v b="$BASH_CALLS" -v t="$TOTAL_CALLS" 'BEGIN { printf "%.4f", (t>0 ? b/t : 0) }')
    if awk -v r="$bash_share" -v t="$T_BASH_SHARE" 'BEGIN { exit !(r >= t) }'; then
        ctx=$(jq -nc --argjson b "$BASH_CALLS" --argjson t "$TOTAL_CALLS" \
              --arg share "$bash_share" --arg th "$T_BASH_SHARE" \
              '{bash_calls:$b, total_calls:$t, bash_share:($share|tonumber), threshold:($th|tonumber)}')
        emit_pattern "tool_bash_dominance" "Bash" "$ctx" && EMITTED=$((EMITTED+1))
    fi
fi

# Session-wide: low tool diversity
DISTINCT_TOOLS=$(jq -r '.tools | length' "$STATS_FILE")
if [[ "$TOTAL_CALLS" -ge "$T_MIN_CALLS" && "$DISTINCT_TOOLS" -lt "$T_MIN_DISTINCT_TOOLS" ]]; then
    ctx=$(jq -nc --argjson d "$DISTINCT_TOOLS" --argjson th "$T_MIN_DISTINCT_TOOLS" \
          --argjson tc "$TOTAL_CALLS" \
          '{distinct_tools:$d, total_calls:$tc, threshold:$th}')
    emit_pattern "tool_low_diversity" "tool-time" "$ctx" && EMITTED=$((EMITTED+1))
fi

if [[ "${TOOL_TIME_BRIDGE_VERBOSE:-0}" == "1" ]]; then
    echo "[emit-interspect-evidence] session=${SESSION_ID} emitted=${EMITTED}"
fi

exit 0
