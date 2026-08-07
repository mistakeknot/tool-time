#!/usr/bin/env python3
"""Tests for maintain.py — SessionEnd housekeeping."""

import fcntl
import gzip
import json
import re
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import maintain
from maintain import (
    build_digest_lines,
    gc_seq_files,
    gc_tmp_files,
    has_fresh_events,
    rotate_events,
    write_digest,
)


def _ts_str(days_ago: int = 0, hours_ago: int = 0) -> str:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_line(days_ago: int = 0, seq: int = 1, tool: str = "Read") -> str:
    return json.dumps({
        "v": 1,
        "id": f"sess-{seq}",
        "ts": _ts_str(days_ago=days_ago),
        "event": "PostToolUse",
        "tool": tool,
        "project": "/test/project",
        "error": None,
        "source": "claude-code",
    })


def _write_stats(
    data_dir: Path,
    generated_days_ago: int = 0,
    tools: dict | None = None,
    total_events: int = 200,
) -> None:
    if tools is None:
        tools = {"Read": {"calls": 100, "error_observed_calls": 100, "errors": 1, "rejections": 0}}
    (data_dir / "stats.json").write_text(json.dumps({
        "generated": _ts_str(days_ago=generated_days_ago),
        "total_events": total_events,
        "tools": tools,
    }))


def _write_rig(data_dir: Path, zero_use: list, scanned: int = 5000,
               generated_days_ago: int = 0) -> None:
    (data_dir / "rig.json").write_text(json.dumps({
        "zero_use": zero_use,
        "meta": {
            "generated": _ts_str(days_ago=generated_days_ago),
            "events_scanned": scanned,
        },
    }))


def _archive_files(data_dir: Path) -> list[Path]:
    # Rotation order == creation order; sort by mtime with name tiebreak.
    return sorted(
        data_dir.glob("events-archive-*.jsonl.gz"),
        key=lambda p: (p.stat().st_mtime, p.name),
    )


