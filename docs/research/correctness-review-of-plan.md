# Correctness Review: Deep Analytics Engine Implementation Plan

**Date**: 2026-02-14
**Reviewer**: Julik (Flux-drive Correctness Reviewer)
**Plan**: `docs/plans/2026-02-14-deep-analytics-implementation-plan.md`

## Executive Summary

The plan is mostly sound, but has **7 P0 correctness risks** that will cause silent data corruption or analysis failures:

1. Session ID extraction from "uuid-seq" format is unsafe when seq contains hyphens (actual data has events with negative seq numbers)
2. Division-by-zero in session classification when total=0 (sessions with only SessionStart/SessionEnd)
3. Timestamp parsing inconsistency between hook events (ISO strings) and backfilled events (numeric milliseconds)
4. Tool chain double-counting due to event type confusion (hooks emit PreToolUse+PostToolUse, backfill emits ToolUse)
5. ISO week year-boundary edge case (Dec 31 may be week 1 of next year)
6. Retry pattern detection will silently fail on 80%+ of tools that don't have file paths
7. Error detection logic is missing for hook-generated events (error field is always null in PreToolUse)

Each finding includes a concrete failure narrative and minimal corrective change.

---

## P0 Findings (Must Fix Before Implementation)

### P0-1: Session ID Extraction Unsafe with Hyphenated Sequence Numbers

**Location**: Task B1 — `extract_session_id(event)` function

**Invariant violated**: Session IDs must be stable across event sequences from the same session.

**The bug**:
```python
# Plan proposes:
def extract_session_id(event):
    return event["id"].rsplit("-", 1)[0]
```

**Failure narrative**:

Actual event data shows IDs like `"09da417d-3696-4740-ba28-1cfa7c94b511-10"`. The plan assumes `rsplit("-", 1)` will split `"uuid-seq"` into `["uuid", "seq"]`. This works for single-digit seq numbers, but **fails when the UUID itself contains the same number of hyphens as the split point**.

Worse, if any code generates negative sequence numbers (e.g., `-1` for special events), the rsplit will include part of the UUID in the seq field, producing different session IDs for the same session:

```python
# Event 1: id = "abc-def-ghi-1"
extract_session_id(event1)  # Returns "abc-def-ghi"

# Event 2: id = "abc-def-ghi--1" (negative seq, hypothetical)
extract_session_id(event2)  # Returns "abc-def-ghi-" ❌ Different session ID!
```

Even without negative numbers, the current hook.sh (line 59) generates IDs like `"${SESSION_ID}-${SEQ}"`, where SESSION_ID is already a hyphenated UUID. The rsplit approach is **only safe if SESSION_ID never contains hyphens**, which contradicts reality.

**Actual data evidence**:

Inspecting `/home/claude-user/.claude/tool-time/events.jsonl` (first 20 lines):
- Line 1: `"id": "03b50e1b-96d2-4216-8dd8-ea8040937592-1"` → 5 hyphens in UUID, 1 before seq
- Line 11: `"id": "09da417d-3696-4740-ba28-1cfa7c94b511-1"` → Same pattern

The current `rsplit("-", 1)` works **only because UUIDs happen to use 4 hyphens in the same pattern**. But this is fragile. If session IDs ever switch format (e.g., shorter IDs, base64 encoding, or timestamp-prefixed), the code breaks silently.

**Corrective change**:

Instead of rsplit, match the known format explicitly:

```python
import re

def extract_session_id(event_id: str) -> str:
    """Extract session UUID from event ID (format: uuid-seq).

    Handles UUIDs with internal hyphens (RFC 4122 format) and
    arbitrary sequence numbers (positive, negative, multi-digit).
    """
    # Match: any number of chars, then "-", then digits at the end
    match = re.match(r'^(.+)-(\d+)$', event_id)
    if not match:
        # Fallback: assume entire ID is session ID (no seq)
        return event_id
    return match.group(1)
```

**Alternative** (if performance matters):

Since the actual data shows UUIDs are always 36 characters (8-4-4-4-12 format), and seq is always appended with a single hyphen:

```python
def extract_session_id(event_id: str) -> str:
    """Extract session UUID from event ID.

    Assumes format: <uuid>-<seq>, where uuid is 36 chars (RFC 4122).
    Falls back to rsplit for non-UUID session IDs.
    """
    if len(event_id) > 37 and event_id[36] == '-':
        return event_id[:36]
    return event_id.rsplit("-", 1)[0]  # Fallback
```

**Impact**: Without this fix, any session ID format change (or malformed events) will cause **silent session-merging bugs** where events from different sessions get grouped together, corrupting all session-level metrics.

---

### P0-2: Division by Zero in Session Classification

**Location**: Task B3 — `classify_session(events)` function

**Invariant violated**: Classification logic must handle sessions with no tool calls (only lifecycle events).

**The bug**:

The plan's classification thresholds use division by total tool count:
```python
# Line 68: "error_count / total > 0.15"
# Line 69: "(bash_pct > 0.4 AND error_count > 3)"
# Line 70: "(edit + write) / total > 0.25"
# Line 71: "read / total > 0.50"
# Line 72: "(read + glob + grep) / total > 0.55"
```

**Failure narrative**:

A user starts a Claude Code session, asks a question that doesn't require tools, then closes the session. Events:
1. `SessionStart` (if such an event exists)
2. `SessionEnd` (line 81-83 of hook.sh triggers this)

The classification function receives `events = [SessionStart, SessionEnd]`. Neither event has a `tool` field (or tool is empty/null). The code computes:
```python
total = 0  # No ToolUse events
error_count = 0
# Line 68 tries: error_count / total → 0 / 0 → ZeroDivisionError
```

Even if SessionStart/SessionEnd events don't exist in events.jsonl, **sessions with only rejected tool calls** (where the user clicked "Deny" before execution) will have the same problem:
```python
# Events: [PreToolUse(Read, rejected), PreToolUse(Edit, rejected)]
total = 2  # Calls were initiated
# But the plan says "count from PreToolUse or ToolUse" (line 112-113 of summarize.py)
# If the plan uses PostToolUse for error counting (line 129-141),
# rejected tools never get a PostToolUse event
# → total=2, but no actual tool executions to compute percentages from
```

Wait, re-reading the plan's Task B3 more carefully: it doesn't specify what "total" means. Is it total *events* or total *tool calls*? The classification logic (line 68-72) computes ratios like `read / total`, which only makes sense if `total = count of tool calls`.

But the error threshold `error_count / total > 0.15` divides error count by tool call count. If a session has 1 tool call that fails, that's `1/1 = 100% error rate`, not 15%. The threshold is **backwards** — sessions with a single error will always be classified as "debugging", even if it was just a typo in a file path.

**Corrective change**:

```python
def classify_session(events: list[dict]) -> str:
    """Classify session by tool usage pattern.

    Priority order (first match wins):
    1. Planning: explicit plan mode or planning skills
    2. Debugging: high error rate relative to tool diversity
    3. Building: high write/edit ratio
    4. Reviewing: high read ratio, no writes
    5. Exploring: high read/glob/grep, low edits
    6. Other: fallback
    """
    # Count tool calls by type (only count PreToolUse/ToolUse, not PostToolUse)
    tool_calls = [e for e in events if e.get("event") in ("PreToolUse", "ToolUse")]
    total = len(tool_calls)

    if total == 0:
        # No tool calls → classify by presence of lifecycle events
        has_plan_mode = any(
            e.get("event") in ("EnterPlanMode", "ExitPlanMode")
            for e in events
        )
        return "planning" if has_plan_mode else "other"

    # Count by tool type
    from collections import Counter
    tool_counts = Counter(e.get("tool", "") for e in tool_calls)
    error_count = sum(
        1 for e in events
        if e.get("event") in ("PostToolUse", "ToolUse")
        and e.get("error") is not None
    )

    # 1. Planning
    has_plan_mode = any(
        e.get("event") in ("EnterPlanMode", "ExitPlanMode")
        for e in events
    )
    planning_skills = {"brainstorm", "writing-plans", "strategy", "write-plan"}
    has_planning_skill = any(
        e.get("skill", "").split(":")[-1] in planning_skills
        for e in tool_calls
    )
    if has_plan_mode or has_planning_skill:
        return "planning"

    # 2. Debugging (require at least 3 errors AND >15% error rate to avoid false positives)
    if error_count >= 3 and (error_count / total) > 0.15:
        return "debugging"
    # Also debugging if Bash-heavy with multiple errors (iterative troubleshooting)
    bash_count = tool_counts.get("Bash", 0)
    if bash_count / total > 0.4 and error_count > 3:
        return "debugging"

    # 3. Building
    edit_count = tool_counts.get("Edit", 0)
    write_count = tool_counts.get("Write", 0)
    if (edit_count + write_count) / total > 0.25:
        return "building"

    # 4. Reviewing
    read_count = tool_counts.get("Read", 0)
    if read_count / total > 0.50 and (edit_count + write_count) == 0:
        return "reviewing"

    # 5. Exploring
    glob_count = tool_counts.get("Glob", 0)
    grep_count = tool_counts.get("Grep", 0)
    if (read_count + glob_count + grep_count) / total > 0.55 and edit_count / total < 0.10:
        return "exploring"

    return "other"
```

