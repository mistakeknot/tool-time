"""Tests for analyze.py — deep analytics engine."""

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from analyze import (
    TOOL_ALIASES,
    classify_session,
    compute_bigrams,
    compute_project_breakdown,
    compute_retry_patterns,
    compute_session_metrics,
    compute_source_comparison,
    compute_time_patterns,
    compute_trigrams,
    compute_weekly_trends,
    extract_session_id,
    group_by_session,
    is_call_event,
    is_error_event,
    load_all_events,
    normalize_tool_name,
    parse_timestamp,
    run_analysis,
)


# --- Test helpers ---

def _ts(day_offset: int = 0, hour: int = 12) -> datetime:
    """Create a UTC datetime relative to 2026-01-15."""
    return datetime(2026, 1, 15 + day_offset, hour, 0, 0, tzinfo=timezone.utc)


def _ts_str(day_offset: int = 0, hour: int = 12) -> str:
    return _ts(day_offset, hour).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_event(
    tool: str,
    event_type: str = "PreToolUse",
    error: str | None = None,
    session: str = "abc-def-ghi",
    seq: int = 1,
    day_offset: int = 0,
    hour: int = 12,
    source: str | None = "claude-code",
    project: str = "/root/projects/test",
    skill: str | None = None,
    file: str | None = None,
) -> dict:
    """Create a test event."""
    ev = {
        "v": 1,
        "id": f"{session}-{seq}",
        "ts": _ts_str(day_offset, hour),
        "event": event_type,
        "tool": tool,
        "project": project,
        "error": error,
        "_ts": _ts(day_offset, hour),
    }
    if source:
        ev["source"] = source
    if skill:
        ev["skill"] = skill
    if file:
        ev["file"] = file
    return ev


# --- Helpers ---

class TestIsCallEvent:
    def test_pre_tool_use(self):
        assert is_call_event({"event": "PreToolUse"}) is True

    def test_tool_use(self):
        assert is_call_event({"event": "ToolUse"}) is True

    def test_post_tool_use(self):
        assert is_call_event({"event": "PostToolUse"}) is False

    def test_session_end(self):
        assert is_call_event({"event": "SessionEnd"}) is False


class TestIsErrorEvent:
    def test_post_tool_use_with_error(self):
        assert is_error_event({"event": "PostToolUse", "error": "fail"}) is True

    def test_tool_use_with_error(self):
        assert is_error_event({"event": "ToolUse", "error": "fail"}) is True

    def test_post_tool_use_no_error(self):
        assert is_error_event({"event": "PostToolUse", "error": None}) is False

    def test_pre_tool_use_with_error(self):
        # PreToolUse never has errors — should return False
        assert is_error_event({"event": "PreToolUse", "error": "fail"}) is False

    def test_missing_error_field(self):
        assert is_error_event({"event": "PostToolUse"}) is False


class TestNormalizeToolName:
    def test_codex_aliases(self):
        assert normalize_tool_name("shell") == "Bash"
        assert normalize_tool_name("shell_command") == "Bash"
        assert normalize_tool_name("exec_command") == "Bash"
        assert normalize_tool_name("write_stdin") == "Write"
        assert normalize_tool_name("update_plan") == "TaskUpdate"

    def test_openclaw_aliases(self):
        assert normalize_tool_name("exec") == "Bash"
        assert normalize_tool_name("process") == "Bash"
        assert normalize_tool_name("edit") == "Edit"
        assert normalize_tool_name("write") == "Write"
        assert normalize_tool_name("read") == "Read"
        assert normalize_tool_name("web_fetch") == "WebFetch"
        assert normalize_tool_name("web_search") == "WebSearch"

    def test_passthrough(self):
        assert normalize_tool_name("Bash") == "Bash"
        assert normalize_tool_name("Read") == "Read"
        assert normalize_tool_name("SomeNewTool") == "SomeNewTool"


