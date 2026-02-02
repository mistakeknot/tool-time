#!/usr/bin/env python3
"""Transcript parsers for Claude Code, Codex CLI, and OpenClaw.

Each parser reads session JSONL transcripts and yields unified event dicts
compatible with events.jsonl schema.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

# Unified event schema (v1)
# {"v":1, "id":"<session>-<seq>", "ts":"<iso8601>", "event":"ToolUse",
#  "tool":"<name>", "project":"<cwd>", "error":null,
#  "source":"claude-code"|"codex"|"openclaw", "file":null|"<path>"}


def parse_claude_code(session_path: Path) -> Generator[dict, None, None]:
    """Parse a Claude Code session transcript.

    Format: ~/.claude/projects/<project-slug>/<session-id>.jsonl
    Records have top-level 'type' field. Tool calls are in assistant messages
    with content blocks of type 'tool_use'. Results come back as user messages
    with content blocks of type 'tool_result'.
    """
    cwd = ""
    session_id = ""
    seq = 0

    # Collect tool_use calls keyed by tool_use_id so we can match results
    pending_calls: dict[str, dict] = {}

    for line in session_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not cwd:
            cwd = record.get("cwd", "")
        if not session_id:
            session_id = record.get("sessionId", session_path.stem)

        ts = record.get("timestamp", "")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        record_type = record.get("type", "")
        msg = record.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        if record_type == "assistant":
            model = msg.get("model", "")
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    seq += 1
                    tool_name = block.get("name", "")
                    tool_use_id = block.get("id", "")
                    tool_input = block.get("input", {})
                    file_path = tool_input.get("file_path") or tool_input.get("path") or None
                    skill_name = tool_input.get("skill") if tool_name == "Skill" else None
                    event_dict = {
                        "v": 1,
                        "id": f"{session_id}-{seq}",
                        "ts": ts,
                        "event": "ToolUse",
                        "tool": tool_name,
                        "project": cwd,
                        "error": None,
                        "source": "claude-code",
                    }
                    if model:
                        event_dict["model"] = model
                    if file_path:
                        event_dict["file"] = file_path
                    if skill_name:
                        event_dict["skill"] = skill_name
                    pending_calls[tool_use_id] = event_dict

        elif record_type == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    event = pending_calls.pop(tool_use_id, None)
                    if event is None:
                        continue
                    # Check for errors
                    is_error = block.get("is_error", False)
                    if is_error:
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = " ".join(
                                c.get("text", "") for c in result_content
                                if isinstance(c, dict)
                            )
                        event["error"] = str(result_content)[:200]
                    yield event

    # Yield any unmatched calls (no result seen)
    for event in pending_calls.values():
        yield event


def parse_codex(session_path: Path) -> Generator[dict, None, None]:
    """Parse a Codex CLI session transcript.

    Format: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    Records have {timestamp, type, payload}. Tool calls are response_item
    records with payload.type == 'function_call'. Results are response_item
    records with payload.type == 'function_call_output'.
    """
    cwd = ""
    session_id = session_path.stem  # rollout-<timestamp>-<uuid>
    seq = 0

    # Collect function calls keyed by call_id
    pending_calls: dict[str, dict] = {}

    for line in session_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = record.get("timestamp", "")
        record_type = record.get("type", "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue

        if record_type == "session_meta":
            cwd = payload.get("cwd", "")
            continue

        if record_type != "response_item":
            continue

        payload_type = payload.get("type", "")

        if payload_type == "function_call":
            seq += 1
            tool_name = payload.get("name", "")
            call_id = payload.get("call_id", "")
            # arguments is a JSON string in Codex transcripts
            file_path = None
            skill_name = None
            try:
                args = json.loads(payload.get("arguments", "{}"))
                file_path = args.get("file_path") or args.get("path") or None
                if tool_name == "Skill":
                    skill_name = args.get("skill")
            except (json.JSONDecodeError, TypeError):
                pass
            event_dict = {
                "v": 1,
                "id": f"{session_id}-{seq}",
                "ts": ts,
                "event": "ToolUse",
                "tool": tool_name,
                "project": cwd,
                "error": None,
                "source": "codex",
            }
            if file_path:
                event_dict["file"] = file_path
            if skill_name:
                event_dict["skill"] = skill_name
            pending_calls[call_id] = event_dict

        elif payload_type == "function_call_output":
            call_id = payload.get("call_id", "")
            event = pending_calls.pop(call_id, None)
            if event is None:
                continue
            output = str(payload.get("output", ""))
            # Codex outputs include "Exit code: N" for shell commands
            if "Exit code: " in output:
                try:
                    code_str = output.split("Exit code: ")[1].split("\n")[0].strip()
                    if code_str != "0":
                        event["error"] = output[:200]
                except (IndexError, ValueError):
                    pass
            yield event

    # Yield any unmatched calls
    for event in pending_calls.values():
        yield event


def parse_openclaw(session_path: Path) -> Generator[dict, None, None]:
    """Parse an OpenClaw (Moltbot/Clawdbot) session transcript.

    Format: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
    Also: ~/.moltbot/... and ~/.clawdbot/...
    Records have top-level 'type' field. Tool calls are in assistant messages
    with content blocks of type 'toolCall'. Results are separate messages with
    role 'toolResult' containing toolCallId, toolName, and isError.
    """
    cwd = ""
    session_id = session_path.stem
    model = ""
    seq = 0

    pending_calls: dict[str, dict] = {}

    for line in session_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        record_type = record.get("type", "")
        ts = record.get("timestamp", "")

        if record_type == "session":
            cwd = record.get("cwd", "")
            session_id = record.get("id", session_id)
            continue

        if record_type == "model_change":
            model = record.get("modelId", "")
            continue

        if record_type != "message":
            continue

        msg = record.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        if role == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "toolCall":
                    seq += 1
                    tool_name = block.get("name", "")
                    tool_call_id = block.get("id", "")
                    args = block.get("arguments", {})
                    if not isinstance(args, dict):
                        args = {}
                    file_path = args.get("path") or args.get("file_path") or None
                    event_dict = {
                        "v": 1,
                        "id": f"{session_id}-{seq}",
                        "ts": ts,
                        "event": "ToolUse",
                        "tool": tool_name,
                        "project": cwd,
                        "error": None,
                        "source": "openclaw",
                    }
                    if model:
                        event_dict["model"] = model
                    if file_path:
                        event_dict["file"] = file_path
                    pending_calls[tool_call_id] = event_dict

        elif role == "toolResult":
            tool_call_id = msg.get("toolCallId", "")
            event = pending_calls.pop(tool_call_id, None)
            if event is None:
                continue
            if msg.get("isError"):
                result_text = ""
                for block in content:
                    if isinstance(block, dict):
                        result_text += block.get("text", "")
                event["error"] = result_text[:200] if result_text else "error"
            yield event

    for event in pending_calls.values():
        yield event


def find_openclaw_sessions(base_dirs: list[Path] | None = None) -> list[Path]:
    """Find all OpenClaw/Moltbot/Clawdbot session transcript files."""
    if base_dirs is None:
        home = Path.home()
        base_dirs = [
            home / ".openclaw" / "agents",
            home / ".moltbot" / "agents",
            home / ".clawdbot" / "agents",
        ]
    sessions: list[Path] = []
    seen: set[str] = set()
    for base in base_dirs:
        if not base.exists():
            continue
        for p in sorted(base.glob("*/sessions/*.jsonl")):
            # Deduplicate across directories (same session ID may exist in multiple)
            if p.name not in seen:
                seen.add(p.name)
                sessions.append(p)
    return sorted(sessions)


def find_claude_code_sessions(base: Path | None = None) -> list[Path]:
    """Find all Claude Code session transcript files."""
    base = base or Path.home() / ".claude" / "projects"
    if not base.exists():
        return []
    return sorted(base.glob("*/*.jsonl"))


def find_codex_sessions(base: Path | None = None) -> list[Path]:
    """Find all Codex CLI session transcript files."""
    base = base or Path.home() / ".codex" / "sessions"
    if not base.exists():
        return []
    return sorted(base.glob("**/rollout-*.jsonl"))
