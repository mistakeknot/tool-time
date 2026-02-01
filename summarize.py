#!/usr/bin/env python3
"""tool-time summarizer.

Reads events from ~/.claude/tool-time/events.jsonl, computes tool usage
statistics, and writes stats.json. No opinions, no thresholds — just data
for an agent to reason about.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"
STATS_FILE = DATA_DIR / "stats.json"
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


def load_events(
    days: int = LOOKBACK_DAYS,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Load recent events, optionally filtered by project path."""
    if not EVENTS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events: list[dict[str, Any]] = []
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            # Filter by project after parse (correctness over micro-optimization)
            if project and ev.get("project") != project:
                continue
            ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
            if ts >= cutoff:
                events.append(ev)
        except (json.JSONDecodeError, KeyError):
            continue
    return events


def scan_installed_plugins(
    settings_file: Path | None = None,
) -> list[str]:
    """Read installed plugins from Claude settings."""
    if settings_file is None:
        settings_file = Path.home() / ".claude" / "settings.json"

    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
            plugins = settings.get("enabledPlugins", {})
            if isinstance(plugins, dict):
                return sorted(plugins.keys())
        except (json.JSONDecodeError, OSError):
            pass

    return []


def compute_tool_statistics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-tool call/error/rejection counts, skill counts,
    MCP server stats, and session-scoped edit-without-read count."""
    tool_counts: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    tool_rejections: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    mcp_server_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "errors": 0}
    )

    # Group file ops by session for edit-without-read detection
    session_file_ops: dict[str, list[tuple[str, str | None]]] = defaultdict(list)

    for ev in events:
        tool = ev.get("tool", "")
        event_type = ev.get("event", "")
        if not tool:
            continue
        model = ev.get("model")
        if model:
            model_counts[model] += 1

        # Count calls from PreToolUse or ToolUse events
        if event_type in ("PreToolUse", "ToolUse"):
            tool_counts[tool] += 1
            file_path = ev.get("file")
            session_id = ev["id"].rsplit("-", 1)[0]
            session_file_ops[session_id].append((tool, file_path))

            # Skills
            skill_name = ev.get("skill")
            if skill_name:
                skill_counts[skill_name] += 1

            # MCP servers — parse from mcp__<server>__<tool> pattern
            if tool.startswith("mcp__"):
                parts = tool.split("__", 2)
                if len(parts) >= 3 and parts[1]:
                    mcp_server_stats[parts[1]]["calls"] += 1

        # Count errors from PostToolUse or ToolUse events
        if event_type in ("PostToolUse", "ToolUse"):
            error = ev.get("error")
            if error is not None:
                if is_user_rejection(error):
                    tool_rejections[tool] += 1
                else:
                    tool_errors[tool] += 1
                    # Track MCP server errors
                    if tool.startswith("mcp__"):
                        parts = tool.split("__", 2)
                        if len(parts) >= 3 and parts[1]:
                            mcp_server_stats[parts[1]]["errors"] += 1

    # Session-scoped edit-without-read
    edit_without_read_count = 0
    for ops in session_file_ops.values():
        files_read: set[str] = set()
        for tool, file_path in ops:
            if tool == "Read" and file_path:
                files_read.add(file_path)
            elif tool == "Write" and file_path:
                files_read.add(file_path)
            elif tool == "Edit" and file_path:
                if file_path not in files_read:
                    edit_without_read_count += 1

    # Build per-tool stats
    tools: dict[str, dict[str, int]] = {}
    for tool in sorted(tool_counts, key=tool_counts.get, reverse=True):
        tools[tool] = {
            "calls": tool_counts[tool],
            "errors": tool_errors.get(tool, 0),
            "rejections": tool_rejections.get(tool, 0),
        }

    # Build skill stats
    skills: dict[str, dict[str, int]] = {}
    for name in sorted(skill_counts, key=skill_counts.get, reverse=True):
        skills[name] = {"calls": skill_counts[name]}

    # Build MCP server stats (only include servers with actual usage)
    mcp_servers: dict[str, dict[str, int]] = {}
    for name in sorted(mcp_server_stats, key=lambda n: mcp_server_stats[n]["calls"], reverse=True):
        mcp_servers[name] = dict(mcp_server_stats[name])

    # Most common model, or null
    model = model_counts.most_common(1)[0][0] if model_counts else None

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_events": len(events),
        "tools": tools,
        "edit_without_read_count": edit_without_read_count,
        "model": model,
        "skills": skills,
        "mcp_servers": mcp_servers,
        "installed_plugins": scan_installed_plugins(),
    }


def main() -> None:
    project = os.getcwd()
    events = load_events(project=project)
    stats = compute_tool_statistics(events)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(stats, indent=2) + "\n")
    print(str(STATS_FILE))


if __name__ == "__main__":
    main()
