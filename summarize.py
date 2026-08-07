#!/usr/bin/env python3
"""tool-time summarizer.

Reads events from ~/.claude/tool-time/events.jsonl, computes tool usage
statistics, and writes stats.json. No opinions, no thresholds — just data
for an agent to reason about.
"""

import json
import os
import sys
import tempfile
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


def parse_event_ts(ts_raw: str | None) -> datetime | None:
    """Parse an event timestamp, returning None if missing or malformed."""
    if not ts_raw:
        return None
    try:
        return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None


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


def is_error_observable(event: dict[str, Any]) -> bool:
    """True when this event's `error` field can be trusted.

    Only transcript-derived events (`event: "ToolUse"`, written by
    backfill.py via parsers.py) carry error truth: session transcripts record
    an explicit `is_error` flag per tool_result.

    Hook-derived events (`event: "PostToolUse"`) never can. Claude Code does
    not fire PostToolUse when a tool fails, so the hook only ever observes
    successes — measured directly, and the reason the old payload-sniffing
    heuristic produced 20,517 false positives and 0 true ones. Counting a
    hook event as "no error" would be just as wrong as the old heuristic: it
    is not an observation of success, it is the absence of an observation.
    """
    return event.get("event") == "ToolUse"


def compute_tool_statistics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-tool call/error/rejection counts, skill counts,
    MCP server stats, and session-scoped edit-without-read count.

    `errors`/`rejections` are None for a tool with no error-observable events
    (see is_error_observable). None means "not measured", which is not the
    same fact as 0, and consumers must not render it as a rate.
    """
    tool_counts: Counter[str] = Counter()
    tool_observed: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    tool_rejections: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    mcp_server_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "errors": 0, "error_observed_calls": 0}
    )
    tool_last_used: dict[str, datetime] = {}
    mcp_last_used: dict[str, datetime] = {}

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
        source = ev.get("source")
        if source:
            source_counts[source] += 1

        # Calls are counted at PostToolUse (not PreToolUse) to avoid
        # double-counting if PreToolUse logging is ever added
        if event_type in ("PostToolUse", "ToolUse"):
            tool_counts[tool] += 1
            file_path = ev.get("file")

            # Skills — a historical hook bug wrote file paths into the
            # "skill" field. A path is not a skill: never count it as one
            # (path-like values would leak private paths downstream). When
            # such an event has no "file" key, the value IS the file path
            # the old hook misplaced — recover it for the edit-without-read
            # logic below.
            skill_name = ev.get("skill")
            if skill_name:
                if "/" in skill_name:
                    if "file" not in ev:
                        file_path = skill_name
                else:
                    skill_counts[skill_name] += 1

            session_id = ev["id"].rsplit("-", 1)[0]
            session_file_ops[session_id].append((tool, file_path))

            ts = parse_event_ts(ev.get("ts"))
            if ts is not None and (tool not in tool_last_used or ts > tool_last_used[tool]):
                tool_last_used[tool] = ts

            # MCP servers — parse from mcp__<server>__<tool> pattern
            server = None
            if tool.startswith("mcp__"):
                parts = tool.split("__", 2)
                if len(parts) >= 3 and parts[1]:
                    server = parts[1]
                    mcp_server_stats[server]["calls"] += 1
                    if ts is not None and (server not in mcp_last_used or ts > mcp_last_used[server]):
                        mcp_last_used[server] = ts

            # Errors and rejections ride on the same event, but only from a
            # source that can actually observe a failure. Events from the
            # hook path are skipped entirely rather than counted as clean —
            # they are unobserved, not successful.
            if is_error_observable(ev):
                tool_observed[tool] += 1
                if server:
                    mcp_server_stats[server]["error_observed_calls"] += 1
                error = ev.get("error")
                if error is not None:
                    if is_user_rejection(error):
                        tool_rejections[tool] += 1
                    else:
                        tool_errors[tool] += 1
                        # Track MCP server errors
                        if server:
                            mcp_server_stats[server]["errors"] += 1

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
    tools: dict[str, dict[str, Any]] = {}
    for tool in sorted(tool_counts, key=tool_counts.get, reverse=True):
        last = tool_last_used.get(tool)
        observed = tool_observed.get(tool, 0)
        tools[tool] = {
            "calls": tool_counts[tool],
            # Denominator for errors/rejections. `calls` is NOT that
            # denominator: most events come from a source that cannot see
            # failures, so errors/calls understates by whatever fraction of
            # calls was never error-observable.
            "error_observed_calls": observed,
            "errors": tool_errors.get(tool, 0) if observed else None,
            "rejections": tool_rejections.get(tool, 0) if observed else None,
            "last_used": last.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last else None,
        }

    # Build skill stats
    skills: dict[str, dict[str, int]] = {}
    for name in sorted(skill_counts, key=skill_counts.get, reverse=True):
        skills[name] = {"calls": skill_counts[name]}

    # Build MCP server stats (only include servers with actual usage)
    mcp_servers: dict[str, dict[str, Any]] = {}
    for name in sorted(mcp_server_stats, key=lambda n: mcp_server_stats[n]["calls"], reverse=True):
        last = mcp_last_used.get(name)
        mcp_servers[name] = dict(mcp_server_stats[name])
        mcp_servers[name]["last_used"] = last.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last else None

    # Most common model/client, or null
    model = model_counts.most_common(1)[0][0] if model_counts else None
    client = source_counts.most_common(1)[0][0] if source_counts else None

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_events": len(events),
        "tools": tools,
        "edit_without_read_count": edit_without_read_count,
        "model": model,
        "client": client,
        "skills": skills,
        "mcp_servers": mcp_servers,
        "installed_plugins": scan_installed_plugins(),
    }


def main() -> None:
    project = os.getcwd()
    events = load_events(project=project)
    stats = compute_tool_statistics(events)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write: parallel readers (maintain.py, evidence bridges) must
    # never see a torn stats.json — write a tempfile in the same dir, then
    # swap it into place with os.replace.
    payload = json.dumps(stats, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=DATA_DIR, prefix=".stats-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_name, STATS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    print(str(STATS_FILE))


if __name__ == "__main__":
    main()