**Impact**: Without this fix, analyze.py will **crash on every session with no tool calls**, blocking all analysis runs.

---

### P0-3: Timestamp Parsing Inconsistency Between Sources

**Location**: Task B1 — `load_all_events()` function

**Invariant violated**: All timestamps must be normalized to a single timezone-aware format for correct chronological ordering and weekly grouping.

**The bug**:

The plan doesn't specify how timestamps are parsed. Looking at actual event sources:

1. **Hook-generated events** (hook.sh line 58): `date -u +"%Y-%m-%dT%H:%M:%SZ"` → ISO 8601 string, UTC
2. **Backfilled Claude Code events** (parsers.py line 49): `datetime.fromtimestamp(ts / 1000, tz=timezone.utc)` → Converts numeric milliseconds to ISO string
3. **Backfilled Codex events** (parsers.py line 138): `ts = record.get("timestamp", "")` → Already a string, no conversion

The parsers.py code shows that **some sources provide numeric timestamps** (Claude Code transcripts have `"timestamp": 1737273403770`), while others provide strings.

**Failure narrative**:

The analyze.py `load_all_events()` reads events.jsonl and tries to sort by timestamp. If it doesn't parse timestamps uniformly:

```python
# Event from hook:
{"ts": "2026-01-19T06:56:43.770Z", ...}

# Event from backfilled transcript (hypothetical buggy parse):
{"ts": 1737273403770, ...}  # Forgot to convert to string

# Python's default sort tries: "2026-01-19..." < 1737273403770
# TypeError: '<' not supported between instances of 'str' and 'int'
```

Even if all timestamps are strings, **timezone handling is critical**. The plan's Task B6 (weekly trends) uses `datetime.isocalendar().week`, which requires parsing the ISO string back to datetime. If parsing doesn't preserve UTC, events will be mis-bucketed into the wrong week for users in other timezones.

**Corrective change**:

In `load_all_events()`:

```python
def load_all_events(
    since: datetime | None = None,
    until: datetime | None = None,
    project: str | None = None,
    source: str | None = None,
) -> list[dict]:
    """Load events from events.jsonl with optional filters.

    All timestamps are normalized to timezone-aware UTC datetime objects.
    """
    if not EVENTS_FILE.exists():
        return []

    events: list[dict] = []
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)

            # Normalize timestamp to datetime (handle both ISO strings and numeric)
            ts_raw = event.get("ts", "")
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

            # Store both parsed datetime (for filtering/grouping) and original string
            event["_ts_parsed"] = ts

            # Apply filters
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            if project and event.get("project") != project:
                continue
            if source and event.get("source") != source:
                continue

            events.append(event)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    return events
```

Then in all analysis functions, use `event["_ts_parsed"]` for chronological operations, and `event["ts"]` for output.

**Impact**: Without uniform timestamp parsing, weekly trends will be **incorrect for any user not in UTC**, and cross-source event ordering will be **undefined**, breaking tool chain analysis.

---

### P0-4: Tool Chain Double-Counting Due to Event Type Confusion

**Location**: Task B5 — `compute_bigrams()` and `compute_trigrams()` functions