def _read_archive(data_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in _archive_files(data_dir):
        with gzip.open(path, "rt") as f:
            lines.extend(f.read().splitlines())
    return lines


class TestRotation:
    def test_no_rotation_under_threshold(self, tmp_path):
        # Real 50MB threshold: a small file is left alone even if all-old
        events = tmp_path / "events.jsonl"
        content = _make_line(days_ago=200) + "\n"
        events.write_text(content)
        rotate_events(tmp_path)
        assert events.read_text() == content
        assert _archive_files(tmp_path) == []

    def test_missing_file_is_noop(self, tmp_path):
        rotate_events(tmp_path)
        assert not (tmp_path / "events.jsonl").exists()

    def test_rotation_preserves_total_line_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        old = [_make_line(days_ago=100, seq=i) for i in range(5)]
        recent = [_make_line(days_ago=1, seq=i) for i in range(3)]
        events = tmp_path / "events.jsonl"
        events.write_text("\n".join(old + recent) + "\n")
        rotate_events(tmp_path)
        kept = events.read_text().splitlines()
        archived = _read_archive(tmp_path)
        assert len(kept) + len(archived) == 8
        assert kept == recent
        assert archived == old

    def test_malformed_lines_archived_not_lost(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        good = _make_line(days_ago=1)
        malformed = ["not valid json", '{"missing": "ts field"}']
        events = tmp_path / "events.jsonl"
        events.write_text("\n".join([malformed[0], good, malformed[1]]) + "\n")
        rotate_events(tmp_path)
        assert events.read_text().splitlines() == [good]
        assert _read_archive(tmp_path) == malformed

    def test_each_rotation_writes_own_archive(self, tmp_path, monkeypatch):
        # One self-contained gzip per rotation: a crash appending to a
        # shared archive can't corrupt earlier rotations.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        events = tmp_path / "events.jsonl"
        first = _make_line(days_ago=100, seq=1)
        second = _make_line(days_ago=95, seq=2)
        events.write_text(first + "\n")
        rotate_events(tmp_path)
        events.write_text(second + "\n")
        rotate_events(tmp_path)
        archives = _archive_files(tmp_path)
        assert len(archives) == 2
        # Each file is independently gunzip-readable, whole and alone.
        contents = []
        for path in archives:
            with gzip.open(path, "rt") as f:
                contents.append(f.read().splitlines())
        assert sorted(contents) == sorted([[first], [second]])

    def test_no_rewrite_when_nothing_old(self, tmp_path, monkeypatch):
        # Oversized but all-recent: rotation must NOT rewrite an identical
        # file (that would re-run the append-loss race on every SessionEnd).
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        recent = [_make_line(days_ago=1, seq=i) for i in range(3)]
        events = tmp_path / "events.jsonl"
        events.write_text("\n".join(recent) + "\n")
        inode_before = events.stat().st_ino

        def no_write(*args, **kwargs):
            raise AssertionError("rotation wrote despite nothing being old")

        monkeypatch.setattr(maintain.tempfile, "mkstemp", no_write)
        rotate_events(tmp_path)
        assert events.stat().st_ino == inode_before  # never replaced
        assert events.read_text().splitlines() == recent
        assert _archive_files(tmp_path) == []

    def test_invalid_utf8_rotation(self, tmp_path, monkeypatch):
        # One invalid byte must not disable rotation forever: the mangled
        # line archives as old, valid lines are kept/archived normally.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        good = _make_line(days_ago=1)
        events = tmp_path / "events.jsonl"
        events.write_bytes(b"\xff\xfe not utf-8\n" + (good + "\n").encode())
        rotate_events(tmp_path)  # must not raise UnicodeDecodeError
        assert events.read_text().splitlines() == [good]
        archived = _read_archive(tmp_path)
        assert len(archived) == 1
        assert "not utf-8" in archived[0]

    def test_lock_skip(self, tmp_path, monkeypatch):
        # A concurrent rotation holds .rotate.lock — we skip silently.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        content = _make_line(days_ago=100) + "\n"
        events = tmp_path / "events.jsonl"
        events.write_text(content)
        lock_fd = os.open(tmp_path / ".rotate.lock", os.O_CREAT | os.O_WRONLY)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            rotate_events(tmp_path)
        finally:
            os.close(lock_fd)
        assert events.read_text() == content  # untouched
        assert _archive_files(tmp_path) == []

    def test_rotates_after_lock_released(self, tmp_path, monkeypatch):
        # Sanity: the lock file left behind by a previous rotation does
        # not block the next one.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        events = tmp_path / "events.jsonl"
        events.write_text(_make_line(days_ago=100, seq=1) + "\n")
        rotate_events(tmp_path)
        events.write_text(_make_line(days_ago=95, seq=2) + "\n")
        rotate_events(tmp_path)
        assert len(_archive_files(tmp_path)) == 2

    def test_tmp_unlinked_when_replace_fails(self, tmp_path, monkeypatch):
        # ENOSPC-style failure between mkstemp and os.replace must not
        # strand .events-*.tmp files.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        content = _make_line(days_ago=100) + "\n"
        events = tmp_path / "events.jsonl"
        events.write_text(content)

        def broken_replace(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(maintain.os, "replace", broken_replace)
        with pytest.raises(OSError):
            rotate_events(tmp_path)
        monkeypatch.undo()
        assert list(tmp_path.glob(".events-*.tmp")) == []
        assert events.read_text() == content  # source intact

    def test_fsync_called_before_replace(self, tmp_path, monkeypatch):
        # Power-loss safety: both the archive and the rewritten events
        # file are fsynced before os.replace.
        monkeypatch.setattr(maintain, "ROTATE_SIZE_BYTES", 0)
        events = tmp_path / "events.jsonl"
        events.write_text(_make_line(days_ago=100) + "\n")
        real_fsync = os.fsync
        synced = []

        def spy_fsync(fd):
            synced.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(maintain.os, "fsync", spy_fsync)
        rotate_events(tmp_path)
        assert len(synced) >= 2


class TestHasFreshEvents:
    def test_fresh_event(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line(days_ago=0) + "\n")
        assert has_fresh_events(tmp_path)

    def test_only_old_events(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line(days_ago=3) + "\n")
        assert not has_fresh_events(tmp_path)

    def test_missing_file(self, tmp_path):
        assert not has_fresh_events(tmp_path)

    def test_malformed_only(self, tmp_path):
        (tmp_path / "events.jsonl").write_text("not json\n")
        assert not has_fresh_events(tmp_path)


class TestDigestPipelineCheck:
    def test_fresh_events_missing_stats(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "pipeline may be broken" in lines[0]

    def test_fresh_events_stale_generated(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, generated_days_ago=8)
        lines = build_digest_lines(tmp_path)
        assert any("pipeline may be broken" in l for l in lines)

    def test_fresh_events_empty_tools_high_volume(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={}, total_events=150)
        lines = build_digest_lines(tmp_path)
        assert any("pipeline may be broken" in l for l in lines)

    def test_empty_tools_low_volume_not_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={}, total_events=50)
        assert build_digest_lines(tmp_path) == []

    def test_no_fresh_events_missing_stats_silent(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line(days_ago=3) + "\n")
        assert build_digest_lines(tmp_path) == []


class TestDigestErrorRate:
    def test_high_error_rate_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 40, "error_observed_calls": 40, "errors": 10, "rejections": 0},
        })
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "Edit" in lines[0]
        assert "10/40" in lines[0]
        assert "/tool-time" in lines[0]

    def test_worst_offender_named(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 40, "error_observed_calls": 40, "errors": 8, "rejections": 0},   # 20%
            "Bash": {"calls": 30, "error_observed_calls": 30, "errors": 15, "rejections": 0},  # 50%
        })
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "Bash" in lines[0]

    def test_below_min_calls_not_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 10, "error_observed_calls": 10, "errors": 9, "rejections": 0},
        })
        assert build_digest_lines(tmp_path) == []

    def test_below_rate_threshold_not_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 100, "error_observed_calls": 100, "errors": 5, "rejections": 0},
        })
        assert build_digest_lines(tmp_path) == []