class TestExtractSessionId:
    def test_standard_uuid(self):
        assert extract_session_id("03b50e1b-96d2-4216-8dd8-ea8040937592-1") == "03b50e1b-96d2-4216-8dd8-ea8040937592"

    def test_multi_digit_seq(self):
        assert extract_session_id("abc-def-ghi-123") == "abc-def-ghi"

    def test_single_char_no_hyphen(self):
        assert extract_session_id("nodashes") == "nodashes"

    def test_simple_uuid_seq(self):
        assert extract_session_id("abc-1") == "abc"

    def test_long_seq(self):
        assert extract_session_id("uuid-here-9999") == "uuid-here"


class TestParseTimestamp:
    def test_iso_string(self):
        ts = parse_timestamp("2026-01-15T12:00:00Z")
        assert ts == datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_numeric_ms(self):
        # 2026-01-15T12:00:00Z in milliseconds
        ms = int(datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ts = parse_timestamp(ms)
        assert ts.year == 2026 and ts.month == 1 and ts.day == 15

    def test_invalid(self):
        assert parse_timestamp("not-a-date") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None


# --- Loaders ---

class TestLoadAllEvents:
    def test_load_with_filters(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events = [
            {"v": 1, "id": "s1-1", "ts": "2026-01-10T12:00:00Z", "event": "PreToolUse", "tool": "Read", "project": "/p1"},
            {"v": 1, "id": "s1-2", "ts": "2026-01-20T12:00:00Z", "event": "PreToolUse", "tool": "Edit", "project": "/p1"},
            {"v": 1, "id": "s2-1", "ts": "2026-01-20T12:00:00Z", "event": "PreToolUse", "tool": "Bash", "project": "/p2"},
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events))

        with patch("analyze.EVENTS_FILE", events_file):
            # All events
            result = load_all_events()
            assert len(result) == 3

            # Filter by project
            result = load_all_events(project="/p1")
            assert len(result) == 2

            # Filter by date
            since = datetime(2026, 1, 15, tzinfo=timezone.utc)
            result = load_all_events(since=since)
            assert len(result) == 2

    def test_empty_file(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        with patch("analyze.EVENTS_FILE", events_file):
            assert load_all_events() == []

    def test_missing_file(self, tmp_path):
        events_file = tmp_path / "nonexistent.jsonl"
        with patch("analyze.EVENTS_FILE", events_file):
            assert load_all_events() == []

    def test_malformed_lines_skipped(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"v": 1, "id": "s1-1", "ts": "2026-01-10T12:00:00Z", "event": "PreToolUse", "tool": "Read", "project": "/p"}),
            "not json",
            json.dumps({"v": 1, "ts": "2026-01-10T12:00:00Z"}),  # missing id
        ]
        events_file.write_text("\n".join(lines))
        with patch("analyze.EVENTS_FILE", events_file):
            result = load_all_events()
            assert len(result) == 1


class TestGroupBySession:
    def test_groups_and_sorts(self):
        events = [
            _make_event("Edit", seq=2, hour=14),
            _make_event("Read", seq=1, hour=12),
            _make_event("Bash", session="other-sess", seq=1),
        ]
        sessions = group_by_session(events)
        assert len(sessions) == 2
        assert sessions["abc-def-ghi"][0]["tool"] == "Read"  # sorted by ts
        assert sessions["abc-def-ghi"][1]["tool"] == "Edit"


# --- Session Classification ---