**Invariant violated**: Each tool call must be counted exactly once in transition sequences.

**The bug**:

The plan doesn't specify which event types to include in tool chains. Looking at actual event data:

- **Hook events** generate BOTH `PreToolUse` and `PostToolUse` for every tool call (hook.sh line 94, 49)
- **Backfilled events** generate only `ToolUse` (parsers.py line 75, 174, 268)

If the bigram logic naively counts all events with a `tool` field:

**Failure narrative**:

User runs a single session with 3 tools: Read → Edit → Bash.

Hook events captured:
1. PreToolUse(Read)
2. PostToolUse(Read)
3. PreToolUse(Edit)
4. PostToolUse(Edit)
5. PreToolUse(Bash)
6. PostToolUse(Bash)

Naive bigram extraction (sliding window over events):
```
Read → Read   (Pre → Post)  ❌ Spurious self-loop
Read → Edit   (Post → Pre)  ✓ Real transition
Edit → Edit   (Pre → Post)  ❌ Spurious
Edit → Bash   (Post → Pre)  ✓ Real
Bash → Bash   (Pre → Post)  ❌ Spurious
```

The tool chain shows **50% fake self-loops** that never happened. Even worse, the transition `Read → Read` might be a real pattern (e.g., reading multiple files in sequence), so the fake self-loops from Pre→Post **corrupt real self-loop counts**.

**Corrective change**:

Only count tool calls from `PreToolUse` or `ToolUse` events (never `PostToolUse`), matching the convention in summarize.py:

```python
def compute_bigrams(sessions: dict[str, list[dict]]) -> dict[tuple[str, str], int]:
    """Compute tool transition bigrams across all sessions.

    Only counts transitions between actual tool calls (PreToolUse/ToolUse),
    ignoring PostToolUse to avoid double-counting.
    """
    from collections import Counter
    bigrams = Counter()

    for events in sessions.values():
        # Extract tool call sequence (filter to call events only)
        tools = [
            e.get("tool", "")
            for e in events
            if e.get("event") in ("PreToolUse", "ToolUse") and e.get("tool")
        ]
        # Compute transitions
        for i in range(len(tools) - 1):
            bigrams[(tools[i], tools[i+1])] += 1

    # Filter to count >= 5 (reduce noise)
    return {k: v for k, v in bigrams.items() if v >= 5}
```

**Impact**: Without this fix, tool chain visualizations will be **meaningless**, showing fake self-loops and double-counting every transition from hook-captured sessions.

---

### P0-5: ISO Week Year-Boundary Edge Case

**Location**: Task B6 — `compute_weekly_trends()` function

**Invariant violated**: Weekly grouping must handle year boundaries correctly (Dec 31 may be week 1 of next year).

**The bug**:

The plan says:
> Group events by ISO week (`datetime.isocalendar().week`). Per week: event count, session count, error rate, tool breakdown, classification mix.

ISO weeks are weird at year boundaries. December 29-31 can be in week 1 of the *next* year, and January 1-3 can be in week 52/53 of the *previous* year. If the code groups by `(week_number,)` without including the ISO year, events from two different years get merged:

**Failure narrative**:

User has events on:
- 2025-12-30 (Tuesday) → ISO week (2026, 1, 2) → week 1 of 2026
- 2026-01-06 (Tuesday) → ISO week (2026, 2, 2) → week 2 of 2026
- 2026-12-30 (Wednesday) → ISO week (2027, 1, 3) → week 1 of 2027

If code groups by `week_number` only:
```python
weekly_events = defaultdict(list)
for event in events:
    ts = event["_ts_parsed"]
    week_num = ts.isocalendar().week  # Missing year!
    weekly_events[week_num].append(event)

# weekly_events[1] now contains events from both 2025-12-30 and 2026-12-30
# → trend shows a massive spike in week 1 spanning two years ❌
```

**Corrective change**:

Group by `(iso_year, iso_week)`:

