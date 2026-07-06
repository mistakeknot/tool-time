#!/usr/bin/env python3
"""Tests for rig.py."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rig import (
    command_string,
    distinctive_token,
    find_duplicates,
    main,
    parse_mcp_tool,
    token_matches_process,
)


def _make_event(
    tool: str,
    event_type: str = "PostToolUse",
    error: str | None = None,
    skill: str | None = None,
    session_id: str = "sess1",
    seq: int = 1,
    days_ago: float = 0,
) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ev = {
        "v": 1,
        "id": f"{session_id}-{seq}",
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event_type,
        "tool": tool,
        "project": "/test/project",
        "error": error,
        "source": "claude-code",
    }
    if skill:
        ev["skill"] = skill
    return ev


def _write_events(events: list[dict], path: Path) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


@pytest.fixture
def rig_env(tmp_path, monkeypatch):
    """Point every rig.py input at fixture paths under tmp_path."""
    data_dir = tmp_path / "tool-time"
    data_dir.mkdir()
    monkeypatch.setenv("TOOL_TIME_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOOL_TIME_CLAUDE_JSON", str(tmp_path / "claude.json"))
    monkeypatch.setenv("TOOL_TIME_SETTINGS_JSON", str(tmp_path / "settings.json"))
    monkeypatch.setenv("TOOL_TIME_PLUGIN_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("TOOL_TIME_PS_CMD", "true")  # no processes
    return tmp_path


def _write_fixture_configs(tmp_path: Path) -> None:
    """Standard fixture: qmd registered globally AND via plugin interknow."""
    (tmp_path / "claude.json").write_text(json.dumps({
        "mcpServers": {
            "qmd": {
                "command": "node",
                "args": ["/Users/x/.claude/qmd-server/dist/index.js"],
            },
            "lowbeer": {"command": "/usr/local/bin/lowbeer-mcp", "args": []},
        },
        "projects": {
            "/proj/a": {
                "mcpServers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    },
                },
            },
        },
    }))
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {
            "interknow@interagency-marketplace": True,
            "disabled-one@interagency-marketplace": False,
        },
    }))
    plugin_dir = tmp_path / "cache" / "interagency-marketplace" / "interknow" / "1.0.0"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "interknow",
        "skills": ["./skills/recall", "./skills/compound"],
        "mcpServers": {
            "qmd": {
                "command": "node",
                "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"],
            },
        },
    }))


def _write_fixture_events(data_dir: Path) -> None:
    events = [
        _make_event("mcp__qmd__query", seq=1),
        _make_event("mcp__qmd__query", seq=2, days_ago=2),
        # Plugin-prefixed event — separate qualified identity, never merged
        _make_event("mcp__plugin_interknow_qmd__get", seq=3),
        # lowbeer used 45 days ago: in 90d window, not 30d
        _make_event("mcp__lowbeer__get_memory", seq=4, days_ago=45),
        # and 100 days ago: only in calls_total
        _make_event("mcp__lowbeer__get_memory", seq=5, days_ago=100),
        # Skill events for plugin attribution
        _make_event("Skill", skill="interknow:recall", seq=6),
        # Field-corruption bug: file path in the skill field
        _make_event("Read", skill="/Users/x/projects/foo/bar.py", seq=7),
        _make_event("Bash", seq=8),
    ]
    _write_events(events, data_dir / "events.jsonl")


def _write_two_plugin_configs(tmp_path: Path, server_name_a: str, server_name_b: str) -> None:
    """Two unrelated plugins, each shipping the standard relative layout."""
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {
            "aplug@interagency-marketplace": True,
            "bplug@interagency-marketplace": True,
        },
    }))
    for plugin, server in (("aplug", server_name_a), ("bplug", server_name_b)):
        plugin_dir = tmp_path / "cache" / "interagency-marketplace" / plugin / "1.0.0"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": plugin,
            "mcpServers": {
                server: {
                    "command": "node",
                    "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"],
                },
            },
        }))


def _run_and_read(data_dir: Path, capsys) -> dict:
    main()
    out = capsys.readouterr().out.strip()
    assert out == str(data_dir / "rig.json")
    return json.loads((data_dir / "rig.json").read_text())


class TestParseMcpTool:
    def test_plain_server(self):
        assert parse_mcp_tool("mcp__slack__send") == ("slack", "slack", None, None)

    def test_plugin_prefixed(self):
        assert parse_mcp_tool("mcp__plugin_interknow_qmd__get") == (
            "plugin:interknow/qmd", "qmd", "plugin:interknow", "plugin_interknow_qmd",
        )

    def test_hyphenated_plugin_and_server(self):
        assert parse_mcp_tool("mcp__plugin_tldr-swinton_tldr-code__arch") == (
            "plugin:tldr-swinton/tldr-code", "tldr-code",
            "plugin:tldr-swinton", "plugin_tldr-swinton_tldr-code",
        )

    def test_non_mcp_tool(self):
        assert parse_mcp_tool("Bash") is None

    def test_empty_server_segment(self):
        assert parse_mcp_tool("mcp____tool") is None


class TestCommandHelpers:
    def test_command_string_generic_placeholder(self):
        cfg = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"]}
        assert command_string(cfg) == "node <root>/dist/index.js"

    def test_command_string_per_plugin_placeholder(self):
        cfg = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"]}
        assert command_string(cfg, plugin="interknow") == "node <root:interknow>/dist/index.js"

    def test_command_string_non_dict(self):
        assert command_string(None) == ""

    def test_distinctive_token_skips_launchers(self):
        cfg = {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]}
        assert distinctive_token(cfg) == "@playwright/mcp@latest"

    def test_distinctive_token_package_flag(self):
        cfg = {"command": "npx", "args": ["-y", "--package=task-master-ai"]}
        assert distinctive_token(cfg) == "task-master-ai"

    def test_distinctive_token_plugin_root_relative(self):
        cfg = {"command": "npx", "args": ["tsx", "${CLAUDE_PLUGIN_ROOT}/src/index.ts"]}
        assert distinctive_token(cfg) == "src/index.ts"

    def test_distinctive_token_plugin_root_resolved(self):
        cfg = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"]}
        assert distinctive_token(
            cfg, plugin_root="/cache/mkt/interknow/1.0.0"
        ) == "/cache/mkt/interknow/1.0.0/dist/index.js"

    def test_distinctive_token_absolute_command(self):
        cfg = {"command": "/usr/local/bin/lowbeer-mcp", "args": []}
        assert distinctive_token(cfg) == "/usr/local/bin/lowbeer-mcp"


class TestRedaction:
    """Regression: MCP configs with inline credentials must never reach rig.json."""

    def test_value_after_sensitive_flag_redacted(self):
        cfg = {"command": "my-server", "args": ["--api-key", "sk-live-abc123"]}
        assert command_string(cfg) == "my-server --api-key <redacted>"

    def test_sensitive_command_name_does_not_shield_flag_value(self):
        # "secretive-mcp" matches the sensitive regex itself; the flag
        # must still pass through so its VALUE is what gets redacted
        cfg = {"command": "secretive-mcp", "args": ["--api-key", "sk-live-abc123"]}
        assert command_string(cfg) == "secretive-mcp --api-key <redacted>"

    def test_inline_sensitive_flag_value_redacted(self):
        cfg = {"command": "my-server", "args": ["--token=tok_sekret", "--port=8080"]}
        assert command_string(cfg) == "my-server --token=<redacted> --port=8080"

    def test_url_query_string_and_userinfo_stripped(self):
        cfg = {"url": "https://user:hunter2@api.example.com/mcp?token=tok_sekret#frag"}
        assert command_string(cfg) == "https://api.example.com/mcp"

    def test_high_entropy_bare_token_redacted(self):
        secret = "aB3dE5fG7hI9jK1lM2nO4pQ6rS8tU0vWxYz_-42"
        assert len(secret) >= 32
        cfg = {"command": "my-server", "args": [secret]}
        assert command_string(cfg) == "my-server <redacted>"

    def test_path_like_long_token_not_redacted(self):
        path = "/Users/x/some/deeply/nested/long/path/to/an/mcp/server/binary"
        cfg = {"command": path, "args": []}
        assert command_string(cfg) == path

    def test_distinctive_token_unaffected_by_secrets(self):
        # ps matching still keys on the executable/package name
        cfg = {"command": "npx", "args": ["-y", "@scope/pkg", "--api-key", "sk-live-abc"]}
        assert distinctive_token(cfg) == "@scope/pkg"
        cfg = {"command": "my-server", "args": ["--api-key", "sk-live-abc"]}
        assert distinctive_token(cfg) == "my-server"

    def test_rig_json_contains_no_credentials(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        (tmp_path / "claude.json").write_text(json.dumps({
            "mcpServers": {
                "secretive": {
                    "command": "secretive-mcp",
                    "args": ["--api-key", "sk-live-badbadbad"],
                },
                "webby": {
                    "url": "https://alice:hunter2@example.com/mcp?token=tok_sekret_value",
                },
            },
        }))
        _run_and_read(data_dir, capsys)
        raw = (data_dir / "rig.json").read_text()
        assert "sk-live-badbadbad" not in raw
        assert "tok_sekret_value" not in raw
        assert "hunter2" not in raw
        assert "alice" not in raw
        # The flag name and host survive so the command is still recognizable
        assert "--api-key" in raw
        assert "https://example.com/mcp" in raw


class TestProcessMatching:
    """Regression: substring matching let 'qmd' absorb 'someother_qmd' processes."""

    def test_bare_token_requires_boundary(self):
        assert token_matches_process("qmd", "qmd --serve")
        assert token_matches_process("qmd", "/usr/local/bin/qmd --serve")
        assert not token_matches_process("qmd", "someother_qmd --daemon")
        assert not token_matches_process("qmd", "node /opt/someother_qmd/server.js")

    def test_path_token_matches_on_path_suffix(self):
        assert token_matches_process("dist/index.js", "node /a/b/dist/index.js")
        assert token_matches_process("/x/qmd.js", "node /x/qmd.js")
        assert not token_matches_process("dist/index.js", "node /a/b/dist/index.jsx")
        assert not token_matches_process("/x/qmd.js", "node /y/x/qmd.jsx")

    def test_empty_token_never_matches(self):
        assert not token_matches_process("", "anything at all")

    def test_lookalike_process_not_attributed(self, rig_env, monkeypatch, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        (tmp_path / "claude.json").write_text(json.dumps({
            "mcpServers": {"qmd": {"command": "qmd", "args": ["serve"]}},
        }))
        ps_stub = tmp_path / "ps_stub.sh"
        ps_stub.write_text(
            "#!/bin/sh\n"
            "echo '  RSS ARGS'\n"
            "echo '499712 someother_qmd --daemon'\n"  # ~488MB lookalike
            "echo '102400 qmd serve'\n"
        )
        ps_stub.chmod(0o755)
        monkeypatch.setenv("TOOL_TIME_PS_CMD", f"sh {ps_stub}")
        rig = _run_and_read(data_dir, capsys)

        assert rig["servers"]["qmd"]["proc_count"] == 1
        assert rig["servers"]["qmd"]["rss_mb"] == 100.0


class TestQualifiedIdentity:
    """Regression: plugin_<plugin>_<server> events must not merge with
    same-named servers from other sources."""

    def test_plugin_and_global_same_name_not_merged(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        bare = rig["servers"]["qmd"]
        assert bare["name"] == "qmd"
        assert bare["calls_30d"] == 2  # only the direct mcp__qmd__ calls
        assert bare["sources"] == ["global"]
        assert bare["aliases"] == []

        qualified = rig["servers"]["plugin:interknow/qmd"]
        assert qualified["name"] == "qmd"
        assert qualified["calls_30d"] == 1  # only the plugin-prefixed call
        assert qualified["sources"] == ["plugin:interknow"]
        assert qualified["aliases"] == ["plugin_interknow_qmd"]
        assert qualified["last_used"] is not None

    def test_same_name_across_two_plugins_not_cross_credited(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_two_plugin_configs(tmp_path, "qmd", "qmd")
        events = [
            _make_event("mcp__plugin_aplug_qmd__query", seq=1),
            _make_event("mcp__plugin_bplug_qmd__get", seq=2),
        ]
        _write_events(events, data_dir / "events.jsonl")
        rig = _run_and_read(data_dir, capsys)

        # 2 real calls stay 2 total: 1 per plugin, never 2 to each
        assert rig["servers"]["plugin:aplug/qmd"]["calls_30d"] == 1
        assert rig["servers"]["plugin:bplug/qmd"]["calls_30d"] == 1
        assert "qmd" not in rig["servers"]
        assert rig["plugins"]["aplug"]["mcp_calls_30d"] == 1
        assert rig["plugins"]["bplug"]["mcp_calls_30d"] == 1

        # The possible relationship surfaces only as a name_match hypothesis
        name_dupes = [d for d in rig["duplicates"] if d["reason"] == "name_match"]
        assert len(name_dupes) == 1
        assert name_dupes[0]["canonical"] == "qmd"
        sources = sorted(r["source"] for r in name_dupes[0]["registrations"])
        assert sources == ["plugin:aplug", "plugin:bplug"]

    def test_window_counts(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        lowbeer = rig["servers"]["lowbeer"]
        assert lowbeer["calls_30d"] == 0
        assert lowbeer["calls_90d"] == 1
        assert lowbeer["calls_total"] == 2

    def test_registered_but_never_used_server_present(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        playwright = rig["servers"]["playwright"]
        assert playwright["calls_total"] == 0
        assert playwright["last_used"] is None
        assert playwright["sources"] == ["project:/proj/a"]


class TestDuplicates:
    def test_global_plus_plugin_qmd_is_name_match_hypothesis(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        dupes = {d["canonical"]: d for d in rig["duplicates"]}
        assert "qmd" in dupes
        assert dupes["qmd"]["reason"] == "name_match"
        sources = sorted(r["source"] for r in dupes["qmd"]["registrations"])
        assert sources == ["global", "plugin:interknow"]

    def test_same_command_different_names(self):
        regs = [
            {"name": "alpha", "source": "global", "command": "node /x/server.js", "token": "/x/server.js"},
            {"name": "beta", "source": "project:/p", "command": "node /x/server.js", "token": "/x/server.js"},
        ]
        dupes = find_duplicates(regs)
        assert len(dupes) == 1
        assert dupes[0]["canonical"] == "alpha"
        assert dupes[0]["reason"] == "command_match"
        assert len(dupes[0]["registrations"]) == 2

    def test_no_duplicates(self):
        regs = [
            {"name": "alpha", "source": "global", "command": "node /x/a.js", "token": "/x/a.js"},
            {"name": "beta", "source": "global", "command": "node /x/b.js", "token": "/x/b.js"},
        ]
        assert find_duplicates(regs) == []

    def test_command_group_not_rereported_under_name_group(self):
        regs = [
            {"name": "qmd", "source": "global", "command": "node /x/qmd.js", "token": "/x/qmd.js"},
            {"name": "qmd", "source": "plugin:interknow", "command": "node /x/qmd.js", "token": "/x/qmd.js"},
        ]
        assert len(find_duplicates(regs)) == 1

    def test_two_plugins_standard_layout_not_duplicates(self, rig_env, capsys):
        # Regression: two unrelated plugins both ship
        # "node ${CLAUDE_PLUGIN_ROOT}/dist/index.js" — not the same server
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_two_plugin_configs(tmp_path, "aserv", "bserv")
        _write_events([_make_event("Bash", seq=1)], data_dir / "events.jsonl")
        rig = _run_and_read(data_dir, capsys)

        assert rig["duplicates"] == []
        assert rig["servers"]["plugin:aplug/aserv"]["commands"] == [
            "node <root:aplug>/dist/index.js"
        ]
        assert rig["servers"]["plugin:bplug/bserv"]["commands"] == [
            "node <root:bplug>/dist/index.js"
        ]
        # Unused plugin servers appear in zero_use under qualified keys
        assert rig["zero_use"] == ["plugin:aplug/aserv", "plugin:bplug/bserv"]


class TestZeroUse:
    def test_zero_use_lists_registered_unused(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        # lowbeer's last call was 45+ days ago; playwright was never called;
        # both qmd identities were called within the window
        assert rig["zero_use"] == ["lowbeer", "playwright"]
        assert "qmd" not in rig["zero_use"]
        assert "plugin:interknow/qmd" not in rig["zero_use"]


class TestProcesses:
    def test_rss_aggregation(self, rig_env, monkeypatch, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        plugin_root = tmp_path / "cache" / "interagency-marketplace" / "interknow" / "1.0.0"
        ps_stub = tmp_path / "ps_stub.sh"
        ps_stub.write_text(
            "#!/bin/sh\n"
            "echo '  RSS ARGS'\n"
            "echo '102400 node /Users/x/.claude/qmd-server/dist/index.js'\n"
            f"echo '204800 node {plugin_root}/dist/index.js'\n"
            "echo ' 51200 /usr/local/bin/lowbeer-mcp'\n"
            "echo ' 12345 some-unrelated-process'\n"
        )
        ps_stub.chmod(0o755)
        monkeypatch.setenv("TOOL_TIME_PS_CMD", f"sh {ps_stub}")
        rig = _run_and_read(data_dir, capsys)

        # Each qualified identity claims only its own process
        assert rig["servers"]["qmd"]["proc_count"] == 1
        assert rig["servers"]["qmd"]["rss_mb"] == 100.0
        assert rig["servers"]["plugin:interknow/qmd"]["proc_count"] == 1
        assert rig["servers"]["plugin:interknow/qmd"]["rss_mb"] == 200.0
        assert rig["servers"]["lowbeer"]["proc_count"] == 1
        assert rig["servers"]["lowbeer"]["rss_mb"] == 50.0
        assert rig["servers"]["playwright"]["proc_count"] == 0
        assert rig["servers"]["playwright"]["rss_mb"] == 0.0

    def test_ps_failure_warns_not_crashes(self, rig_env, monkeypatch, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        monkeypatch.setenv("TOOL_TIME_PS_CMD", "false")
        rig = _run_and_read(data_dir, capsys)

        assert any("ps exited" in w for w in rig["meta"]["warnings"])
        assert rig["servers"]["qmd"]["proc_count"] == 0


class TestPlugins:
    def test_plugin_aggregation(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        rig = _run_and_read(data_dir, capsys)

        interknow = rig["plugins"]["interknow"]
        # Only the plugin's own qualified server counts — the global qmd's
        # 2 direct calls are not credited to the plugin
        assert interknow["mcp_calls_30d"] == 1
        assert interknow["skill_calls_30d"] == 1  # "interknow:recall"
        assert interknow["skills_shipped"] == 2
        assert "disabled-one" not in rig["plugins"]


class TestGracefulDegradation:
    def test_all_inputs_missing(self, rig_env, monkeypatch, capsys):
        # rig_env points every input at nonexistent fixture files
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        monkeypatch.setenv("TOOL_TIME_PS_CMD", "false")
        rig = _run_and_read(data_dir, capsys)

        assert rig["servers"] == {}
        assert rig["duplicates"] == []
        assert rig["zero_use"] == []
        assert rig["plugins"] == {}
        assert rig["meta"]["events_scanned"] == 0
        assert rig["meta"]["window_days"] == [30, 90]
        assert len(rig["meta"]["warnings"]) >= 4  # events, claude.json, settings, cache, ps

    def test_malformed_configs(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        (tmp_path / "claude.json").write_text("not json{{{")
        (tmp_path / "settings.json").write_text("[1, 2, 3]")
        (data_dir / "events.jsonl").write_text("garbage\n")
        rig = _run_and_read(data_dir, capsys)

        assert rig["servers"] == {}
        assert rig["plugins"] == {}
        assert any("claude.json" in w for w in rig["meta"]["warnings"])
        assert any("settings.json" in w for w in rig["meta"]["warnings"])

    def test_missing_plugin_cache_dir(self, rig_env, capsys):
        tmp_path = rig_env
        data_dir = tmp_path / "tool-time"
        _write_fixture_configs(tmp_path)
        _write_fixture_events(data_dir)
        import shutil
        shutil.rmtree(tmp_path / "cache")
        rig = _run_and_read(data_dir, capsys)

        # Plugin still listed (from settings), just with no cache-derived data
        assert rig["plugins"]["interknow"]["skills_shipped"] == 0
        assert any("plugin cache" in w for w in rig["meta"]["warnings"])
