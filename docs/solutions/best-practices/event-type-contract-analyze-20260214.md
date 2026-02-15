---
module: analyze.py
date: 2026-02-14
problem_type: best_practice
component: tooling
symptoms:
  - "Tool chain bigrams show 50% fake self-loops (Read→Read, Edit→Edit) from Pre→Post event pairs"
  - "Error counts are zero because errors only appear on PostToolUse, not PreToolUse"
  - "Retry detection produces false positives on Bash→Bash sequences (no file field to distinguish)"
  - "Session classification divides by zero on sessions with only lifecycle events"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [event-types, hook-events, double-counting, pre-post-pairs, analytics, tool-chains]
---

# Best Practice: Event Type Contract for Hook-Based Analytics

## Problem

When building analytics on Claude Code hook events (`events.jsonl`), three different event sources produce different event structures. Naively processing all events causes silent data corruption: double-counted tool calls, fake self-loops in chain analysis, missed errors, and false retry detection.

## Environment

- Module: analyze.py (tool-time deep analytics engine)
- Affected Component: All analytics functions (classification, chains, trends, error rates)
- Data: 333K events from 3 sources (Claude Code hooks, Codex backfill, OpenClaw backfill)
- Date: 2026-02-14

## Symptoms

- Sankey diagram of tool chains shows thick "Read→Read" and "Edit→Edit" self-loops that never actually happened — these are artifacts from PreToolUse→PostToolUse pairs
- Error rates compute as 0% because code checks `PreToolUse` events for errors (they're always null — errors only appear on `PostToolUse`)
- Retry pattern detection reports "Bash retried 45 times" when consecutive Bash calls are unrelated commands (no file field to distinguish them)
- `ZeroDivisionError` crashes on sessions with only `SessionStart`/`SessionEnd` events (no tool calls)

## What Didn't Work

**Direct solution:** The flux-drive correctness review identified all 7 failure modes before implementation. The event type contract was designed upfront rather than discovered through debugging. This document captures the contract for reuse.

## Solution

### The Event Type Contract

Every analytics function must use exactly one of these helper predicates:

```python
def is_call_event(event: dict) -> bool:
    """True for events that represent a tool invocation (not result).
    Use for: counting tool calls, building chain sequences, classification ratios."""
    return event.get("event") in ("PreToolUse", "ToolUse")

def is_error_event(event: dict) -> bool:
    """True for events carrying error info.
    Use for: counting errors, computing error rates."""
    return (
        event.get("event") in ("PostToolUse", "ToolUse")
        and event.get("error") is not None
    )
```

### Why Two Predicates?

The three event sources produce different structures:

| Source | Tool Call Event | Error Event | Notes |
|--------|----------------|-------------|-------|
| **Claude Code hooks** | `PreToolUse` | `PostToolUse` (with error) | Hooks emit BOTH Pre and Post for every call |
| **Codex backfill** | `ToolUse` | `ToolUse` (with error) | Single event per call |
| **OpenClaw backfill** | `ToolUse` | `ToolUse` (with error) | Single event per call |

### Application Rules

| Analysis Function | Event Filter | Why |
|-------------------|-------------|-----|
| **Count tool calls** | `is_call_event()` | Counts Pre OR ToolUse, never both |
| **Count errors** | `is_error_event()` | Errors on Post OR ToolUse, never Pre |
| **Bigrams/trigrams** | `is_call_event()` only | Prevents fake self-loops from Pre→Post pairs |
| **Classification ratios** | `is_call_event()` for totals, `is_error_event()` for errors | Different denominators |
| **Retry detection** | `is_error_event()` on current + `is_call_event()` on next | Error→retry sequence |

### Critical Guard: Retry Detection Scope

Only detect retries for **file-based tools** (Read, Edit, Write). Other tools have no `file` field, so consecutive calls look like retries when they're not:

```python
FILE_TOOLS = {"Read", "Edit", "Write"}

# WRONG: all tools — Bash→Bash after error = false positive
if curr_error and next_tool == curr_tool:
    retry_count += 1

# RIGHT: file-based only, with file match
if (curr_tool in FILE_TOOLS
    and is_error_event(curr)
    and is_call_event(nxt)
    and nxt.get("tool") == curr_tool
    and curr.get("file") is not None
    and curr.get("file") == nxt.get("file")):
    retry_count += 1
```

### Critical Guard: Division by Zero

Sessions may have zero call events (only lifecycle events, or only PostToolUse). Always guard:

```python
def classify_session(events):
    call_events = [e for e in events if is_call_event(e)]
    total = len(call_events)
    if total == 0:
        return "other"
    # Now safe to divide by total
```

## Why This Works

1. **PreToolUse + PostToolUse = one tool call**: Hooks fire both events for the same invocation. Counting both doubles every call. Counting only `PreToolUse` (or `ToolUse` for backfill) counts exactly once.

2. **Errors only on PostToolUse**: Hook.sh only sets the `error` field on `PostToolUse` events (line 49-55). `PreToolUse` events always have `error: null`. Checking `PreToolUse` for errors finds nothing.

3. **Chain analysis needs call-only sequences**: If you include `PostToolUse` in bigrams, every `[PreToolUse(Read), PostToolUse(Read)]` pair creates a fake `Read→Read` transition that never happened.

4. **File field absence**: Only Read, Edit, Write have a `file` field. Bash, Grep, Glob, Task, etc. don't. Using `event.get("file")` on these returns `None`, and `None == None` is `True`, causing every consecutive same-tool call to match as a "retry."

## Prevention

- **Always use the helper predicates** — never check `event["event"]` directly in analytics code
- **Test with mixed event sources** — include both hook events (Pre+Post pairs) and backfill events (single ToolUse) in test data
- **Test the zero-call-events edge case** — sessions with only SessionStart/SessionEnd or only PostToolUse events
- **For retry detection, require a non-None file field match** — never match on `None == None`
- **When adding new analytics functions**, consult this contract before deciding which events to count

## Related Issues

No related issues documented yet.