class TestDigestMeasurementDown:
    """The hardest failure to see: a dead error pipeline and a clean run
    produce identical silence, because every tripwire correctly skips a
    tool whose `errors` is None."""

    def test_zero_observable_at_volume_is_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 300, "error_observed_calls": 0, "errors": None, "rejections": None},
            "Read": {"calls": 200, "error_observed_calls": 0, "errors": None, "rejections": None},
        })
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "error measurement is down" in lines[0]
        assert "500" in lines[0], "must name the unmeasured denominator"

    def test_silent_below_volume_floor(self, tmp_path):
        """An idle machine is not a broken pipeline."""
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 5, "error_observed_calls": 0, "errors": None, "rejections": None},
        })
        assert build_digest_lines(tmp_path) == []

    def test_silent_when_any_measurement_exists(self, tmp_path):
        """Partial coverage is backfill lagging, not backfill dead."""
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 300, "error_observed_calls": 0, "errors": None, "rejections": None},
            "Read": {"calls": 200, "error_observed_calls": 4, "errors": 0, "rejections": 0},
        })
        assert build_digest_lines(tmp_path) == []

    def test_silent_when_errors_are_measured_and_zero(self, tmp_path):
        """A genuinely clean run must stay quiet."""
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 300, "error_observed_calls": 300, "errors": 0, "rejections": 0},
        })
        assert build_digest_lines(tmp_path) == []

    def test_does_not_mask_a_real_error_rate(self, tmp_path):
        """When measurement works, the worst-offender line still wins its slot."""
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 300, "error_observed_calls": 40, "errors": 10, "rejections": 0},
        })
        lines = build_digest_lines(tmp_path)
        assert any("Edit" in ln and "10/40" in ln for ln in lines)
        assert not any("measurement is down" in ln for ln in lines)


