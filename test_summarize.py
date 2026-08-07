#!/usr/bin/env python3
"""Tests for summarize.py."""

import json
import os
import tempfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from summarize import (
    compute_tool_statistics,
    is_user_rejection,
    load_events,
    main as summarize_main,
    scan_installed_plugins,
)


def _make_event(
    tool: str,
    event_type: str = "PostToolUse",
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

    def test_pre_tool_use_not_counted_as_call(self):
        """Calls count at PostToolUse — a Pre+Post pair must count once."""
        events = [
            _make_event("Edit", event_type="PreToolUse", seq=1),
            _make_event("Edit", event_type="PostToolUse", error="file not found", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Edit"]["calls"] == 1
        # The hook path cannot observe failures, so errors stays unmeasured
        # rather than being fabricated from the payload.
        assert stats["tools"]["Edit"]["errors"] is None

    def test_post_tool_use_only_stream_counts(self):
        """Regression: production logs contain ONLY PostToolUse events for
        tools — aggregates must be non-empty for such a stream."""
        events = [
            _make_event("Read", event_type="PostToolUse", seq=1),
            _make_event("Bash", event_type="PostToolUse", seq=2),
            _make_event("Task", event_type="PostToolUse", skill="clavain:sprint", seq=3),
            _make_event("mcp__slack__send", event_type="PostToolUse", seq=4),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Read"]["calls"] == 1
        assert stats["tools"]["Bash"]["calls"] == 1
        assert stats["skills"]["clavain:sprint"]["calls"] == 1
        assert stats["mcp_servers"]["slack"]["calls"] == 1

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
        """Skills count at PostToolUse only — a Pre+Post pair counts once."""
        events = [
            _make_event("Task", event_type="PreToolUse", skill="foo", seq=1),
            _make_event("Task", event_type="PostToolUse", skill="foo", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"]["foo"]["calls"] == 1


class TestPathLikeSkillValues:
    """Historical hook bug: the "skill" field frequently contains absolute
    file paths. Paths must never be counted as skills (privacy: they'd
    flow into stats.json and on to the community upload), but when the
    event has no "file" key the path is recovered as the file for the
    edit-without-read logic."""

    def test_path_like_skill_not_counted_as_skill(self):
        events = [
            _make_event("Read", skill="/Users/sma/projects/secret/file.py", seq=1),
            _make_event("Edit", skill="/Users/sma/projects/secret/file.py", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"] == {}

    def test_relative_path_skill_not_counted(self):
        events = [
            _make_event("Read", skill="src/lib/thing.ts", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"] == {}

    def test_real_skills_still_counted_alongside_path_values(self):
        events = [
            _make_event("Task", skill="clavain:sprint", seq=1),
            _make_event("Read", skill="/Users/sma/projects/foo/bar.py", seq=2),
        ]
        stats = compute_tool_statistics(events)
        assert stats["skills"] == {"clavain:sprint": {"calls": 1}}

    def test_path_skill_recovered_as_file_for_edit_without_read(self):
        """Data recovery on pre-fix events: the misplaced path is the file.
        Session s1 reads then edits the file (OK); session s2 edits it
        without reading (flagged)."""
        path = "/Users/sma/projects/foo/bar.py"
        events = [
            _make_event("Read", skill=path, session_id="s1", seq=1),
            _make_event("Edit", skill=path, session_id="s1", seq=2),
            _make_event("Edit", skill=path, session_id="s2", seq=1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["edit_without_read_count"] == 1
        assert stats["skills"] == {}

    def test_path_skill_does_not_override_real_file_key(self):
        """When the event carries a real "file" key, that wins — the
        path-like skill value is not used for edit-without-read."""
        events = [
            _make_event("Read", file="/real.py", session_id="s1", seq=1),
            _make_event(
                "Edit", file="/real.py",
                skill="/Users/sma/projects/other.py",
                session_id="s1", seq=2,
            ),
        ]
        stats = compute_tool_statistics(events)
        # If the skill path overrode the file key, the Edit would target
        # an unread file and be flagged.
        assert stats["edit_without_read_count"] == 0


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
            _make_event("mcp__slack__send", event_type="ToolUse", error="timeout", seq=2),
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


class TestLastUsed:
    def test_tool_last_used_is_max_ts(self):
        t1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 3, 15, 30, 0, tzinfo=timezone.utc)
        events = [
            _make_event("Read", seq=1, ts=t2),
            _make_event("Read", seq=2, ts=t1),
        ]
        stats = compute_tool_statistics(events)
        assert stats["tools"]["Read"]["last_used"] == "2026-07-03T15:30:00Z"

    def test_mcp_server_last_used(self):
        t1 = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
        events = [_make_event("mcp__slack__send", seq=1, ts=t1)]
        stats = compute_tool_statistics(events)
        assert stats["mcp_servers"]["slack"]["last_used"] == "2026-07-02T09:00:00Z"

    def test_missing_ts_gives_null_last_used(self):
        ev = _make_event("Bash", seq=1)
        del ev["ts"]
        stats = compute_tool_statistics([ev])
        assert stats["tools"]["Bash"]["last_used"] is None


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


class TestAtomicStatsWrite:
    """stats.json is read in parallel (maintain.py, the interspect
    evidence bridge) — main() must swap a fully-written tempfile into
    place so a reader can never see a torn file."""

    def _run_main(self, data_dir: Path, replace_side_effect=None):
        events_file = data_dir / "events.jsonl"
        _write_events([_make_event("Read", project="/test/project")], events_file)
        with ExitStack() as stack:
            stack.enter_context(mock.patch("summarize.DATA_DIR", data_dir))
            stack.enter_context(
                mock.patch("summarize.STATS_FILE", data_dir / "stats.json")
            )
            stack.enter_context(mock.patch("summarize.EVENTS_FILE", events_file))
            stack.enter_context(
                mock.patch("summarize.os.getcwd", return_value="/test/project")
            )
            if replace_side_effect is not None:
                stack.enter_context(
                    mock.patch("summarize.os.replace", side_effect=replace_side_effect)
                )
            summarize_main()

    def test_old_stats_intact_until_replace(self, tmp_path):
        """Regression: at the moment of the swap, the destination must
        still hold the previous complete document and the source must be
        a complete new document — never a partial write in place."""
        stats_file = tmp_path / "stats.json"
        stats_file.write_text('{"old": true}\n')

        real_replace = os.replace
        observed = {}

        def checking_replace(src, dst):
            observed["dst_at_replace"] = Path(dst).read_text()
            observed["src_doc"] = json.loads(Path(src).read_text())
            return real_replace(src, dst)

        self._run_main(tmp_path, replace_side_effect=checking_replace)

        assert observed["dst_at_replace"] == '{"old": true}\n'
        assert observed["src_doc"]["total_events"] == 1

        final = json.loads(stats_file.read_text())
        assert final["total_events"] == 1

    def test_no_tempfile_leftovers(self, tmp_path):
        self._run_main(tmp_path)
        leftovers = [
            p.name for p in tmp_path.iterdir()
            if p.name not in ("stats.json", "events.jsonl")
        ]
        assert leftovers == []
        # And the result is complete, parseable JSON
        stats = json.loads((tmp_path / "stats.json").read_text())
        assert stats["tools"]["Read"]["calls"] == 1