class TestClassifySession:
    def test_empty_session(self):
        assert classify_session([]) == "other"

    def test_no_call_events(self):
        events = [
            _make_event("Read", event_type="PostToolUse"),
            _make_event("Edit", event_type="PostToolUse"),
        ]
        assert classify_session(events) == "other"

    def test_planning(self):
        events = [
            _make_event("EnterPlanMode", seq=1),
            _make_event("Read", seq=2),
            _make_event("ExitPlanMode", seq=3),
        ]
        assert classify_session(events) == "planning"

    def test_planning_requires_10_percent(self):
        """A single planning skill in a 20-event building session → building, not planning."""
        events = [_make_event("Edit", seq=i) for i in range(1, 20)]
        events.append(_make_event("Skill", seq=20, skill="brainstorm"))
        assert classify_session(events) != "planning"

    def test_debugging(self):
        """High error rate → debugging."""
        events = []
        for i in range(1, 11):
            events.append(_make_event("Bash", seq=i))
        # Add errors on PostToolUse (where errors actually appear for hook events)
        for i in range(1, 6):
            events.append(_make_event("Bash", event_type="PostToolUse", error="fail", seq=10 + i))
        assert classify_session(events) == "debugging"

    def test_building(self):
        events = [
            _make_event("Read", seq=1),
            _make_event("Edit", seq=2),
            _make_event("Edit", seq=3),
            _make_event("Write", seq=4),
        ]
        assert classify_session(events) == "building"

    def test_reviewing(self):
        events = [
            _make_event("Read", seq=i) for i in range(1, 11)
        ]
        assert classify_session(events) == "reviewing"

    def test_exploring(self):
        # Need edit_count > 0 to disqualify "reviewing" (which requires edit+write==0)
        # But edit/total must be < 0.10 for "exploring"
        events = [
            _make_event("Read", seq=1),
            _make_event("Glob", seq=2),
            _make_event("Grep", seq=3),
            _make_event("Read", seq=4),
            _make_event("Read", seq=5),
            _make_event("Read", seq=6),
            _make_event("Read", seq=7),
            _make_event("Read", seq=8),
            _make_event("Read", seq=9),
            _make_event("Read", seq=10),
            _make_event("Read", seq=11),
            _make_event("Edit", seq=12),  # 1/12 = 8.3% < 10%, disqualifies reviewing
        ]
        assert classify_session(events) == "exploring"

    def test_priority_debugging_over_building(self):
        """Debugging wins over building when both thresholds are met."""
        events = []
        # 10 Edit calls (building threshold met)
        for i in range(1, 11):
            events.append(_make_event("Edit", seq=i))
        # But also high errors (debugging threshold met)
        for i in range(11, 16):
            events.append(_make_event("Edit", event_type="PostToolUse", error="fail", seq=i))
        result = classify_session(events)
        assert result == "debugging"

    def test_division_by_zero_guard(self):
        """Session with only PostToolUse events → 'other' (no call events)."""
        events = [
            _make_event("Read", event_type="PostToolUse", seq=1),
        ]
        assert classify_session(events) == "other"


# --- Session Metrics ---

class TestComputeSessionMetrics:
    def test_empty(self):
        result = compute_session_metrics({})
        assert result["total"] == 0

    def test_single_session(self):
        events = [
            _make_event("Read", seq=1, hour=10),
            _make_event("Edit", seq=2, hour=11),
            _make_event("Bash", seq=3, hour=12),
        ]
        result = compute_session_metrics({"s1": events})
        assert result["total"] == 1
        assert result["avg_tools_per_session"] == 3
        assert result["avg_duration_minutes"] == 120  # 10am to 12pm

    def test_tiny_session_excluded_from_tools_per(self):
        """Session with <2 call events excluded from avg_tools_per_session."""
        events = [_make_event("Read", seq=1)]
        result = compute_session_metrics({"s1": events})
        assert result["avg_tools_per_session"] == 0  # excluded


# --- Tool Chain Analysis ---

class TestComputeBigrams:
    def test_basic(self):
        events = [
            _make_event("Read", seq=1),
            _make_event("Edit", seq=2),
            _make_event("Bash", seq=3),
        ]
        result = compute_bigrams({"s1": events}, min_count=1)
        assert any(b["from"] == "Read" and b["to"] == "Edit" for b in result)
        assert any(b["from"] == "Edit" and b["to"] == "Bash" for b in result)

    def test_no_self_loops_from_pre_post(self):
        """Pre+Post pairs should NOT create self-loops — only PreToolUse counted."""
        events = [
            _make_event("Read", event_type="PreToolUse", seq=1),
            _make_event("Read", event_type="PostToolUse", seq=2),
            _make_event("Edit", event_type="PreToolUse", seq=3),
            _make_event("Edit", event_type="PostToolUse", seq=4),
        ]
        result = compute_bigrams({"s1": events}, min_count=1)
        # Only transition should be Read → Edit (from PreToolUse events)
        assert len(result) == 1
        assert result[0]["from"] == "Read" and result[0]["to"] == "Edit"