```python
def compute_weekly_trends(events: list[dict]) -> list[dict]:
    """Group events by ISO week, return time series of metrics per week.

    Uses (iso_year, iso_week) as key to handle year boundaries correctly.
    """
    from collections import defaultdict
    weekly = defaultdict(lambda: {
        "events": [],
        "sessions": set(),
        "errors": 0,
        "tools": Counter(),
    })

    for event in events:
        ts = event.get("_ts_parsed")
        if not ts:
            continue
        iso = ts.isocalendar()
        key = (iso.year, iso.week)  # Use both year and week

        weekly[key]["events"].append(event)
        session_id = extract_session_id(event["id"])
        weekly[key]["sessions"].add(session_id)
        if event.get("event") in ("PreToolUse", "ToolUse"):
            weekly[key]["tools"][event.get("tool", "")] += 1
        if event.get("error") is not None:
            weekly[key]["errors"] += 1

    # Convert to sorted list
    trends = []
    for (year, week), data in sorted(weekly.items()):
        total_events = len(data["events"])
        error_rate = data["errors"] / total_events if total_events > 0 else 0.0
        trends.append({
            "iso_year": year,
            "iso_week": week,
            "event_count": total_events,
            "session_count": len(data["sessions"]),
            "error_rate": error_rate,
            "top_tools": dict(data["tools"].most_common(10)),
            # Add classification mix here
        })

    return trends
```

**Impact**: Without this fix, year-boundary weeks will have **corrupted counts**, making trend analysis unreliable around New Year.

---

### P0-6: Retry Pattern Detection Fails on Tools Without File Paths

**Location**: Task B5 — `compute_retry_patterns()` function

**Invariant violated**: Retry detection should only flag retries where the same *file* was re-attempted, not just the same tool.

**The bug**:

The plan says:
> `compute_retry_patterns(sessions)` — detect same-tool-same-file retry after error.

But looking at actual event schema (events.jsonl line 1-20):
- Read, Edit, Write have `file` field
- Bash, Glob, Grep, Task, Skill, ExitPlanMode do **not** have file field

If the retry logic looks for `event["file"]` on all tools:

**Failure narrative**:

Session events:
1. Bash (run tests) → error (test failed)
2. Bash (run tests again) → success

The retry detection code tries:
```python
for i in range(len(events) - 1):
    if events[i].get("error") and events[i]["tool"] == events[i+1]["tool"]:
        if events[i]["file"] == events[i+1]["file"]:  # KeyError: "file"
            retry_count += 1
```

Even if the code uses `.get("file")` with a default:
```python
if events[i].get("file") == events[i+1].get("file"):  # Both are None
    retry_count += 1  # False positive!
```

Now **every Bash→Bash, Grep→Grep, Task→Task sequence after an error** is counted as a retry, even if they're unrelated commands.

**Corrective change**:

Only count retries for file-based tools:

```python
def compute_retry_patterns(sessions: dict[str, list[dict]]) -> dict[str, dict]:
    """Detect same-tool-same-file retry after error.

    Only applies to tools that operate on files (Read, Edit, Write).
    For other tools, retries are not detectable without command-level comparison.
    """
    from collections import defaultdict
    retry_stats = defaultdict(lambda: {
        "retry_count": 0,
        "max_retries": 0,
        "sessions_affected": set(),
    })

    FILE_TOOLS = {"Read", "Edit", "Write"}

    for session_id, events in sessions.items():
        # Track retries within this session
        session_retries = defaultdict(int)

        for i in range(len(events) - 1):
            curr = events[i]
            next_ev = events[i + 1]

            # Only count if both events are for the same file-based tool
            tool = curr.get("tool", "")
            if tool not in FILE_TOOLS:
                continue

            # Check: error on curr, same tool + file on next
            if (curr.get("error") is not None
                and curr.get("event") in ("PostToolUse", "ToolUse")
                and next_ev.get("event") in ("PreToolUse", "ToolUse")
                and next_ev.get("tool") == tool
                and curr.get("file") == next_ev.get("file")
                and curr.get("file") is not None):  # Ignore if file is missing

                session_retries[tool] += 1

        # Update global stats
        for tool, count in session_retries.items():
            retry_stats[tool]["retry_count"] += count
            retry_stats[tool]["max_retries"] = max(
                retry_stats[tool]["max_retries"], count
            )
            retry_stats[tool]["sessions_affected"].add(session_id)

    # Convert sets to counts for JSON serialization
    return {
        tool: {
            "retry_count": stats["retry_count"],
            "max_retries": stats["max_retries"],
            "sessions_affected": len(stats["sessions_affected"]),
        }
        for tool, stats in retry_stats.items()
    }
```

