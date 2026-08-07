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
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    existing_ids = load_existing_ids()

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
