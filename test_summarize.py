#!/usr/bin/env python3
"""Tests for summarize.py."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from summarize import (
    compute_tool_statistics,
    is_user_rejection,
    load_events,
    scan_installed_plugins,
)


def _make_event(
    tool: str,
    event_type: str = "ToolUse",
    project: str = "/test/project",
    error: str | None = None,
    file: str | None = None,
    skill: str | None = None,
    session_id: str = "sess1",
    seq: int = 1,
    ts: datetime | None = None,
) -> dict:
    ts = ts or datetime.now(timezone.utc)
    ev = {
        "v": 1,
        "id": f"{session_id}-{seq}",
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event_type,
        "tool": tool,
        "project": project,
        "error": error,
        "source": "claude-code",
    }
    if file:
        ev["file"] = file
    if skill:
        ev["skill"] = skill
    return ev


def _write_events(events: list[dict], path: Path) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


class TestIsUserRejection:
    def test_none(self):
        assert not is_user_rejection(None)

    def test_real_error(self):
        assert not is_user_rejection("old_string not found")

    def test_user_denied(self):
        assert is_user_rejection("User denied the operation")

    def test_permission(self):
        assert is_user_rejection("Permission to use Bash was denied")

    def test_doesnt_want(self):
        assert is_user_rejection("The user doesn't want to proceed with this")


class TestLoadEvents:
    def test_empty_file(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        with mock.patch("summarize.EVENTS_FILE", events_file):
            assert load_events() == []

    def test_no_file(self, tmp_path):
        with mock.patch("summarize.EVENTS_FILE", tmp_path / "nonexistent.jsonl"):
            assert load_events() == []

    def test_filters_by_time(self, tmp_path):
        recent = _make_event("Read", ts=datetime.now(timezone.utc))
        old = _make_event("Read", ts=datetime.now(timezone.utc) - timedelta(days=30))
        events_file = tmp_path / "events.jsonl"
        _write_events([recent, old], events_file)
        with mock.patch("summarize.EVENTS_FILE", events_file):
            result = load_events(days=7)
        assert len(result) == 1

    def test_filters_by_project(self, tmp_path):
        ev1 = _make_event("Read", project="/project/a")
        ev2 = _make_event("Read", project="/project/b")
        events_file = tmp_path / "events.jsonl"
        _write_events([ev1, ev2], events_file)
        with mock.patch("summarize.EVENTS_FILE", events_file):
            result = load_events(project="/project/a")
        assert len(result) == 1
        assert result[0]["project"] == "/project/a"

    def test_skips_malformed_lines(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        good = _make_event("Read")
        events_file.write_text(
            json.dumps(good) + "\n"
            "not valid json\n"
            '{"missing": "ts field"}\n'
        )
        with mock.patch("summarize.EVENTS_FILE", events_file):
            result = load_events()
        assert len(result) == 1


class TestComputeToolStatistics:
    def test_empty_events(self):
        stats = compute_tool_statistics([])
        assert stats["total_events"] == 0
        assert stats["tools"] == {}
        assert stats["edit_without_read_count"] == 0

    def test_counts_tools(self):
        events = [
            _make_event("Read", seq=1),
            _make_event("Read", seq=2),
            _make_event("Bash", seq=3),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Read"]["calls"] == 2
        assert stats["tools"]["Bash"]["calls"] == 1

    def test_counts_errors(self):
        events = [
            _make_event("Edit", event_type="ToolUse", seq=1),
            _make_event("Edit", event_type="ToolUse", error="old_string not found", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Edit"]["calls"] == 2
        assert stats["tools"]["Edit"]["errors"] == 1

    def test_separates_rejections(self):
        events = [
            _make_event("Bash", event_type="ToolUse", seq=1),
            _make_event("Bash", event_type="ToolUse", error="User denied the operation", seq=2),
            _make_event("Bash", event_type="ToolUse", error="command failed", seq=3),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Bash"]["calls"] == 3
        assert stats["tools"]["Bash"]["rejections"] == 1
        assert stats["tools"]["Bash"]["errors"] == 1

    def test_edit_without_read_session_scoped(self):
        # Session 1: Read foo, Edit foo (OK)
        # Session 2: Edit foo without reading (flagged)
        events = [
            _make_event("Read", file="/foo.py", session_id="s1", seq=1),
            _make_event("Edit", file="/foo.py", session_id="s1", seq=2),
            _make_event("Edit", file="/foo.py", session_id="s2", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["edit_without_read_count"] == 1

    def test_write_counts_as_known(self):
        # Write creates the file, so editing after Write is OK
        events = [
            _make_event("Write", file="/new.py", session_id="s1", seq=1),
            _make_event("Edit", file="/new.py", session_id="s1", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["edit_without_read_count"] == 0

    def test_has_generated_timestamp(self):
        stats = compute_tool_statistics([])
        assert "generated" in stats
        # Should be parseable ISO format
        datetime.fromisoformat(stats["generated"].replace("Z", "+00:00"))

    def test_post_tool_use_errors_counted(self):
        """PostToolUse events should count errors even though they don't count calls."""
        events = [
            _make_event("Edit", event_type="PreToolUse", seq=1),
            _make_event("Edit", event_type="PostToolUse", error="file not found", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Edit"]["calls"] == 1
        assert stats["tools"]["Edit"]["errors"] == 1

    def test_empty_stats_have_new_keys(self):
        stats = compute_tool_statistics([])
        assert stats["skills"] == {}
        assert stats["mcp_servers"] == {}
        assert isinstance(stats["installed_plugins"], list)


class TestSkillAggregation:
    def test_counts_skills(self):
        events = [
            _make_event("Task", skill="superpowers:brainstorming", seq=1),
            _make_event("Task", skill="superpowers:brainstorming", seq=2),
            _make_event("Read", skill="tool-time:tool-time", seq=3),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"]["superpowers:brainstorming"]["calls"] == 2
        assert stats["skills"]["tool-time:tool-time"]["calls"] == 1

    def test_ignores_events_without_skill(self):
        events = [
            _make_event("Read", seq=1),
            _make_event("Edit", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"] == {}

    def test_skills_sorted_by_calls(self):
        events = [
            _make_event("Task", skill="alpha", seq=1),
            _make_event("Task", skill="beta", seq=2),
            _make_event("Task", skill="beta", seq=3),
        ]
        stats = compute_tool_statistics(events)
        names = list(stats["skills"].keys())
        assert names == ["beta", "alpha"]

    def test_skill_only_counted_on_call_events(self):
        """PostToolUse events should not count skill invocations."""
        events = [
            _make_event("Task", event_type="PreToolUse", skill="foo", seq=1),
            _make_event("Task", event_type="PostToolUse", skill="foo", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"]["foo"]["calls"] == 1


class TestMcpServerAggregation:
    def test_parses_mcp_server(self):
        events = [
            _make_event("mcp__chrome-devtools__new_page", seq=1),
            _make_event("mcp__chrome-devtools__click", seq=2),
            _make_event("mcp__slack__send_message", seq=3),
        ]
        stats = compute_tool_statistics(events)
        assert stats["mcp_servers"]["chrome-devtools"]["calls"] == 2
        assert stats["mcp_servers"]["slack"]["calls"] == 1

    def test_mcp_server_errors(self):
        events = [
            _make_event("mcp__slack__send", event_type="PreToolUse", seq=1),
            _make_event("mcp__slack__send", event_type="PostToolUse", error="timeout", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["mcp_servers"]["slack"]["calls"] == 1
        assert stats["mcp_servers"]["slack"]["errors"] == 1

    def test_empty_server_name_ignored(self):
        """mcp____tool should be ignored (empty server name)."""
        events = [
            _make_event("mcp____some_tool", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["mcp_servers"] == {}

    def test_only_two_parts_ignored(self):
        """mcp__server with no tool part should be ignored."""
        events = [
            _make_event("mcp__server", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["mcp_servers"] == {}

    def test_mcp_tools_also_in_regular_tool_stats(self):
        """MCP tools should appear in both mcp_servers and regular tool stats."""
        events = [
            _make_event("mcp__slack__send", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert "mcp__slack__send" in stats["tools"]
        assert "slack" in stats["mcp_servers"]

    def test_mcp_servers_sorted_by_calls(self):
        events = [
            _make_event("mcp__alpha__tool", seq=1),
            _make_event("mcp__beta__tool", seq=2),
            _make_event("mcp__beta__tool", seq=3),
        ]
        stats = compute_tool_statistics(events)
        names = list(stats["mcp_servers"].keys())
        assert names == ["beta", "alpha"]


class TestScanInstalledPlugins:
    def test_reads_enabled_plugins(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "enabledPlugins": {
                "tool-time@interagency-marketplace": True,
                "superpowers@superpowers-marketplace": True,
            }
        }))
        result = scan_installed_plugins(settings_file=settings)
        assert result == [
            "superpowers@superpowers-marketplace",
            "tool-time@interagency-marketplace",
        ]

    def test_missing_file_returns_empty(self, tmp_path):
        result = scan_installed_plugins(settings_file=tmp_path / "nonexistent.json")
        assert result == []

    def test_malformed_json_returns_empty(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("not json{{{")
        result = scan_installed_plugins(settings_file=settings)
        assert result == []

    def test_empty_enabled_plugins(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"enabledPlugins": {}}))
        result = scan_installed_plugins(settings_file=settings)
        assert result == []

    def test_non_dict_enabled_plugins(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"enabledPlugins": ["a", "b"]}))
        result = scan_installed_plugins(settings_file=settings)
        assert result == []

    def test_missing_enabled_plugins_key(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"other_key": "value"}))
        result = scan_installed_plugins(settings_file=settings)
        assert result == []
