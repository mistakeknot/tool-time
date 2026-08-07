#!/usr/bin/env python3
"""Backfill events.jsonl from historical session transcripts.

Parses Claude Code, Codex CLI, and OpenClaw transcripts and appends unified
events to ~/.claude/tool-time/events.jsonl. Safe to re-run — deduplicates by
event ID.

This is the ONLY path that puts error-observable events (`event: "ToolUse"`)
into events.jsonl. Transcripts carry an explicit `is_error` flag per
tool_result; the PostToolUse hook never fires on failure and so can never
supply one. Without this running, every error count in stats.json is None.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-POSIX
    fcntl = None  # type: ignore[assignment]

from parsers import (
    find_claude_code_sessions,
    find_codex_sessions,
    find_openclaw_sessions,
    parse_claude_code,
    parse_codex,
    parse_openclaw,
)

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"
LOCK_FILE = DATA_DIR / ".rotate.lock"
LOCK_TIMEOUT_SECS = 30


class RotationBusy(Exception):
    """maintain.py is rewriting events.jsonl; writing now would be lost."""


def acquire_rotate_lock(timeout: float | None = None) -> int | None:
    """Take maintain.py's rotation lock for the duration of our appends.

    rotate_events() rewrites events.jsonl read-filter-replace, so anything
    appended between its read and its os.replace is lost. That was an
    accepted, bounded risk while hook.sh was the only appender — one line,
    microseconds. This module appends tens of thousands of lines over
    seconds, which overlaps essentially the whole rewrite.

    rotate_events takes this same lock LOCK_EX|LOCK_NB and skips silently
    when it cannot get it, so holding it here costs at most one deferred
    rotation. We block rather than skip, because rotation is short.

    Returns the fd to close (releasing the lock), or None if locking is
    unavailable on this platform. Raises RotationBusy on timeout: backfill
    is idempotent and re-runs within hours, so writing anyway — into a file
    being rewritten — is strictly worse than not writing.
    """
    # Read the module constants at call time, not as parameter defaults:
    # a default binds at def time, so overriding LOCK_TIMEOUT_SECS (in a
    # test, or at runtime) would silently have no effect.
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECS
    if fcntl is None:
        return None
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY, 0o644)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise RotationBusy(
                    f"could not acquire {LOCK_FILE} within {timeout}s"
                )
            time.sleep(0.25)


def load_existing_ids() -> set[str]:
    """Load event IDs already in events.jsonl to avoid duplicates.

    Only ids this module could have written are worth holding: hook ids live
    in a different namespace (`<uuid>-<int>` vs this module's `<uuid>-t<int>`)
    and can never collide, so keeping them would just inflate the set.
    """
    if not EVENTS_FILE.exists():
        return set()
    ids = set()
    for line in EVENTS_FILE.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "ToolUse":
            continue
        eid = event.get("id")
        if isinstance(eid, str):
            ids.add(eid)
    return ids


def recent(paths: list[Path], days: int | None) -> list[Path]:
    """Filter transcripts to those modified within the window."""
    if not days:
        return paths
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    kept = []
    for p in paths:
        try:
            if p.stat().st_mtime >= cutoff:
                kept.append(p)
        except OSError:
            continue
    return kept


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--days", type=int, default=None,
        help="Only parse transcripts modified in the last N days "
             "(default: all history).",
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress the report.")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sources = [
        ("claude-code", recent(find_claude_code_sessions(), args.days), parse_claude_code),
        ("codex", recent(find_codex_sessions(), args.days), parse_codex),
        ("openclaw", recent(find_openclaw_sessions(), args.days), parse_openclaw),
    ]

    def say(*a):
        if not args.quiet:
            print(*a)

    for name, paths, _ in sources:
        say(f"Found {len(paths)} {name} sessions")

    new_events = 0
    skipped = 0
    failures = 0
    tools: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    with_error = 0

    # The lock must cover load_existing_ids() as well as the appends. A
    # rotation landing between the two would archive ids we still hold,
    # and we would skip those events as duplicates against a file that no
    # longer contains them.
    try:
        lock_fd = acquire_rotate_lock()
    except RotationBusy as e:
        print(f"backfill skipped: {e}", file=sys.stderr)
        return
    existing_ids = load_existing_ids()

    # O_APPEND + one write() per line. hooks/hook.sh appends to this same file
    # concurrently; a buffered multi-line flush can interleave with its writes
    # and tear a line. Each line here is well under PIPE_BUF, and O_APPEND
    # makes the offset update atomic, so writes cannot overlap.
    fd = os.open(EVENTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        for name, paths, parse in sources:
            for path in paths:
                try:
                    for event in parse(path):
                        eid = str(event.get("id") or "")
                        if eid and eid in existing_ids:
                            skipped += 1
                            continue
                        existing_ids.add(eid)
                        os.write(fd, (json.dumps(event) + "\n").encode())
                        new_events += 1
                        tools[event.get("tool", "")] += 1
                        by_source[name] += 1
                        if event.get("error") is not None:
                            with_error += 1
                except Exception as e:  # one bad transcript must not stop the rest
                    failures += 1
                    print(f"  Error parsing {path.name}: {e}", file=sys.stderr)
    finally:
        os.close(fd)
        if lock_fd is not None:
            os.close(lock_fd)  # releases the flock

    say("\nBackfill complete:")
    say(f"  {new_events} new events written")
    say(f"  {skipped} duplicates skipped")
    say(f"  {with_error} carried a tool error")
    if failures:
        say(f"  {failures} sessions failed to parse")
    if by_source:
        say("\nBy source:")
        for source, count in by_source.most_common():
            say(f"  {source}: {count}")
    if tools:
        say("\nTop 10 tools:")
        for tool, count in tools.most_common(10):
            say(f"  {tool}: {count}")


if __name__ == "__main__":
    main()
