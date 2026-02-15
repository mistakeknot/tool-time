#!/usr/bin/env python3
"""tool-time deep analytics engine.

Reads all events from ~/.claude/tool-time/events.jsonl and produces
a rich analysis.json with session classification, tool chains, trends,
time patterns, and source comparison. Runs on-demand (not on every
SessionEnd like summarize.py).

Design doc: docs/plans/2026-02-14-deep-analytics-engine-design.md
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"
ANALYSIS_FILE = DATA_DIR / "analysis.json"

# Tool name normalization for cross-source comparison
TOOL_ALIASES: dict[str, str] = {
    # Codex CLI
    "shell": "Bash",
    "shell_command": "Bash",
    "exec_command": "Bash",
    "write_stdin": "Write",
    "update_plan": "TaskUpdate",
    # OpenClaw
    "exec": "Bash",
    "process": "Bash",
    "edit": "Edit",
    "write": "Write",
    "read": "Read",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
}


# --- Helpers ---

def is_call_event(event: dict) -> bool:
    """True for events that represent a tool invocation (not result)."""
    return event.get("event") in ("PreToolUse", "ToolUse")


def is_error_event(event: dict) -> bool:
    """True for events carrying error info (PostToolUse or ToolUse with error)."""
    return (
        event.get("event") in ("PostToolUse", "ToolUse")
        and event.get("error") is not None
    )


def normalize_tool_name(name: str) -> str:
    """Normalize tool name for cross-source comparison."""
    return TOOL_ALIASES.get(name, name)


_SESSION_ID_RE = re.compile(r"^(.+)-(\d+)$")


def extract_session_id(event_id: str) -> str:
    """Extract session UUID from event ID (format: uuid-seq).

    Handles UUIDs with internal hyphens (RFC 4122) and multi-digit
    sequence numbers by matching the last -digits suffix.
    """
    m = _SESSION_ID_RE.match(event_id)
    if m:
        return m.group(1)
    return event_id


def parse_timestamp(ts_raw: Any) -> datetime | None:
    """Parse timestamp from either ISO string or numeric ms."""
    try:
        if isinstance(ts_raw, (int, float)):
            return datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
        if isinstance(ts_raw, str) and ts_raw:
            return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        pass
    return None


# --- Loaders ---

def load_all_events(
    since: datetime | None = None,
    until: datetime | None = None,
    project: str | None = None,
    source: str | None = None,
) -> list[dict]:
    """Load events from events.jsonl with optional filters.

    All timestamps are normalized to timezone-aware UTC datetime objects
    stored as event["_ts"] for internal use.
    """
    if not EVENTS_FILE.exists():
        return []

    events: list[dict] = []
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Must have an id
        if not event.get("id"):
            continue

        # Parse timestamp
        ts = parse_timestamp(event.get("ts"))
        if ts is None:
            continue
        event["_ts"] = ts

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

    return events


def group_by_session(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by session ID, sort each group by timestamp."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        sid = extract_session_id(event["id"])
        sessions[sid].append(event)
    for sid in sessions:
        sessions[sid].sort(key=lambda e: e["_ts"])
    return dict(sessions)


# --- Session Classification ---

PLANNING_SKILLS = {"brainstorm", "writing-plans", "strategy", "write-plan"}


def classify_session(events: list[dict]) -> str:
    """Classify a session by tool usage pattern.

    Priority: planning > debugging > building > reviewing > exploring > other.
    Guard: returns "other" for sessions with no call events.
    """
    call_events = [e for e in events if is_call_event(e)]
    total = len(call_events)

    if total == 0:
        return "other"

    tool_counts: Counter[str] = Counter(e.get("tool", "") for e in call_events)
    error_count = sum(1 for e in events if is_error_event(e))

    # 1. Planning — but only if planning signals are >10% of total
    plan_mode = sum(
        1 for e in call_events
        if e.get("tool") in ("EnterPlanMode", "ExitPlanMode")
    )
    planning_skill = sum(
        1 for e in call_events
        if e.get("skill", "").split(":")[-1] in PLANNING_SKILLS
    )
    planning_signals = plan_mode + planning_skill
    if planning_signals > 0 and planning_signals / total > 0.10:
        return "planning"

    # 2. Debugging — require at least 3 errors AND >15% error rate
    if total > 0 and error_count >= 3 and (error_count / total) > 0.15:
        return "debugging"
    bash_count = tool_counts.get("Bash", 0) + tool_counts.get("shell", 0) + tool_counts.get("shell_command", 0) + tool_counts.get("exec_command", 0) + tool_counts.get("exec", 0)
    if total > 0 and bash_count / total > 0.4 and error_count > 3:
        return "debugging"

    # 3. Building
    edit_count = tool_counts.get("Edit", 0) + tool_counts.get("edit", 0)
    write_count = tool_counts.get("Write", 0) + tool_counts.get("write", 0)
    if (edit_count + write_count) / total > 0.25:
        return "building"

    # 4. Reviewing
    read_count = tool_counts.get("Read", 0) + tool_counts.get("read", 0)
    if read_count / total > 0.50 and (edit_count + write_count) == 0:
        return "reviewing"

    # 5. Exploring
    glob_count = tool_counts.get("Glob", 0)
    grep_count = tool_counts.get("Grep", 0)
    if (read_count + glob_count + grep_count) / total > 0.55 and edit_count / total < 0.10:
        return "exploring"

    return "other"


# --- Session Metrics ---

def compute_session_metrics(sessions: dict[str, list[dict]]) -> dict:
    """Compute aggregate session metrics: count, duration, tools/session, classification."""
    total_sessions = len(sessions)
    if total_sessions == 0:
        return {
            "total": 0,
            "avg_duration_minutes": 0,
            "avg_tools_per_session": 0,
            "median_tools_per_session": 0,
            "classifications": {},
        }

    durations: list[float] = []
    tools_per_session: list[int] = []
    classifications: Counter[str] = Counter()

    for sid, events in sessions.items():
        # Duration
        timestamps = [e["_ts"] for e in events if "_ts" in e]
        if len(timestamps) >= 2:
            dur = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
            durations.append(dur)

        # Tool calls per session (only call events, skip sessions with <2 calls)
        call_count = sum(1 for e in events if is_call_event(e))
        if call_count >= 2:
            tools_per_session.append(call_count)

        # Classification
        classifications[classify_session(events)] += 1

    return {
        "total": total_sessions,
        "avg_duration_minutes": round(statistics.mean(durations), 1) if durations else 0,
        "avg_tools_per_session": round(statistics.mean(tools_per_session), 1) if tools_per_session else 0,
        "median_tools_per_session": round(statistics.median(tools_per_session), 1) if tools_per_session else 0,
        "classifications": dict(classifications.most_common()),
    }


# --- Tool Chain Analysis ---

def compute_bigrams(sessions: dict[str, list[dict]], min_count: int | None = None) -> list[dict]:
    """Compute tool transition bigrams across all sessions.

    Only counts transitions between call events (PreToolUse/ToolUse),
    ignoring PostToolUse to avoid fake self-loops from Pre→Post pairs.
    """
    bigrams: Counter[tuple[str, str]] = Counter()

    for events in sessions.values():
        tools = [
            e.get("tool", "")
            for e in events
            if is_call_event(e) and e.get("tool")
        ]
        for i in range(len(tools) - 1):
            bigrams[(tools[i], tools[i + 1])] += 1

    total = sum(bigrams.values())
    if min_count is None:
        min_count = max(3, total // 200) if total > 0 else 1

    result = []
    for (from_tool, to_tool), count in bigrams.most_common(50):
        if count < min_count:
            break
        pct = round(count / total * 100, 1) if total > 0 else 0
        result.append({"from": from_tool, "to": to_tool, "count": count, "pct": pct})

    return result


def compute_trigrams(sessions: dict[str, list[dict]], min_count: int = 3) -> list[dict]:
    """Compute tool transition trigrams (sliding window of 3) over call events."""
    trigrams: Counter[tuple[str, str, str]] = Counter()

    for events in sessions.values():
        tools = [
            e.get("tool", "")
            for e in events
            if is_call_event(e) and e.get("tool")
        ]
        for i in range(len(tools) - 2):
            trigrams[(tools[i], tools[i + 1], tools[i + 2])] += 1

    result = []
    for seq, count in trigrams.most_common(30):
        if count < min_count:
            break
        result.append({"sequence": list(seq), "count": count})

    return result


FILE_TOOLS = {"Read", "Edit", "Write"}


def compute_retry_patterns(sessions: dict[str, list[dict]]) -> list[dict]:
    """Detect same-tool-same-file retry after error.

    Only for file-based tools (Read, Edit, Write) — other tools don't
    have a file field, causing false positives.
    """
    retry_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_retries": 0, "max_retries": 0, "sessions_affected": set()}
    )

    for session_id, events in sessions.items():
        session_retries: Counter[str] = Counter()

        for i in range(len(events) - 1):
            curr = events[i]
            nxt = events[i + 1]

            tool = curr.get("tool", "")
            if tool not in FILE_TOOLS:
                continue

            # Error on current (PostToolUse/ToolUse), followed by call to same tool+file
            if (
                is_error_event(curr)
                and is_call_event(nxt)
                and nxt.get("tool") == tool
                and curr.get("file") is not None
                and curr.get("file") == nxt.get("file")
            ):
                session_retries[tool] += 1

        for tool, count in session_retries.items():
            retry_stats[tool]["total_retries"] += count
            retry_stats[tool]["max_retries"] = max(retry_stats[tool]["max_retries"], count)
            retry_stats[tool]["sessions_affected"].add(session_id)

    result = []
    for tool in sorted(retry_stats, key=lambda t: retry_stats[t]["total_retries"], reverse=True):
        stats = retry_stats[tool]
        sessions_count = len(stats["sessions_affected"])
        if sessions_count > 0:
            result.append({
                "tool": tool,
                "avg_retries": round(stats["total_retries"] / sessions_count, 1),
                "max_retries": stats["max_retries"],
                "sessions_with_retries": sessions_count,
            })

    return result


# --- Trends ---

def compute_weekly_trends(events: list[dict], sessions: dict[str, list[dict]] | None = None) -> list[dict]:
    """Group events by ISO week, return time series of metrics per week.

    Uses (iso_year, iso_week) as key to handle year boundaries correctly.
    Tool names are normalized for cross-source consistency.
    """
    weekly: dict[tuple[int, int], dict] = defaultdict(
        lambda: {"events": 0, "errors": 0, "sessions": set(), "tools": Counter(), "call_events": 0}
    )

    for event in events:
        ts = event.get("_ts")
        if ts is None:
            continue
        iso = ts.isocalendar()
        key = (iso[0], iso[1])  # (iso_year, iso_week)

        weekly[key]["events"] += 1
        sid = extract_session_id(event["id"])
        weekly[key]["sessions"].add(sid)

        if is_call_event(event):
            weekly[key]["call_events"] += 1
            tool = normalize_tool_name(event.get("tool", ""))
            weekly[key]["tools"][tool] += 1

        if is_error_event(event):
            weekly[key]["errors"] += 1

    result = []
    for (year, week) in sorted(weekly):
        data = weekly[(year, week)]
        total = data["events"]
        error_rate = round(data["errors"] / data["call_events"], 3) if data["call_events"] > 0 else 0.0
        result.append({
            "week": f"{year}-W{week:02d}",
            "iso_year": year,
            "iso_week": week,
            "events": total,
            "sessions": len(data["sessions"]),
            "error_rate": error_rate,
            "tools": dict(data["tools"].most_common(10)),
        })

    return result


# --- Time Patterns ---

def _get_local_tz():
    """Get system local timezone, fallback to UTC."""
    try:
        import zoneinfo
        import time
        tz_name = time.tzname[0]
        # Try to get a proper zoneinfo timezone
        if hasattr(time, "tzset"):
            import os
            tz_env = os.environ.get("TZ", "")
            if tz_env:
                return zoneinfo.ZoneInfo(tz_env)
        # Fallback: use UTC offset
        local_offset = datetime.now(timezone.utc).astimezone().utcoffset()
        if local_offset is not None:
            return timezone(local_offset)
    except Exception:
        pass
    return timezone.utc


def compute_time_patterns(events: list[dict], tz: Any = None) -> dict:
    """Compute hour-of-day and day-of-week patterns.

    Uses local timezone by default so peak hours are meaningful.
    """
    if tz is None:
        tz = _get_local_tz()

    by_hour: dict[int, dict] = defaultdict(lambda: {"events": 0, "errors": 0, "call_events": 0})
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_day: dict[str, dict] = defaultdict(lambda: {"events": 0, "errors": 0, "sessions": set()})

    for event in events:
        ts = event.get("_ts")
        if ts is None:
            continue
        local_ts = ts.astimezone(tz)
        hour = local_ts.hour
        day = day_names[local_ts.weekday()]

        by_hour[hour]["events"] += 1
        by_day[day]["events"] += 1

        sid = extract_session_id(event["id"])
        by_day[day]["sessions"].add(sid)

        if is_call_event(event):
            by_hour[hour]["call_events"] += 1

        if is_error_event(event):
            by_hour[hour]["errors"] += 1
            by_day[day]["errors"] += 1

    # Build hour list (0-23)
    hours = []
    for h in range(24):
        data = by_hour[h]
        er = round(data["errors"] / data["call_events"], 3) if data["call_events"] > 0 else 0.0
        hours.append({"hour": h, "events": data["events"], "error_rate": er})

    # Build day list
    days = []
    for d in day_names:
        data = by_day[d]
        total = data["events"]
        er = round(data["errors"] / total, 3) if total > 0 else 0.0
        days.append({"day": d, "events": total, "sessions": len(data["sessions"]), "error_rate": er})

    # Peaks
    peak_hour = max(range(24), key=lambda h: by_hour[h]["events"]) if any(by_hour[h]["events"] > 0 for h in range(24)) else 0
    peak_day = max(day_names, key=lambda d: by_day[d]["events"]) if any(by_day[d]["events"] > 0 for d in day_names) else "Monday"
    error_prone_hour = max(
        range(24),
        key=lambda h: by_hour[h]["errors"] / by_hour[h]["call_events"] if by_hour[h]["call_events"] > 0 else 0,
    ) if any(by_hour[h]["call_events"] > 0 for h in range(24)) else 0

    tz_name = str(tz)
    if hasattr(tz, "key"):
        tz_name = tz.key

    return {
        "by_hour": hours,
        "by_day_of_week": days,
        "peak_hour": peak_hour,
        "peak_day": peak_day,
        "most_error_prone_hour": error_prone_hour,
        "timezone": tz_name,
    }


# --- Source Comparison ---

def compute_source_comparison(events: list[dict], sessions: dict[str, list[dict]]) -> dict:
    """Per-source metrics with normalized tool names."""
    # Map session → source (use most common source in session)
    session_source: dict[str, str] = {}
    source_events: dict[str, list[dict]] = defaultdict(list)

    for event in events:
        src = event.get("source") or "unknown"
        source_events[src].append(event)
        sid = extract_session_id(event["id"])
        if sid not in session_source:
            session_source[sid] = src

    result = {}
    for src, src_events in sorted(source_events.items()):
        call_count = sum(1 for e in src_events if is_call_event(e))
        error_count = sum(1 for e in src_events if is_error_event(e))

        # Sessions for this source
        src_sessions = {
            sid for sid, s_src in session_source.items()
            if s_src == src and sid in sessions
        }

        # Tools (normalized)
        tool_counts: Counter[str] = Counter()
        for e in src_events:
            if is_call_event(e):
                tool_counts[normalize_tool_name(e.get("tool", ""))] += 1

        # Avg tools per session
        tools_per = []
        for sid in src_sessions:
            if sid in sessions:
                tc = sum(1 for e in sessions[sid] if is_call_event(e))
                if tc >= 2:
                    tools_per.append(tc)

        # Classification mix
        class_mix: Counter[str] = Counter()
        for sid in src_sessions:
            if sid in sessions:
                class_mix[classify_session(sessions[sid])] += 1

        result[src] = {
            "events": len(src_events),
            "sessions": len(src_sessions),
            "avg_tools_per_session": round(statistics.mean(tools_per), 1) if tools_per else 0,
            "error_rate": round(error_count / call_count, 3) if call_count > 0 else 0.0,
            "top_tools": [t for t, _ in tool_counts.most_common(5)],
            "classification_mix": dict(class_mix.most_common()),
        }

    return result


# --- Project Breakdown ---

def compute_project_breakdown(events: list[dict], sessions: dict[str, list[dict]]) -> dict:
    """Per-project metrics with raw tool names."""
    project_events: dict[str, list[dict]] = defaultdict(list)
    project_sessions: dict[str, set[str]] = defaultdict(set)

    for event in events:
        proj = event.get("project", "unknown")
        project_events[proj].append(event)
        sid = extract_session_id(event["id"])
        project_sessions[proj].add(sid)

    result = {}
    for proj in sorted(project_events, key=lambda p: len(project_events[p]), reverse=True):
        proj_evts = project_events[proj]
        call_count = sum(1 for e in proj_evts if is_call_event(e))
        error_count = sum(1 for e in proj_evts if is_error_event(e))

        tool_counts: Counter[str] = Counter()
        for e in proj_evts:
            if is_call_event(e):
                tool_counts[e.get("tool", "")] += 1

        # Primary classification from sessions
        class_counts: Counter[str] = Counter()
        for sid in project_sessions[proj]:
            if sid in sessions:
                class_counts[classify_session(sessions[sid])] += 1
        primary_class = class_counts.most_common(1)[0][0] if class_counts else "other"

        # Use short project name (last path component)
        short_name = proj.rstrip("/").rsplit("/", 1)[-1] if "/" in proj else proj

        result[short_name] = {
            "path": proj,
            "events": len(proj_evts),
            "sessions": len(project_sessions[proj]),
            "top_tools": [t for t, _ in tool_counts.most_common(5)],
            "primary_classification": primary_class,
            "error_rate": round(error_count / call_count, 3) if call_count > 0 else 0.0,
        }

    return result


# --- Main ---

def run_analysis(
    since: datetime | None = None,
    until: datetime | None = None,
    project: str | None = None,
    source: str | None = None,
    tz: Any = None,
) -> dict:
    """Run full analysis and return the result dict."""
    events = load_all_events(since=since, until=until, project=project, source=source)
    if not events:
        return {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period": {"start": None, "end": None},
            "filters": {"project": project, "source": source},
            "event_count": 0,
            "sessions": {"total": 0, "avg_duration_minutes": 0, "avg_tools_per_session": 0, "median_tools_per_session": 0, "classifications": {}},
            "tool_chains": {"bigrams": [], "trigrams": [], "retry_patterns": []},
            "trends": [],
            "time_patterns": {"by_hour": [], "by_day_of_week": [], "peak_hour": 0, "peak_day": "Monday", "most_error_prone_hour": 0, "timezone": "UTC"},
            "by_source": {},
            "projects": {},
        }

    sessions = group_by_session(events)

    # Date range
    timestamps = [e["_ts"] for e in events]
    period_start = min(timestamps).strftime("%Y-%m-%d")
    period_end = max(timestamps).strftime("%Y-%m-%d")

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": {"start": period_start, "end": period_end},
        "filters": {"project": project, "source": source},
        "event_count": len(events),
        "sessions": compute_session_metrics(sessions),
        "tool_chains": {
            "bigrams": compute_bigrams(sessions),
            "trigrams": compute_trigrams(sessions),
            "retry_patterns": compute_retry_patterns(sessions),
        },
        "trends": compute_weekly_trends(events, sessions),
        "time_patterns": compute_time_patterns(events, tz=tz),
        "by_source": compute_source_comparison(events, sessions),
        "projects": compute_project_breakdown(events, sessions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep analytics for tool-time events")
    parser.add_argument("--project", help="Filter to specific project path")
    parser.add_argument("--source", help="Filter to specific source (claude-code, codex, openclaw)")
    parser.add_argument("--since", help="Start date (YYYY-MM-DD), default: 90 days ago")
    parser.add_argument("--until", help="End date (YYYY-MM-DD), default: now")
    parser.add_argument("--timezone", help="Timezone for time patterns (e.g., America/Los_Angeles)")
    args = parser.parse_args()

    # Parse dates
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=90)

    until = None
    if args.until:
        until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)

    # Parse timezone
    tz = None
    if args.timezone:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(args.timezone)
        except Exception:
            print(f"Warning: Unknown timezone '{args.timezone}', using system default", file=sys.stderr)

    result = run_analysis(since=since, until=until, project=args.project, source=args.source, tz=tz)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_FILE.write_text(json.dumps(result, indent=2) + "\n")
    print(str(ANALYSIS_FILE))


if __name__ == "__main__":
    main()