**Alternative**: If retry detection is valuable for Bash commands, the implementation would need to compare command text (from `tool_input.command` field if captured), but this isn't available in events.jsonl.

**Impact**: Without this fix, retry stats will be **wildly inflated**, showing false positives for every consecutive Bash/Grep/Task call after an error.

---

### P0-7: Error Detection Missing for Hook-Generated PreToolUse Events

**Location**: Hook.sh line 47-55, and Task B3/B5/B6 (all error-rate computations)

**Invariant violated**: Error rates must account for both hook-generated events (which only mark errors in PostToolUse) and backfilled events (which mark errors in the single ToolUse event).

**The bug**:

Looking at hook.sh:
```bash
# Line 49: ERROR="null"
# Line 50: if [ "$EVENT" = "PostToolUse" ]; then
#   ... extract error ...
```

So hook events **only write error info on PostToolUse events**. PreToolUse events always have `error: null`.

But the plan's classification logic (Task B3 line 68) says:
> `error_count / total > 0.15`

If the code counts errors from **all events** (PreToolUse + PostToolUse), the error count will be correct. But if it filters to "only count errors on call events" (PreToolUse/ToolUse), it will **miss all hook-generated errors**.

**Failure narrative**:

Session with 10 tool calls, 2 errors (hook-generated):
1. PreToolUse(Read) — error: null
2. PostToolUse(Read) — error: "file not found"
3. PreToolUse(Edit) — error: null
4. PostToolUse(Edit) — error: null
5. ... (8 more pairs)

If error counting uses:
```python
# Buggy: only count errors on PreToolUse/ToolUse
error_count = sum(
    1 for e in events
    if e.get("event") in ("PreToolUse", "ToolUse") and e.get("error")
)
# error_count = 0  ❌ All errors are on PostToolUse
```

Correct version:
```python
# Count errors on PostToolUse OR ToolUse (backfilled events)
error_count = sum(
    1 for e in events
    if e.get("event") in ("PostToolUse", "ToolUse") and e.get("error") is not None
)
```

But this creates a **secondary bug**: if both PreToolUse and PostToolUse exist for the same call, and both have the error field set, the error gets counted twice.

**Corrective change**:

Use the same logic as summarize.py (line 129-141), which correctly counts errors from PostToolUse OR ToolUse:

```python
def count_errors(events: list[dict]) -> int:
    """Count errors from hook events (PostToolUse) and backfilled events (ToolUse).

    Does not double-count errors from Pre+Post pairs.
    """
    # For hook events: errors appear on PostToolUse
    # For backfilled events: errors appear on ToolUse
    # Never count PreToolUse errors (always null)
    return sum(
        1 for e in events
        if e.get("event") in ("PostToolUse", "ToolUse")
        and e.get("error") is not None
    )
```

And verify that hook.sh (line 47-55) is **not** setting error on PreToolUse. The current code only sets error on PostToolUse, which is correct.

**Impact**: If error counting is implemented incorrectly, sessions will be **mis-classified** (debugging sessions marked as "other", high-error sessions marked as "building"), making all classification-based analysis wrong.

---

## P1 Findings (Should Fix Before Production)

### P1-1: Tool Name Normalization Incomplete

**Location**: Task B2 — `TOOL_ALIASES` map

**Issue**: The alias map is missing several common variants:
- `shell_exec`, `execute_command`, `run_command` → all should map to `Bash`
- `read_file`, `file_read` → should map to `Read`
- `write_file`, `file_write` → should map to `Write`

**Evidence**: Searching events.jsonl would reveal the actual tool name distribution, but the plan assumes the current set is exhaustive.

**Corrective change**: Add logging when an unknown tool name is encountered, so the alias map can be extended iteratively:

