#!/usr/bin/env python3
"""tool-time maintenance.

Runs on SessionEnd (after summarize.py has refreshed stats.json) to do
housekeeping: rotate an oversized events.jsonl into a gzip archive, write
a small digest.txt of surfacing signals for SessionStart, and GC stale
.seq-* session counters.

Unlike summarize.py (pure data preparation), this script owns a few
minimal surfacing thresholds — they mirror the flagging thresholds in
skills/tool-time/SKILL.md and exist only to decide whether SessionStart
says anything at all. The /tool-time skill does the real analysis.

Contract: never crashes the SessionEnd hook — every step is guarded and
the script always exits 0.
"""

import gzip
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

try:
    import fcntl
except ImportError:  # non-POSIX — rotation runs unlocked rather than crashing
    fcntl = None  # type: ignore[assignment]

DATA_DIR = Path.home() / ".claude" / "tool-time"

# Rotation: keep events.jsonl bounded; archive old lines rather than delete
ROTATE_SIZE_BYTES = 50 * 1024 * 1024
ARCHIVE_AGE_DAYS = 90

# Digest tripwires (surfacing heuristics only — see module docstring)
FRESH_EVENTS_HOURS = 24
STALE_STATS_DAYS = 7
EMPTY_TOOLS_MIN_EVENTS = 100  # empty tools map only alarming with real volume
ERROR_RATE_THRESHOLD = 0.15   # SKILL.md flags ~10%; the digest stays quieter
MIN_CALLS = 20                # ignore small samples
# Edit-diagnostic tripwire. Measured baseline over 6,883 transcripts / 30d is
# ~2.3% overall, so 5% is a genuine regression rather than a restatement of
# normal. Kept separate from ERROR_RATE_THRESHOLD, which reads a different
# source with a different denominator.
EDIT_FAILURE_THRESHOLD = 0.05
EDIT_MIN_CALLS = 200
EDIT_STATS_MAX_AGE_DAYS = 3
# Error measurement is supplied solely by backfill.py's transcript events.
# If that stops, every `errors` reverts to None and each tripwire below skips
# its tool as unmeasured — silently, and indistinguishably from healthy. This
# floor is the volume above which "zero error-observable calls" means the
# pipeline died rather than the machine being idle.
UNMEASURED_MIN_CALLS = 200
ZERO_USE_MIN = 3              # rig.json zero_use entries before surfacing
RIG_MIN_EVENTS_SCANNED = 1000  # zero_use meaningless on a fresh install
RIG_MAX_AGE_DAYS = 30          # a stale rig.json shouldn't nag forever
MAX_DIGEST_LINES = 2
DIGEST_PREFIX = "tool-time: "
DIGEST_TAIL_BYTES = 65536     # tail window scanned for event freshness

SEQ_GC_DAYS = 7
TMP_GC_DAYS = 1               # orphaned rotation temp files


