#!/usr/bin/env python3
"""Tests for edit_stats.py and the error-observability accounting.

The regressions these lock down all share one shape: a metric that fires on
the wrong condition and reads as a measurement. See hooks/hook.sh for the
original — a substring match on tool payloads that produced 20,517 false
failures and 0 true ones.
"""

import pytest

import edit_stats
from summarize import compute_tool_statistics, is_error_observable


# --- core_change_bytes ---

def test_core_change_bytes_identical_is_zero():
    assert edit_stats.core_change_bytes("abc", "abc") == 0


def test_core_change_bytes_strips_common_affixes():
    # Only the middle token differs; the shared prefix/suffix is context.
    old = "prefix TARGET suffix"
    new = "prefix CHANGED suffix"
    assert edit_stats.core_change_bytes(old, new) == len("TARGET") + len("CHANGED")


def test_core_change_bytes_pure_insertion():
    assert edit_stats.core_change_bytes("", "hello") == len("hello")


def test_inflation_ratio_grows_with_padding():
    """Padding an edit with unchanged context raises inflation, by construction."""
    tight_core = edit_stats.core_change_bytes("a", "b")
    padded_core = edit_stats.core_change_bytes("x" * 500 + "a", "x" * 500 + "b")
    assert tight_core == padded_core == 2
    tight_total = len("a") + len("b")
    padded_total = len("x" * 500 + "a") + len("x" * 500 + "b")
    assert padded_total / padded_core > tight_total / tight_core


# --- classify_error ---

@pytest.mark.parametrize(
    "text,expected",
    [
        ("<tool_use_error>File has been modified since read, either by the user", "stale_read"),
        ("File has not been read yet. Read it first before writing to it.", "not_read_first"),
        ("<tool_use_error>String to replace not found in file.", "old_string_not_found"),
        ("Found 3 matches of the string to replace, but replace_all is false", "not_unique"),
        ("No changes to make: old_string and new_string are exactly the same.", "identical_strings"),
        ("This background session hasn't isolated its changes yet. Call EnterWorktree", "worktree_isolation"),
        ("Refusing to write through symlink: /Users/x/.claude/CLAUDE.md", "symlink_refused"),
        ("<tool_use_error>File does not exist.", "file_not_found"),
        ("InputValidationError: Edit failed due to the following issue:", "validation_error"),
        ("something entirely unfamiliar", "other"),
    ],
)
def test_classify_error(text, expected):
    assert edit_stats.classify_error(text) == expected


def test_stale_read_wins_over_file_not_found():
    """Order matters: a stale-read message must not fall through to a generic kind."""
    assert edit_stats.classify_error("File has been modified since read") == "stale_read"


# --- rate ---

def test_rate_returns_none_for_empty_population():
    """An empty denominator is never a measurement — it must not read as 0."""
    assert edit_stats.rate(0, 0) is None
    assert edit_stats.rate(0, 5) == 0.0


# --- summary construction ---

def _record(**overrides):
    base = {
        "tool": "Edit", "path": "/tmp/a.py", "extension": ".py", "project": "/tmp",
        "model": "claude-opus-5", "ts": "", "edits_count": 1, "mode": "single",
        "total_edit_bytes": 100, "core_bytes": 10, "inflation_ratio": 10.0,
        "session": "s1", "transcript": "t", "success": True,
        "error_kind": None, "error_text": None,
    }
    base.update(overrides)
    return base


def test_inflation_pool_excludes_workflow_failures():
    """A not_read_first failure would have happened at any inflation ratio.

    Pooling it would attribute a sequencing bug to string size.
    """
    records = [
        _record(success=False, error_kind="not_read_first"),
        _record(success=False, error_kind="old_string_not_found"),
        _record(success=True),
    ]
    summary = edit_stats.build_summary(records, {"transcripts_scanned": 1, "transcripts_unreadable": 0, "unresolved_calls": 0}, "test")
    assert summary["inflation"]["pool_size"] == 2