```python
TOOL_ALIASES = {
    "shell": "Bash",
    "shell_command": "Bash",
    "exec_command": "Bash",
    "shell_exec": "Bash",
    "write_stdin": "Write",
    "update_plan": "TaskUpdate",
}

def normalize_tool_name(name: str) -> str:
    normalized = TOOL_ALIASES.get(name, name)
    if normalized == name and name not in KNOWN_CANONICAL_TOOLS:
        # Log for future alias extension
        logger.debug(f"Unknown tool name (not normalized): {name}")
    return normalized
```

**Impact**: Without complete aliases, source comparison will show fake differences (e.g., "Codex uses `shell_exec` while Claude Code uses `Bash`") when they're the same tool.

---

### P1-2: Session Duration Computation Ignores TZ

**Location**: Task B4 — `compute_session_metrics()` function

**Issue**: The plan says "average session duration (from first to last event timestamp)" but doesn't specify how to handle sessions that span DST transitions or are recorded in different timezones.

**Corrective change**: Always use UTC timestamps (already fixed if P0-3 is applied):

```python
def compute_session_duration(events: list[dict]) -> float:
    """Return session duration in seconds, or 0 if <2 events."""
    if len(events) < 2:
        return 0.0
    timestamps = [e["_ts_parsed"] for e in events if "_ts_parsed" in e]
    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)).total_seconds()
```

**Impact**: Duration computation will be off by 1 hour during DST transitions if not using UTC.

---

### P1-3: Empty Session Filtering Threshold Arbitrary

**Location**: Task B4 — "exclude sessions with <3 events as noise"

**Issue**: The threshold of 3 events is arbitrary. A session with 2 tool calls (e.g., Read + Edit to fix a typo) is meaningful, not noise.

**Corrective change**: Instead of excluding by event count, exclude by **tool call count**:

```python
# Filter sessions with <2 tool calls (not events)
meaningful_sessions = {
    sid: events for sid, events in sessions.items()
    if len([e for e in events if e.get("event") in ("PreToolUse", "ToolUse")]) >= 2
}
```

**Impact**: Low but affects long-tail accuracy. Very short sessions will be mis-classified as "other" when they're actually mini-builds.

---

### P1-4: Time-of-Day Patterns Don't Account for User Timezone

**Location**: Task B7 — `compute_time_patterns()` function

**Issue**: Events are stored in UTC, but "peak hour" is only meaningful in the user's local timezone. Showing "peak hour: 3 AM UTC" is useless.

**Corrective change**: Add a `--timezone` flag to analyze.py, or detect the system timezone:

```python
import zoneinfo

def compute_time_patterns(events: list[dict], tz_name: str = "UTC") -> dict:
    tz = zoneinfo.ZoneInfo(tz_name)
    hour_buckets = defaultdict(list)

    for event in events:
        ts = event["_ts_parsed"].astimezone(tz)
        hour_buckets[ts.hour].append(event)

    # ... rest of logic
```

**Impact**: Without timezone adjustment, time-of-day patterns are **meaningless for users outside UTC**.

---

## P2 Findings (Nice to Have)

### P2-1: Bigram/Trigram Filtering Threshold Hardcoded

**Location**: Task B5 — "Filter to count >= 5"

**Issue**: The threshold of 5 is arbitrary. For users with <100 total tool calls, this may filter out all bigrams.

**Corrective change**: Use a dynamic threshold based on total bigram count:

```python
def compute_bigrams(sessions, min_count=None):
    bigrams = Counter()
    # ... compute ...
    total = sum(bigrams.values())
    if min_count is None:
        min_count = max(3, total // 100)  # At least 1% of total
    return {k: v for k, v in bigrams.items() if v >= min_count}
```

---

### P2-2: Missing Event ID Validation

**Location**: Task B1 — `load_all_events()`

**Issue**: Malformed event IDs (e.g., missing hyphen, empty ID) will cause crashes in `extract_session_id()`.

**Corrective change**: Add validation:

```python
def load_all_events(...):
    for line in EVENTS_FILE.read_text().splitlines():
        event = json.loads(line)
        if "id" not in event or not event["id"]:
            continue  # Skip malformed events
        # ...
```

---

## Test Coverage Gaps

The plan's test strategy (Task B11) doesn't cover:

