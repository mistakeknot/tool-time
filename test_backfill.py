#!/usr/bin/env python3
"""Tests for transcript backfill: id namespacing, dedup, and source preference.

Every failure mode guarded here is silent. A colliding id drops events with
no error; a missing source preference doubles counts that still look
plausible; a lost `is_error` turns a measured failure into None. None of
these raise, so they have to be asserted directly.
"""

import json
import subprocess
import sys
from pathlib import Path

from parsers import parse_claude_code
from summarize import prefer_transcript_events, session_of

SESSION = "11111111-2222-3333-4444-555555555555"


def write_transcript(tmp_path: Path, calls: list[tuple[str, str, bool, str]]) -> Path:
    """Build a minimal Claude Code transcript.

    calls: (tool_name, file_path, is_error, result_text)
    """
    lines = []
    for i, (tool, path, is_error, result) in enumerate(calls):
        tuid = f"toolu_{i:04d}"
        lines.append({
            "type": "assistant",
            "sessionId": SESSION,
            "cwd": "/proj",
            "timestamp": "2026-08-07T00:00:00Z",
            "message": {
                "model": "claude-opus-5",
                "content": [{
                    "type": "tool_use", "id": tuid, "name": tool,
                    "input": {"file_path": path} if path else {},
                }],
            },
        })
        lines.append({
            "type": "user",
            "sessionId": SESSION,
            "timestamp": "2026-08-07T00:00:01Z",
            "message": {
                "content": [{
                    "type": "tool_result", "tool_use_id": tuid,
                    "is_error": is_error, "content": result,
                }],
            },
        })
    p = tmp_path / f"{SESSION}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


# --- id namespacing ---

def test_parser_ids_never_collide_with_hook_ids(tmp_path):
    """The whole reason transcript events never landed.

    hooks/hook.sh emits `<session>-<int>` from a counter that ticks on five
    hook events; the parser's ticks only on tool calls. Same namespace, and
    dedup-by-id silently dropped every parser event for a session the hook
    had already touched.
    """
    t = write_transcript(tmp_path, [("Read", "/a.py", False, "ok")] * 5)
    parser_ids = {e["id"] for e in parse_claude_code(t)}
    hook_ids = {f"{SESSION}-{n}" for n in range(1, 100)}
    assert parser_ids.isdisjoint(hook_ids), (
        f"parser ids collide with hook ids: {parser_ids & hook_ids}"
    )


def test_parser_ids_are_stable_across_runs(tmp_path):
    """Dedup depends on the same call getting the same id every parse."""
    t = write_transcript(tmp_path, [("Read", "/a.py", False, "ok")] * 3)
    assert [e["id"] for e in parse_claude_code(t)] == [e["id"] for e in parse_claude_code(t)]


def test_parser_emits_explicit_session(tmp_path):
    t = write_transcript(tmp_path, [("Read", "/a.py", False, "ok")])
    events = list(parse_claude_code(t))
    assert all(e["session"] == SESSION for e in events)
    assert all(session_of(e) == SESSION for e in events)


def test_session_of_falls_back_for_hook_events():
    """Hook events have no `session` key and must still resolve."""
    assert session_of({"id": f"{SESSION}-42"}) == SESSION


# --- error truth ---

def test_tool_failure_survives_into_the_event(tmp_path):
    t = write_transcript(tmp_path, [
        ("Edit", "/a.py", True, "<tool_use_error>File has not been read yet.</tool_use_error>"),
        ("Edit", "/b.py", False, "ok"),
    ])
    events = {e["file"]: e for e in parse_claude_code(t)}
    assert "has not been read yet" in events["/a.py"]["error"]
    assert events["/b.py"]["error"] is None


def test_transcript_events_are_error_observable(tmp_path):
    """summarize.py counts errors only from `event == "ToolUse"`."""
    t = write_transcript(tmp_path, [("Edit", "/a.py", True, "boom")])
    assert all(e["event"] == "ToolUse" for e in parse_claude_code(t))


# --- source preference ---

def hook_ev(session, n, tool="Edit"):
    return {"id": f"{session}-{n}", "event": "PostToolUse", "tool": tool}


def tool_ev(session, n, tool="Edit", error=None):
    return {"id": f"{session}-t{n}", "session": session, "event": "ToolUse",
            "tool": tool, "error": error}


def test_hook_events_dropped_for_covered_session():
    """Both paths record the same call — keeping both doubles every count."""
    events = [hook_ev("s1", 1), hook_ev("s1", 2), tool_ev("s1", 1), tool_ev("s1", 2)]
    kept = prefer_transcript_events(events)
    assert len(kept) == 2
    assert all(e["event"] == "ToolUse" for e in kept)


def test_hook_events_kept_for_uncovered_session():
    """Preference is per session: backfill coverage is per session, so a
    global rule would erase sessions the transcript never reached."""
    events = [hook_ev("s1", 1), tool_ev("s1", 1), hook_ev("s2", 1), hook_ev("s2", 2)]
    kept = prefer_transcript_events(events)
    sessions = [session_of(e) for e in kept]
    assert sessions.count("s2") == 2, "uncovered session lost its only events"
    assert sessions.count("s1") == 1


def test_no_transcript_events_is_a_passthrough():
    events = [hook_ev("s1", 1), hook_ev("s1", 2)]
    assert prefer_transcript_events(events) == events


def test_preference_does_not_drop_non_tool_events():
    """UserPromptSubmit/Stop ride the same file and are not tool records."""
    other = {"id": "s1-9", "event": "Stop", "tool": ""}
    kept = prefer_transcript_events([other, tool_ev("s1", 1)])
    assert other in kept


# --- end to end ---

def test_backfill_is_idempotent(tmp_path, monkeypatch):
    """A second run must write nothing, or events.jsonl grows without bound."""
    import backfill

    events_file = tmp_path / "events.jsonl"
    monkeypatch.setattr(backfill, "EVENTS_FILE", events_file)
    monkeypatch.setattr(backfill, "DATA_DIR", tmp_path)
    t = write_transcript(tmp_path, [("Edit", "/a.py", True, "boom"),
                                    ("Read", "/a.py", False, "ok")])
    monkeypatch.setattr(backfill, "find_claude_code_sessions", lambda: [t])
    monkeypatch.setattr(backfill, "find_codex_sessions", lambda: [])
    monkeypatch.setattr(backfill, "find_openclaw_sessions", lambda: [])
    monkeypatch.setattr(sys, "argv", ["backfill.py", "--quiet"])

    backfill.main()
    first = events_file.read_text().splitlines()
    backfill.main()
    second = events_file.read_text().splitlines()

    assert len(first) == 2
    assert first == second, "re-running backfill duplicated events"


def test_load_existing_ids_ignores_hook_events(tmp_path, monkeypatch):
    """Holding hook ids would bloat the set with ids that cannot collide."""
    import backfill

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        json.dumps({"id": "s1-1", "event": "PostToolUse"}) + "\n"
        + json.dumps({"id": "s1-t1", "event": "ToolUse"}) + "\n"
    )
    monkeypatch.setattr(backfill, "EVENTS_FILE", events_file)
    assert backfill.load_existing_ids() == {"s1-t1"}


def test_backfill_cli_runs():
    """The hook invokes this as a subprocess; an import error there is silent."""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "backfill.py"), "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "--days" in r.stdout