def test_failure_kinds_are_classified_by_cause():
    records = [
        _record(success=False, error_kind="not_read_first"),
        _record(success=False, error_kind="old_string_not_found"),
        _record(success=False, error_kind="symlink_refused"),
    ]
    summary = edit_stats.build_summary(records, {"transcripts_scanned": 1, "transcripts_unreadable": 0, "unresolved_calls": 0}, "test")
    classes = {k["kind"]: k["class"] for k in summary["failure_kinds"]}
    assert classes["not_read_first"] == "workflow"
    assert classes["old_string_not_found"] == "string_match"
    assert classes["symlink_refused"] == "other"


def test_summary_has_generated_timestamp():
    """maintain.py gates its tripwire on freshness; without this it can't."""
    summary = edit_stats.build_summary([], {"transcripts_scanned": 0, "transcripts_unreadable": 0, "unresolved_calls": 0}, "test")
    assert summary["generated"].endswith("Z")


def test_empty_corpus_reports_none_not_zero():
    summary = edit_stats.build_summary([], {"transcripts_scanned": 0, "transcripts_unreadable": 0, "unresolved_calls": 0}, "test")
    assert summary["totals"]["failure_rate"] is None


# --- extract_edits ---

def test_extract_edits_multiedit():
    pairs = edit_stats.extract_edits("MultiEdit", {"edits": [
        {"old_string": "a", "new_string": "b"},
        {"old_string": "c", "new_string": "d"},
    ]})
    assert pairs == [("a", "b"), ("c", "d")]


def test_extract_edits_write_has_no_match_string():
    """A Write cannot fail on string matching, so it has no inflation."""
    assert edit_stats.extract_edits("Write", {"content": "x"}) == []


# --- summarize.py observability accounting ---

def test_hook_events_are_not_error_observable():
    """PostToolUse never fires on failure, so it cannot report success either."""
    assert is_error_observable({"event": "PostToolUse"}) is False
    assert is_error_observable({"event": "ToolUse"}) is True


def test_hook_only_corpus_reports_errors_as_unmeasured():
    """The core regression: hook-only data must yield None, never 0.

    Reporting 0 here is what let a phantom rate reach the session digest.
    """
    events = [
        {"id": "s-1", "event": "PostToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:00Z"},
        {"id": "s-2", "event": "PostToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:01Z"},
    ]
    stats = compute_tool_statistics(events)
    assert stats["tools"]["Edit"]["calls"] == 2
    assert stats["tools"]["Edit"]["error_observed_calls"] == 0
    assert stats["tools"]["Edit"]["errors"] is None
    assert stats["tools"]["Edit"]["rejections"] is None


def test_legacy_error_field_is_ignored():
    """Quarantined values must not be resurrected as signal."""
    events = [
        {"id": "s-1", "event": "PostToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:00Z",
         "error": None, "error_legacy_unreliable": '{"filePath":"/x.py"}'},
    ]
    stats = compute_tool_statistics(events)
    assert stats["tools"]["Edit"]["errors"] is None


def test_transcript_events_are_counted():
    events = [
        {"id": "s-1", "event": "ToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:00Z", "error": None},
        {"id": "s-2", "event": "ToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:01Z",
         "error": "<tool_use_error>String to replace not found"},
    ]
    stats = compute_tool_statistics(events)
    assert stats["tools"]["Edit"]["error_observed_calls"] == 2
    assert stats["tools"]["Edit"]["errors"] == 1


def test_mixed_corpus_uses_only_observable_denominator():
    """98 blind hook calls must not dilute a 1-in-2 observed failure rate."""
    events = [
        {"id": f"s-{i}", "event": "PostToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:00Z"}
        for i in range(98)
    ] + [
        {"id": "s-98", "event": "ToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:00Z", "error": None},
        {"id": "s-99", "event": "ToolUse", "tool": "Edit", "ts": "2026-08-01T00:00:01Z",
         "error": "<tool_use_error>String to replace not found"},
    ]
    stats = compute_tool_statistics(events)
    tool = stats["tools"]["Edit"]
    assert tool["calls"] == 100
    assert tool["error_observed_calls"] == 2
    assert tool["errors"] == 1  # 50% of observed, not 1% of total


# --- quarantine ---