class TestComputeTrigrams:
    def test_basic(self):
        events = [
            _make_event("Glob", seq=1),
            _make_event("Read", seq=2),
            _make_event("Edit", seq=3),
        ]
        result = compute_trigrams({"s1": events}, min_count=1)
        assert len(result) == 1
        assert result[0]["sequence"] == ["Glob", "Read", "Edit"]


class TestComputeRetryPatterns:
    def test_file_based_retry_detected(self):
        events = [
            _make_event("Edit", event_type="PostToolUse", error="not unique", seq=1, file="/a.py"),
            _make_event("Edit", event_type="PreToolUse", seq=2, file="/a.py"),
        ]
        result = compute_retry_patterns({"s1": events})
        assert len(result) == 1
        assert result[0]["tool"] == "Edit"

    def test_non_file_tools_excluded(self):
        """Bash→Bash after error should NOT count as retry (no file field)."""
        events = [
            _make_event("Bash", event_type="PostToolUse", error="fail", seq=1),
            _make_event("Bash", event_type="PreToolUse", seq=2),
        ]
        result = compute_retry_patterns({"s1": events})
        assert len(result) == 0

    def test_different_file_not_retry(self):
        events = [
            _make_event("Edit", event_type="PostToolUse", error="fail", seq=1, file="/a.py"),
            _make_event("Edit", event_type="PreToolUse", seq=2, file="/b.py"),
        ]
        result = compute_retry_patterns({"s1": events})
        assert len(result) == 0


# --- Trends ---

class TestComputeWeeklyTrends:
    def test_single_week(self):
        events = [
            _make_event("Read", seq=1, day_offset=0),
            _make_event("Edit", seq=2, day_offset=1),
        ]
        result = compute_weekly_trends(events)
        assert len(result) == 1
        assert result[0]["events"] == 2

    def test_multi_week(self):
        events = [
            _make_event("Read", seq=1, day_offset=0),
            _make_event("Edit", seq=2, day_offset=8),  # different week
        ]
        result = compute_weekly_trends(events)
        assert len(result) == 2

    def test_year_boundary(self):
        """Dec 29 2025 is ISO week 1 of 2026 (year boundary crossing)."""
        ev1 = _make_event("Read", seq=1)
        ev1["_ts"] = datetime(2025, 12, 29, 12, 0, 0, tzinfo=timezone.utc)
        ev1["ts"] = "2025-12-29T12:00:00Z"

        ev2 = _make_event("Edit", seq=2)
        ev2["_ts"] = datetime(2025, 12, 20, 12, 0, 0, tzinfo=timezone.utc)
        ev2["ts"] = "2025-12-20T12:00:00Z"

        result = compute_weekly_trends([ev1, ev2])
        assert len(result) == 2
        # Dec 29 2025 should be in 2026-W01 (iso_year=2026)
        years = {r["iso_year"] for r in result}
        assert 2026 in years and 2025 in years

    def test_tool_names_normalized(self):
        events = [
            _make_event("shell", source="codex", seq=1),
            _make_event("Bash", source="claude-code", seq=2),
        ]
        result = compute_weekly_trends(events)
        # Both should count as "Bash" in tools
        assert result[0]["tools"].get("Bash", 0) == 2


# --- Time Patterns ---

class TestComputeTimePatterns:
    def test_hour_bucketing(self):
        events = [
            _make_event("Read", seq=1, hour=14),
            _make_event("Edit", seq=2, hour=14),
            _make_event("Bash", seq=3, hour=3),
        ]
        result = compute_time_patterns(events, tz=timezone.utc)
        assert result["peak_hour"] == 14
        assert result["by_hour"][14]["events"] == 2
        assert result["by_hour"][3]["events"] == 1

    def test_day_bucketing(self):
        # 2026-01-15 is a Thursday
        events = [_make_event("Read", seq=1, day_offset=0)]
        result = compute_time_patterns(events, tz=timezone.utc)
        thursday = next(d for d in result["by_day_of_week"] if d["day"] == "Thursday")
        assert thursday["events"] == 1