class TestDigestRateAlwaysNamesDenominator:
    """A percentage without its denominator is unactionable and, worse,
    unfalsifiable — '51% error rate' read plausible for months while being
    computed over a population that could not report errors at all."""

    RATE = re.compile(r"\d+%")
    # "10/40 observed calls" or "of 1,234 calls"
    DENOM = re.compile(r"\d[\d,]*/\d[\d,]*|of [\d,]+ calls")

    def _assert_ok(self, lines):
        for ln in lines:
            if self.RATE.search(ln):
                assert self.DENOM.search(ln), f"rate without denominator: {ln!r}"

    def test_tool_error_rate_line(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 40, "error_observed_calls": 40, "errors": 10, "rejections": 0},
        })
        lines = build_digest_lines(tmp_path)
        assert lines
        self._assert_ok(lines)

    def test_edit_diagnostic_line(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={})
        (tmp_path / "edit_stats.json").write_text(json.dumps({
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totals": {"resolved_calls": 1000, "failed": 80, "failure_rate": 0.08},
            "failure_kinds": [{"kind": "not_read_first", "count": 50,
                               "share_of_failures": 0.62, "class": "workflow"}],
        }))
        lines = build_digest_lines(tmp_path)
        assert lines
        self._assert_ok(lines)

    def test_measurement_down_line(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, tools={
            "Edit": {"calls": 300, "error_observed_calls": 0, "errors": None, "rejections": None},
        })
        lines = build_digest_lines(tmp_path)
        assert lines
        self._assert_ok(lines)