def test_quarantine_renames_only_hook_events():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "quarantine", Path(__file__).parent / "scripts" / "quarantine-legacy-errors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    hook_event = {"event": "PostToolUse", "error": '{"filePath":"/x.py"}'}
    record, changed = module.quarantine_line(dict(hook_event))
    assert changed is True
    assert record["error"] is None
    assert record["error_legacy_unreliable"] == '{"filePath":"/x.py"}'

    # Transcript events carry real is_error truth — never touch them.
    transcript_event = {"event": "ToolUse", "error": "<tool_use_error>boom"}
    record, changed = module.quarantine_line(dict(transcript_event))
    assert changed is False
    assert record["error"] == "<tool_use_error>boom"


def test_quarantine_is_idempotent():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "quarantine", Path(__file__).parent / "scripts" / "quarantine-legacy-errors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    record = {"event": "PostToolUse", "error": None, "error_legacy_unreliable": "old"}
    _, changed = module.quarantine_line(record)
    assert changed is False


# --- digest tripwire (maintain.py) ---

class TestEditDigestTripwire:
    """The tripwire must fire on real regressions and stay silent otherwise.

    The silence cases matter more than the firing case: every one of them is
    a shape that previously produced a false alarm at every session start.
    """

    @staticmethod
    def _probe(tmp_path, edit_stats, stats_tools=None):
        import json
        from datetime import datetime, timezone
        from maintain import build_digest_lines
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (tmp_path / "events.jsonl").write_text(
            json.dumps({"v": 1, "id": "s-1", "ts": now, "event": "PostToolUse", "tool": "Edit"}) + "\n")
        (tmp_path / "stats.json").write_text(json.dumps({
            "generated": now, "total_events": 200,
            "tools": stats_tools or {"Read": {"calls": 100, "error_observed_calls": 0,
                                              "errors": None, "rejections": None}},
        }))
        if edit_stats is not None:
            edit_stats.setdefault("generated", now)
            (tmp_path / "edit_stats.json").write_text(json.dumps(edit_stats))
        return build_digest_lines(tmp_path)

    def test_fires_on_real_regression_and_names_the_cause(self, tmp_path):
        lines = self._probe(tmp_path, {
            "totals": {"resolved_calls": 1000, "failed": 80, "failure_rate": 0.08},
            "failure_kinds": [{"kind": "not_read_first", "count": 50, "share_of_failures": 0.62}],
        })
        assert len(lines) == 1
        assert "not_read_first" in lines[0]  # the cause, not just a rate
        assert "8%" in lines[0]

    def test_silent_at_measured_baseline(self, tmp_path):
        """2.3% is the measured normal, not a regression."""
        assert self._probe(tmp_path, {
            "totals": {"resolved_calls": 1000, "failed": 23, "failure_rate": 0.023},
            "failure_kinds": [{"kind": "not_read_first", "count": 12, "share_of_failures": 0.52}],
        }) == []

    def test_silent_when_rate_is_none(self, tmp_path):
        """An unmeasured population must not alarm and must not crash."""
        assert self._probe(tmp_path, {
            "totals": {"resolved_calls": 0, "failed": 0, "failure_rate": None},
            "failure_kinds": [],
        }) == []

    def test_silent_on_stale_artifact(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert self._probe(tmp_path, {
            "generated": stale,
            "totals": {"resolved_calls": 1000, "failed": 800, "failure_rate": 0.8},
            "failure_kinds": [{"kind": "not_read_first", "count": 500, "share_of_failures": 0.62}],
        }) == []

    def test_silent_without_artifact(self, tmp_path):
        assert self._probe(tmp_path, None) == []

    def test_phantom_error_rate_shape_claims_no_rate(self, tmp_path):
        """The original bug: 203 calls, 103 'errors', 0 of them observable.

        This asserted blanket silence until 2026-08-07. That encoded the
        wrong invariant: what must never happen is *claiming a rate* over a
        population that could not report errors. Reporting that the
        population was unmeasured is the correct behaviour, and is now what
        the measurement-down tripwire exists to say. Silence here would mean
        a dead pipeline and a clean run look identical.
        """
        lines = self._probe(tmp_path, None, stats_tools={
            "Edit": {"calls": 203, "error_observed_calls": 0, "errors": None, "rejections": None},
        })
        assert not any("error rate" in ln for ln in lines), (
            f"claimed a rate over an unobservable population: {lines}"
        )
        assert any("measurement is down" in ln for ln in lines)
        assert "203" in lines[0], "must name the unmeasured denominator"
