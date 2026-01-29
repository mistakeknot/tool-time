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

# Error messages that indicate user rejection, not tool failure
USER_REJECTION_PREFIXES = (
    "The user doesn't want to proceed",
    "Permission to use",
    "User denied",
    "User rejected",
    "User cancelled",
)


def is_user_rejection(error: str | None) -> bool:
    """Check if an error is a user rejection rather than a tool failure."""
    if not error:
        return False
    return any(error.startswith(prefix) for prefix in USER_REJECTION_PREFIXES)


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


def parse_mcp_server(tool_name: str) -> str | None:
    """Extract MCP server name from tool name like 'mcp__server__tool'."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) >= 3:
        return parts[1]
    return None


def analyze(events: list[dict]) -> dict:
    tool_counts: Counter = Counter()
    error_counts: Counter = Counter()
    rejection_counts: Counter = Counter()
    mcp_server_counts: Counter = Counter()
    skill_counts: Counter = Counter()
    total_errors = 0
    total_rejections = 0

    # Track tool calls with file paths for Edit-without-Read detection
    file_ops: list[tuple[str, str | None]] = []  # (tool_name, file_path)
    files_read: set[str] = set()  # files Read in current session window
    edit_without_read: int = 0

    for ev in events:
        tool = ev.get("tool", "")
        event_type = ev.get("event", "")

        if event_type in ("PreToolUse", "PostToolUse", "ToolUse") and tool:
            if event_type in ("PreToolUse", "ToolUse"):
                tool_counts[tool] += 1
                file_path = ev.get("file")
                file_ops.append((tool, file_path))

                # Track MCP server usage
                server = parse_mcp_server(tool)
                if server:
                    mcp_server_counts[server] += 1

                # Track skill usage (tool="Skill", skill name in "skill" field)
                if tool == "Skill":
                    skill_name = ev.get("skill", "unknown")
                    skill_counts[skill_name or "unknown"] += 1

            error = ev.get("error")
            if event_type in ("PostToolUse", "ToolUse") and error is not None:
                if is_user_rejection(error):
                    rejection_counts[tool] += 1
                    total_rejections += 1
                else:
                    error_counts[tool] += 1
                    total_errors += 1

    # Detect Edit-without-Read using file paths
    # Track which files have been Read; flag Edit on a file not yet Read
    files_read = set()
    for tool, file_path in file_ops:
        if tool == "Read" and file_path:
            files_read.add(file_path)
        elif tool == "Edit" and file_path:
            if file_path not in files_read:
                edit_without_read += 1
            # After editing, keep it in files_read (still known)
        elif tool == "Write" and file_path:
            # Writing a new file counts as "knowing" it
            files_read.add(file_path)

    unique_tools = set(t for t, _ in file_ops)

    return {
        "tool_counts": tool_counts,
        "error_counts": error_counts,
        "rejection_counts": rejection_counts,
        "mcp_server_counts": mcp_server_counts,
        "skill_counts": skill_counts,
        "total_errors": total_errors,
        "total_rejections": total_rejections,
        "total_calls": sum(tool_counts.values()),
        "edit_without_read": edit_without_read,
        "unique_tools": len(unique_tools),
    }


def generate_suggestions(stats: dict) -> list[dict]:
    suggestions = []

    if stats["edit_without_read"] > 2:
        suggestions.append({
            "type": "claude_md",
            "priority": "high",
            "text": "Always Read files before Edit — detected "
            f"{stats['edit_without_read']} Edit calls on files not previously Read.",
        })

    # High error rate on a specific tool (excluding user rejections)
    for tool, count in stats["error_counts"].most_common(3):
        total = stats["tool_counts"].get(tool, 1)
        rate = count / total if total else 0
        if rate > 0.3 and count >= 3:
            suggestions.append({
                "type": "claude_md",
                "priority": "medium",
                "text": f"Tool '{tool}' has a {rate:.0%} error rate "
                f"({count}/{total} calls, excluding user rejections). "
                "Consider adding guidance.",
            })

    # High rejection rate (user frequently denies a tool)
    for tool, count in stats["rejection_counts"].most_common(3):
        total = stats["tool_counts"].get(tool, 1)
        rate = count / total if total else 0
        if rate > 0.3 and count >= 5:
            suggestions.append({
                "type": "claude_md",
                "priority": "low",
                "text": f"Tool '{tool}' is rejected by user {rate:.0%} of the time "
                f"({count}/{total} calls). Consider adding constraints to reduce "
                "unnecessary invocations.",
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
        print(f"tool-time: {stats['total_errors']} errors detected (excl. {stats['total_rejections']} user rejections)")
    if stats["edit_without_read"]:
        print(f"tool-time: {stats['edit_without_read']}x Edit-without-Read (file-level)")
    if stats["mcp_server_counts"]:
        mcp_summary = ", ".join(
            f"{s}({c})" for s, c in stats["mcp_server_counts"].most_common(5)
        )
        print(f"tool-time: MCP servers: {mcp_summary}")
    if stats["skill_counts"]:
        skill_summary = ", ".join(
            f"{s}({c})" for s, c in stats["skill_counts"].most_common(5)
        )
        print(f"tool-time: Skills: {skill_summary}")


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
