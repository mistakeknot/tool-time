#!/usr/bin/env python3
"""Backfill events.jsonl from historical session transcripts.

Parses all Claude Code and Codex CLI transcripts, emits unified events
to ~/.claude/tool-time/events.jsonl. Safe to re-run — deduplicates by
event ID.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from parsers import (
    find_claude_code_sessions,
    find_codex_sessions,
    parse_claude_code,
    parse_codex,
)

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"


def load_existing_ids() -> set[str]:
    """Load event IDs already in events.jsonl to avoid duplicates."""
    if not EVENTS_FILE.exists():
        return set()
    ids = set()
    for line in EVENTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ids.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = load_existing_ids()

    claude_sessions = find_claude_code_sessions()
    codex_sessions = find_codex_sessions()

    print(f"Found {len(claude_sessions)} Claude Code sessions")
    print(f"Found {len(codex_sessions)} Codex CLI sessions")

    new_events = 0
    skipped = 0
    errors = 0
    tools = Counter()
    sources = Counter()

    with open(EVENTS_FILE, "a") as f:
        for path in claude_sessions:
            try:
                for event in parse_claude_code(path):
                    if event["id"] in existing_ids:
                        skipped += 1
                        continue
                    f.write(json.dumps(event) + "\n")
                    new_events += 1
                    tools[event["tool"]] += 1
                    sources["claude-code"] += 1
            except Exception as e:
                errors += 1
                print(f"  Error parsing {path.name}: {e}", file=sys.stderr)

        for path in codex_sessions:
            try:
                for event in parse_codex(path):
                    if event["id"] in existing_ids:
                        skipped += 1
                        continue
                    f.write(json.dumps(event) + "\n")
                    new_events += 1
                    tools[event["tool"]] += 1
                    sources["codex"] += 1
            except Exception as e:
                errors += 1
                print(f"  Error parsing {path.name}: {e}", file=sys.stderr)

    print(f"\nBackfill complete:")
    print(f"  {new_events} new events written")
    print(f"  {skipped} duplicates skipped")
    if errors:
        print(f"  {errors} sessions failed to parse")
    print(f"\nBy source:")
    for source, count in sources.most_common():
        print(f"  {source}: {count}")
    print(f"\nTop 10 tools:")
    for tool, count in tools.most_common(10):
        print(f"  {tool}: {count}")


if __name__ == "__main__":
    main()