# --- Source Comparison ---

class TestComputeSourceComparison:
    def test_multi_source(self):
        events = [
            _make_event("Read", source="claude-code", seq=1, session="s1"),
            _make_event("Edit", source="claude-code", seq=2, session="s1"),
            _make_event("shell", source="codex", seq=1, session="s2"),
            _make_event("shell_command", source="codex", seq=2, session="s2"),
        ]
        sessions = group_by_session(events)
        result = compute_source_comparison(events, sessions)
        assert "claude-code" in result
        assert "codex" in result
        assert result["codex"]["events"] == 2

    def test_missing_source_as_unknown(self):
        events = [
            _make_event("Read", source=None, seq=1, session="s1"),
        ]
        sessions = group_by_session(events)
        result = compute_source_comparison(events, sessions)
        assert "unknown" in result


# --- Project Breakdown ---

class TestComputeProjectBreakdown:
    def test_multi_project(self):
        events = [
            _make_event("Read", project="/root/projects/foo", seq=1, session="s1"),
            _make_event("Bash", project="/root/projects/bar", seq=1, session="s2"),
        ]
        sessions = group_by_session(events)
        result = compute_project_breakdown(events, sessions)
        assert "foo" in result
        assert "bar" in result


# --- Integration ---

class TestRunAnalysis:
    def test_empty_events(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        with patch("analyze.EVENTS_FILE", events_file):
            result = run_analysis()
            assert result["event_count"] == 0
            assert result["sessions"]["total"] == 0

    def test_full_pipeline(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events = []
        # Create a realistic mini-dataset: 2 sessions, mixed tools
        for i in range(1, 6):
            events.append({
                "v": 1, "id": f"sess1-{i}",
                "ts": f"2026-01-15T{10+i}:00:00Z",
                "event": "PreToolUse", "tool": "Read",
                "project": "/test", "error": None, "source": "claude-code",
            })
        for i in range(1, 4):
            events.append({
                "v": 1, "id": f"sess1-{5+i}",
                "ts": f"2026-01-15T{15+i}:00:00Z",
                "event": "PreToolUse", "tool": "Edit",
                "project": "/test", "error": None, "source": "claude-code",
            })
        for i in range(1, 3):
            events.append({
                "v": 1, "id": f"sess2-{i}",
                "ts": f"2026-01-16T{10+i}:00:00Z",
                "event": "PreToolUse", "tool": "shell",
                "project": "/test", "error": None, "source": "codex",
            })
        events_file.write_text("\n".join(json.dumps(e) for e in events))

        with patch("analyze.EVENTS_FILE", events_file):
            result = run_analysis(
                since=datetime(2026, 1, 1, tzinfo=timezone.utc),
                tz=timezone.utc,
            )
            assert result["event_count"] == 10
            assert result["sessions"]["total"] == 2
            assert len(result["tool_chains"]["bigrams"]) > 0
            assert len(result["trends"]) >= 1
            assert "claude-code" in result["by_source"]
            assert "codex" in result["by_source"]

    def test_schema_keys(self, tmp_path):
        """Verify all expected top-level keys exist."""
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(json.dumps({
            "v": 1, "id": "s-1", "ts": "2026-01-15T12:00:00Z",
            "event": "PreToolUse", "tool": "Read", "project": "/test",
            "error": None, "source": "claude-code",
        }))
        with patch("analyze.EVENTS_FILE", events_file):
            result = run_analysis(
                since=datetime(2026, 1, 1, tzinfo=timezone.utc),
                tz=timezone.utc,
            )
            expected_keys = {
                "generated", "period", "filters", "event_count",
                "sessions", "tool_chains", "trends", "time_patterns",
                "by_source", "projects",
            }
            assert set(result.keys()) == expected_keys