class TestDigestRig:
    def test_zero_use_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        _write_rig(tmp_path, ["alpha", "beta", "gamma"])
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "3 MCP servers" in lines[0]
        assert "/tool-time rig" in lines[0]

    def test_below_zero_use_min_not_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        _write_rig(tmp_path, ["alpha", "beta"])
        assert build_digest_lines(tmp_path) == []

    def test_missing_rig_json_silent(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        assert build_digest_lines(tmp_path) == []

    def test_fresh_install_low_scan_not_flagged(self, tmp_path):
        # Fresh install: rig.py scanned ~0 events, so every registered
        # server lands in zero_use — the banner must stay silent.
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        _write_rig(tmp_path, ["alpha", "beta", "gamma"], scanned=12)
        assert build_digest_lines(tmp_path) == []

    def test_scan_at_threshold_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        _write_rig(tmp_path, ["alpha", "beta", "gamma"], scanned=1000)
        lines = build_digest_lines(tmp_path)
        assert len(lines) == 1
        assert "3 MCP servers" in lines[0]

    def test_stale_rig_not_flagged(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        _write_rig(tmp_path, ["alpha", "beta", "gamma"], generated_days_ago=40)
        assert build_digest_lines(tmp_path) == []

    def test_missing_meta_not_flagged(self, tmp_path):
        # Old-format rig.json without meta can't prove real volume — silent.
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        (tmp_path / "rig.json").write_text(json.dumps({
            "zero_use": ["alpha", "beta", "gamma"],
        }))
        assert build_digest_lines(tmp_path) == []


class TestWriteDigest:
    def test_empty_digest_when_healthy(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path)
        write_digest(tmp_path)
        digest = tmp_path / "digest.txt"
        assert digest.exists()
        assert digest.read_text() == ""

    def test_max_two_lines_priority_order(self, tmp_path):
        # a (stale generated), b (error rate), and c (rig) all fire;
        # only a and b survive the cap
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        _write_stats(tmp_path, generated_days_ago=8, tools={
            "Edit": {"calls": 40, "error_observed_calls": 40, "errors": 10, "rejections": 0},
        })
        _write_rig(tmp_path, ["alpha", "beta", "gamma"])
        write_digest(tmp_path)
        lines = (tmp_path / "digest.txt").read_text().splitlines()
        assert len(lines) == 2
        assert "pipeline may be broken" in lines[0]
        assert "Edit" in lines[1]
        assert not any("rig" in l for l in lines)

    def test_lines_prefixed(self, tmp_path):
        (tmp_path / "events.jsonl").write_text(_make_line() + "\n")
        write_digest(tmp_path)
        lines = (tmp_path / "digest.txt").read_text().splitlines()
        assert lines
        assert all(l.startswith("tool-time: ") for l in lines)

    def test_overwrites_previous_digest(self, tmp_path):
        (tmp_path / "digest.txt").write_text("tool-time: old news\n")
        write_digest(tmp_path)
        assert (tmp_path / "digest.txt").read_text() == ""


class TestSeqGc:
    def test_deletes_old_keeps_recent(self, tmp_path):
        old = tmp_path / ".seq-old-session"
        old.write_text("5")
        stale = time.time() - 8 * 86400
        os.utime(old, (stale, stale))
        recent = tmp_path / ".seq-new-session"
        recent.write_text("3")
        gc_seq_files(tmp_path)
        assert not old.exists()
        assert recent.exists()

    def test_empty_dir_is_noop(self, tmp_path):
        gc_seq_files(tmp_path)


class TestTmpGc:
    def test_sweeps_stale_rotation_tmps(self, tmp_path):
        # Both rotation temp prefixes are covered by the .events-*.tmp glob.
        stale = time.time() - 2 * 86400
        old_events = tmp_path / ".events-abc123.tmp"
        old_events.write_text("stranded")
        old_archive = tmp_path / ".events-archive-def456.tmp"
        old_archive.write_text("stranded")
        os.utime(old_events, (stale, stale))
        os.utime(old_archive, (stale, stale))
        fresh = tmp_path / ".events-live.tmp"
        fresh.write_text("in flight")
        gc_tmp_files(tmp_path)
        assert not old_events.exists()
        assert not old_archive.exists()
        assert fresh.exists()  # could belong to a rotation in progress

    def test_leaves_real_files_alone(self, tmp_path):
        events = tmp_path / "events.jsonl"
        events.write_text(_make_line() + "\n")
        stale = time.time() - 2 * 86400
        os.utime(events, (stale, stale))
        gc_tmp_files(tmp_path)
        assert events.exists()

    def test_empty_dir_is_noop(self, tmp_path):
        gc_tmp_files(tmp_path)


class TestSafety:
    def test_main_with_missing_data_dir(self, tmp_path, monkeypatch):
        data_dir = tmp_path / ".claude" / "tool-time"
        monkeypatch.setattr(maintain, "DATA_DIR", data_dir)
        maintain.main()  # must not raise
        assert (data_dir / "digest.txt").exists()
        assert (data_dir / "digest.txt").read_text() == ""

    def test_main_with_all_inputs_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maintain, "DATA_DIR", tmp_path)
        maintain.main()  # must not raise
        assert (tmp_path / "digest.txt").read_text() == ""

    def test_main_sweeps_stale_rotation_tmps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(maintain, "DATA_DIR", tmp_path)
        stranded = tmp_path / ".events-stranded.tmp"
        stranded.write_text("orphan")
        stale = time.time() - 2 * 86400
        os.utime(stranded, (stale, stale))
        maintain.main()
        assert not stranded.exists()

    def test_script_exits_zero_with_garbage_inputs(self, tmp_path):
        data_dir = tmp_path / ".claude" / "tool-time"
        data_dir.mkdir(parents=True)
        (data_dir / "events.jsonl").write_text("garbage\n")
        (data_dir / "stats.json").write_text("{{{not json")
        (data_dir / "rig.json").write_text("[truncated")
        env = {**os.environ, "HOME": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(Path(maintain.__file__).resolve())],
            env=env,
            capture_output=True,
        )
        assert result.returncode == 0

    def test_script_exits_zero_with_no_home_data(self, tmp_path):
        env = {**os.environ, "HOME": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(Path(maintain.__file__).resolve())],
            env=env,
            capture_output=True,
        )
        assert result.returncode == 0
