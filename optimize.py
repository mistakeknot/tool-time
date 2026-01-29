#!/usr/bin/env python3
"""tool-time session-end analyzer.

Reads recent events from ~/.claude/tool-time/events.jsonl,
detects patterns, prints a 3-line summary, and writes suggestions
to pending-suggestions.json.
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"
SUGGESTIONS_FILE = DATA_DIR / "pending-suggestions.json"
LOOKBACK_DAYS = 7


def load_events(days: int = LOOKBACK_DAYS) -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = []
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
            if ts >= cutoff:
                events.append(ev)
        except (json.JSONDecodeError, KeyError):
            continue
    return events


def analyze(events: list[dict]) -> dict:
    tool_counts: Counter = Counter()
    error_counts: Counter = Counter()
    total_errors = 0
    sequences: list[str] = []  # ordered tool names for sequence detection
    edit_without_read: int = 0
    recent_tools: set[str] = set()

    for ev in events:
        tool = ev.get("tool", "")
        event_type = ev.get("event", "")

        if event_type in ("PreToolUse", "PostToolUse", "ToolUse") and tool:
            if event_type in ("PreToolUse", "ToolUse"):
                tool_counts[tool] += 1
                sequences.append(tool)

            if event_type in ("PostToolUse", "ToolUse") and ev.get("error") is not None:
                error_counts[tool] += 1
                total_errors += 1

    # Detect Edit-without-Read pattern
    for i, t in enumerate(sequences):
        if t == "Edit":
            preceding = sequences[max(0, i - 5) : i]
            if "Read" not in preceding:
                edit_without_read += 1

    # Track unique tools used
    recent_tools = set(sequences)

    return {
        "tool_counts": tool_counts,
        "error_counts": error_counts,
        "total_errors": total_errors,
        "total_calls": sum(tool_counts.values()),
        "edit_without_read": edit_without_read,
        "unique_tools": len(recent_tools),
    }


def generate_suggestions(stats: dict) -> list[dict]:
    suggestions = []

    if stats["edit_without_read"] > 2:
        suggestions.append({
            "type": "claude_md",
            "priority": "high",
            "text": "Always Read files before Edit — detected "
            f"{stats['edit_without_read']} Edit calls without a preceding Read.",
        })

    # High error rate on a specific tool
    for tool, count in stats["error_counts"].most_common(3):
        total = stats["tool_counts"].get(tool, 1)
        rate = count / total if total else 0
        if rate > 0.3 and count >= 3:
            suggestions.append({
                "type": "claude_md",
                "priority": "medium",
                "text": f"Tool '{tool}' has a {rate:.0%} error rate "
                f"({count}/{total} calls). Consider adding guidance.",
            })

    # Bash dominance (might indicate missing specialized tools)
    bash_count = stats["tool_counts"].get("Bash", 0)
    total = stats["total_calls"]
    if total > 10 and bash_count / total > 0.5:
        suggestions.append({
            "type": "claude_md",
            "priority": "low",
            "text": "Bash accounts for >50% of tool calls. "
            "Consider using specialized tools (Read, Edit, Grep) instead.",
        })

    return suggestions


def print_summary(stats: dict) -> None:
    top3 = ", ".join(
        f"{t}({c})" for t, c in stats["tool_counts"].most_common(3)
    )
    print(f"tool-time: {stats['total_calls']} calls across {stats['unique_tools']} tools | top: {top3}")
    if stats["total_errors"]:
        print(f"tool-time: {stats['total_errors']} errors detected")
    if stats["edit_without_read"]:
        print(f"tool-time: {stats['edit_without_read']}x Edit-without-Read detected")


def main() -> None:
    events = load_events()
    if not events:
        print("tool-time: no events in the last 7 days")
        return

    stats = analyze(events)
    print_summary(stats)

    suggestions = generate_suggestions(stats)
    if suggestions:
        SUGGESTIONS_FILE.write_text(json.dumps(suggestions, indent=2) + "\n")
        print(f"tool-time: {len(suggestions)} suggestion(s) written to {SUGGESTIONS_FILE}")
    elif SUGGESTIONS_FILE.exists():
        SUGGESTIONS_FILE.unlink()


if __name__ == "__main__":
    main()