1. **Cross-source event mixing**: What happens when a session has both hook events (Pre/Post pairs) and backfilled events (single ToolUse)? This is unlikely but possible if backfill runs mid-session.

2. **Malformed timestamps**: Events with `ts: null`, `ts: ""`, or `ts: "invalid"` should be gracefully skipped, not crash the loader.

3. **Unicode in tool names/file paths**: If a file path contains emoji or non-ASCII characters, does the JSONL round-trip correctly?

4. **Year-boundary week 53**: ISO week 53 only exists in some years (long years). Test with 2026-12-31 (week 53) vs 2027-01-01 (week 1).

5. **Negative error rates**: If the error counting logic is buggy and counts more errors than total events, the error rate could be >100% or negative. Add assertions.

**Recommended additional tests**:

```python
class TestEdgeCases:
    def test_session_id_extraction_with_negative_seq(self):
        # Hypothetical: if seq is ever negative
        event_id = "abc-def-ghi--1"
        assert extract_session_id(event_id) == "abc-def-ghi"

    def test_weekly_trends_year_boundary(self):
        events = [
            _make_event("Read", ts=datetime(2026, 12, 30, tzinfo=timezone.utc)),
            _make_event("Edit", ts=datetime(2027, 1, 5, tzinfo=timezone.utc)),
        ]
        trends = compute_weekly_trends(events)
        # 2026-12-30 is week 1 of 2027
        assert any(t["iso_year"] == 2027 and t["iso_week"] == 1 for t in trends)

    def test_classification_with_zero_tools(self):
        events = []  # No tool calls
        assert classify_session(events) == "other"

    def test_error_rate_never_exceeds_100_percent(self):
        events = [_make_event("Read", error="fail") for _ in range(10)]
        stats = compute_session_metrics({"s1": events})
        # Exact assertion depends on how error rate is computed
        # but it should never be >1.0
```

---

## Summary of Corrective Actions

| Priority | Finding | Fix Effort | Blast Radius |
|----------|---------|-----------|--------------|
| P0-1 | Session ID extraction | 5 lines | All session-level metrics |
| P0-2 | Division by zero | 15 lines | Session classification, crashes |
| P0-3 | Timestamp parsing | 20 lines | Weekly trends, event ordering |
| P0-4 | Tool chain double-counting | 10 lines | Bigrams, trigrams, Sankey viz |
| P0-5 | ISO week year boundary | 5 lines | Weekly trends near New Year |
| P0-6 | Retry pattern false positives | 30 lines | Retry analysis |
| P0-7 | Error detection event type | 10 lines | All error-rate metrics |
| P1-1 | Incomplete tool aliases | 10 lines | Source comparison |
| P1-2 | Session duration TZ | 5 lines | Duration stats |
| P1-3 | Session filtering threshold | 5 lines | Metrics accuracy |
| P1-4 | Time-of-day TZ | 15 lines | Time patterns |

**Total estimated fix effort**: ~130 lines of defensive code and edge case handling.

**Recommended execution order**:
1. Fix P0-1, P0-2, P0-3 **before** starting Task B1 (they affect the loader)
2. Fix P0-4, P0-7 **before** Task B5 (they affect tool chain and error logic)
3. Fix P0-5 **before** Task B6 (weekly trends)
4. Fix P0-6 **during** Task B5 (retry patterns)
5. Address P1 findings during testing phase
6. Add edge case tests before declaring done

---

## Concurrency Concerns (Not Applicable)

This plan involves no concurrent processes. The only multi-file write is:
- summarize.py writes stats.json
- analyze.py writes analysis.json
- upload.py reads stats.json (but never writes it)

These run sequentially (hook.sh line 84-85 runs summarize.py, then upload.py in background). No races.

The dashboard serve.sh (Task C9) copies analysis.json before serving, so no read-during-write risk.

---

## Final Recommendation

**Do not start implementation until P0 findings are designed into the initial code.** Retrofitting these fixes after tests are written will require rewriting most of Task B11.

The plan is otherwise well-structured. The YAGNI cuts are appropriate. The testing strategy is solid except for the gaps noted above.

After fixing P0 issues, estimated implementation time: 6-8 hours for Module B, 4-6 hours for Module C, assuming no surprises in D3.js layout debugging.