def _parse_iso(ts_raw: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware UTC datetime, or None."""
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _line_ts(line: str) -> datetime | None:
    """Extract the event timestamp from a JSONL line, or None if malformed."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict):
        return None
    return _parse_iso(ev.get("ts"))


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON object from disk; None if missing, malformed, or not a dict."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(
    data_dir: Path, prefix: str, dest: Path, write_body: Callable[[BinaryIO], None]
) -> None:
    """mkstemp in data_dir, let write_body fill the binary file, fsync,
    then os.replace onto dest. The temp file is unlinked on any failure
    so an exception (e.g. ENOSPC) can't strand .events-*.tmp litter."""
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            write_body(f)
            f.flush()
            os.fsync(f.fileno())  # survive power loss across the replace
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def rotate_events(data_dir: Path) -> None:
    """Rotate events.jsonl once it exceeds ROTATE_SIZE_BYTES.

    Lines older than ARCHIVE_AGE_DAYS go to a fresh, self-contained
    events-archive-<UTC stamp>.jsonl.gz (written via temp file +
    os.replace, so a crash never corrupts an existing archive and every
    archive stays independently readable); the remaining lines are
    rewritten atomically the same way. Malformed lines — including any
    produced by invalid UTF-8 bytes, which are decoded with
    errors='replace' — count as old: they are archived, never dropped.

    If nothing is old yet, we return WITHOUT rewriting: the file simply
    stays oversized until lines age past ARCHIVE_AGE_DAYS. That is
    deliberate — rewriting an identical file would re-run the loss race
    below on every SessionEnd for no benefit.

    Concurrency: a non-blocking flock on .rotate.lock ensures only one
    session rotates at a time (a second concurrent SessionEnd skips
    silently). The lock does NOT cover appenders, though: hook.sh keeps
    appending to events.jsonl while we read-filter-replace, so any line
    appended between our read and our os.replace is lost. On a >50 MB
    file that window is real — hundreds of milliseconds — and it recurs
    on every rotation that archives lines. We accept that bounded loss
    rather than serialize every append through a lock.
    """
    events_file = data_dir / "events.jsonl"
    if not events_file.exists():
        return
    if events_file.stat().st_size <= ROTATE_SIZE_BYTES:
        return

    if fcntl is None:
        _rotate_locked(data_dir, events_file)
        return
    lock_fd = os.open(data_dir / ".rotate.lock", os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # another session is rotating right now — skip silently
        _rotate_locked(data_dir, events_file)
    finally:
        os.close(lock_fd)  # releases the flock


def _rotate_locked(data_dir: Path, events_file: Path) -> None:
    """The read-filter-replace body of rotate_events (lock held)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AGE_DAYS)
    keep: list[str] = []
    old: list[str] = []
    # errors='replace': one invalid byte must not disable rotation forever;
    # the mangled line fails JSON parsing and is archived as old.
    for line in events_file.read_text(errors="replace").splitlines():
        ts = _line_ts(line)
        if ts is None or ts < cutoff:
            old.append(line)
        else:
            keep.append(line)

    if not old:
        return  # nothing to archive — leave the file untouched (see docstring)

    # Archive first: if we crash before the events replace, events.jsonl is
    # intact (worst case a re-run re-archives the same old lines into a new
    # archive file).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_file = data_dir / f"events-archive-{stamp}.jsonl.gz"
    n = 1
    while archive_file.exists():
        archive_file = data_dir / f"events-archive-{stamp}-{n}.jsonl.gz"
        n += 1

    def write_archive(f: BinaryIO) -> None:
        with gzip.open(f, "wt") as gz:
            gz.write("\n".join(old) + "\n")

    def write_keep(f: BinaryIO) -> None:
        if keep:
            f.write(("\n".join(keep) + "\n").encode())

    _write_atomic(data_dir, ".events-archive-", archive_file, write_archive)
    _write_atomic(data_dir, ".events-", events_file, write_keep)


def has_fresh_events(data_dir: Path, hours: int = FRESH_EVENTS_HOURS) -> bool:
    """True if events.jsonl has any event newer than `hours`.

    Only scans the tail of the file (events are append-ordered), so the
    check stays cheap even on a large log.
    """
    events_file = data_dir / "events.jsonl"
    if not events_file.exists():
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with open(events_file, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - DIGEST_TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace")
    for line in tail.splitlines():
        ts = _line_ts(line)
        if ts is not None and ts >= cutoff:
            return True
    return False


def build_digest_lines(data_dir: Path) -> list[str]:
    """Surfacing signals for SessionStart, priority-ordered, capped at
    MAX_DIGEST_LINES. Returns unprefixed lines; empty list means healthy."""
    lines: list[str] = []
    now = datetime.now(timezone.utc)
    stats = _load_json(data_dir / "stats.json")

    # (a) Pipeline self-check: fresh events but stale/empty stats means
    # summarize.py isn't keeping up — the hook chain may be broken.
    if has_fresh_events(data_dir):
        stale = stats is None
        if stats is not None:
            generated = _parse_iso(stats.get("generated"))
            total = stats.get("total_events")
            tools = stats.get("tools")
            if generated is None or generated < now - timedelta(days=STALE_STATS_DAYS):
                stale = True
            elif (
                isinstance(tools, dict)
                and not tools
                and isinstance(total, int)
                and total > EMPTY_TOOLS_MIN_EVENTS
            ):
                stale = True
        if stale:
            lines.append(
                "stats are stale/empty while events are fresh — pipeline may "
                "be broken; run /tool-time to investigate"
            )

    # (b0) Measurement self-check: are we able to see errors at all?
    #
    # Every tripwire below skips a tool whose `errors` is None, which is
    # correct — unmeasured is not zero. But applied to ALL tools it becomes
    # indistinguishable from a clean run: a dead backfill produces exactly
    # the same silence as a session with no failures. This fires on that
    # silence, so the absence of a signal is itself reported.
    if stats is not None:
        tools = stats.get("tools")
        if isinstance(tools, dict) and tools:
            calls = 0
            observed = 0
            for t in tools.values():
                if not isinstance(t, dict):
                    continue
                c = t.get("calls")
                o = t.get("error_observed_calls")
                if isinstance(c, int):
                    calls += c
                if isinstance(o, int):
                    observed += o
            if calls >= UNMEASURED_MIN_CALLS and observed == 0:
                lines.append(
                    f"error measurement is down — 0 of {calls:,} calls in 7d were "
                    "error-observable; backfill.py has not run (see CLAUDE.md)"
                )

    # (b) Error-rate tripwire: name the worst offender with numbers.
    if stats is not None:
        tools = stats.get("tools")
        worst: tuple[float, str, int, int] | None = None
        if isinstance(tools, dict):
            for name, t in tools.items():
                if not isinstance(t, dict):
                    continue
                # The denominator is error-observable calls, never total
                # calls: events from a source that cannot witness failures
                # belong in neither numerator nor denominator. `errors` is
                # None precisely when nothing was observable, so an
                # unmeasured tool is skipped rather than reported as clean.
                observed = t.get("error_observed_calls")
                errors = t.get("errors")
                if not isinstance(observed, int) or not isinstance(errors, int):
                    continue
                if observed < MIN_CALLS or errors / observed < ERROR_RATE_THRESHOLD:
                    continue
                rate = errors / observed
                if worst is None or rate > worst[0]:
                    worst = (rate, name, errors, observed)
        if worst is not None:
            rate, name, errors, observed = worst
            lines.append(
                f"{name} error rate is {round(rate * 100)}% ({errors}/{observed} "
                "observed calls in 7d) — run /tool-time to investigate"
            )

    # (b2) Edit-diagnostic tripwire, from edit_stats.json (transcript-derived,
    # the only source that can observe a tool failure). Names the dominant
    # failure cause rather than a bare rate: "52% not_read_first" tells you
    # what to change, "2.3% error rate" does not.
    edit_stats = _load_json(data_dir / "edit_stats.json")
    if edit_stats is not None:
        generated = _parse_iso(edit_stats.get("generated"))
        totals = edit_stats.get("totals")
        kinds = edit_stats.get("failure_kinds")
        if (
            isinstance(totals, dict)
            and isinstance(kinds, list)
            and kinds
            and generated is not None
            and generated >= now - timedelta(days=EDIT_STATS_MAX_AGE_DAYS)
        ):
            resolved = totals.get("resolved_calls")
            failure_rate = totals.get("failure_rate")
            # failure_rate is None when nothing resolved — not a clean run.
            if (
                isinstance(resolved, int)
                and resolved >= EDIT_MIN_CALLS
                and isinstance(failure_rate, (int, float))
                and failure_rate >= EDIT_FAILURE_THRESHOLD
            ):
                top = kinds[0]
                if isinstance(top, dict) and top.get("kind"):
                    share = top.get("share_of_failures")
                    share_txt = f"{round(share * 100)}%" if isinstance(share, (int, float)) else "most"
                    lines.append(
                        f"edit failures {round(failure_rate * 100)}% of {resolved:,} calls in 7d — "
                        f"{share_txt} are {top['kind']} — run edit_stats.py"
                    )

    # (c) Rig tripwire: rig.json is written by rig.py and may not exist.
    # Gated on volume + freshness: a fresh install scans ~0 events, which
    # puts every registered server in zero_use — without the gate the
    # banner would fire at every session start indefinitely.
    rig = _load_json(data_dir / "rig.json")
    if rig is not None:
        meta = rig.get("meta")
        scanned = meta.get("events_scanned") if isinstance(meta, dict) else None
        rig_generated = _parse_iso(meta.get("generated")) if isinstance(meta, dict) else None
        if (
            isinstance(scanned, int)
            and scanned >= RIG_MIN_EVENTS_SCANNED
            and rig_generated is not None
            and rig_generated >= now - timedelta(days=RIG_MAX_AGE_DAYS)
        ):
            zero_use = rig.get("zero_use")
            if isinstance(zero_use, list) and len(zero_use) >= ZERO_USE_MIN:
                lines.append(
                    f"{len(zero_use)} MCP servers registered but unused in 30d "
                    "— run /tool-time rig"
                )

    return lines[:MAX_DIGEST_LINES]


def write_digest(data_dir: Path) -> None:
    """Write digest.txt (overwrite; empty when healthy so SessionStart
    stays silent)."""
    lines = build_digest_lines(data_dir)
    content = "".join(DIGEST_PREFIX + line + "\n" for line in lines)
    (data_dir / "digest.txt").write_text(content)


def gc_seq_files(data_dir: Path) -> None:
    """Delete .seq-* session counters older than SEQ_GC_DAYS.

    hook.sh also cleans these on SessionEnd; double coverage is fine and
    this catches non-hook invocations.
    """
    cutoff = time.time() - SEQ_GC_DAYS * 86400
    for path in data_dir.glob(".seq-*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def gc_tmp_files(data_dir: Path) -> None:
    """Delete orphaned .events-*.tmp rotation files older than TMP_GC_DAYS.

    rotate_events unlinks its temp file on failure, but a hard kill (or
    power loss) between mkstemp and os.replace can still strand one.
    """
    cutoff = time.time() - TMP_GC_DAYS * 86400
    for path in data_dir.glob(".events-*.tmp"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def main() -> None:
    # Each step is guarded independently so one failure doesn't skip the
    # rest — this script must never crash the SessionEnd hook.
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        rotate_events(DATA_DIR)
    except Exception:
        pass
    try:
        write_digest(DATA_DIR)
    except Exception:
        pass
    try:
        gc_seq_files(DATA_DIR)
    except Exception:
        pass
    try:
        gc_tmp_files(DATA_DIR)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
