#!/usr/bin/env python3
"""Quarantine unreliable historical `error` values in events.jsonl.

Background
----------
Until 2026-08-07, hooks/hook.sh decided whether a tool call had failed by
stringifying the entire PostToolUse `tool_response` and testing it against
/error|Error|ERROR/. That matched tool *payloads*, not tool *failures*: a
successful Edit's response embeds `originalFile` (the whole pre-edit file), so
any source file containing the substring "error" was recorded as a failure.

Two facts make every hook-written `error` value unusable:

  1. Claude Code does not fire PostToolUse when a tool fails. Verified by
     deliberately failing an Edit — the preceding Read logged an event, the
     failed Edit logged nothing. A hook that only fires on success cannot
     witness a failure.
  2. Measured over 209,982 historical events: 20,517 non-null `error` values,
     of which 0 contained `<tool_use_error>`. Every one was a misfiled success.

This script renames `error` to `error_legacy_unreliable` on hook-derived
events so the values survive for forensics but no consumer reads them as
error signal. Transcript-derived events (`event: "ToolUse"`, written by
backfill.py) carry real `is_error` truth and are left untouched.

Safe to re-run: events already quarantined are skipped.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "tool-time"
EVENTS_FILE = DATA_DIR / "events.jsonl"
LEGACY_KEY = "error_legacy_unreliable"

# Only hook-derived events are quarantined. Transcript-derived events
# ("ToolUse") carry an explicit is_error flag and are ground truth.
HOOK_EVENT_TYPE = "PostToolUse"


def quarantine_line(record: dict) -> tuple[dict, bool]:
    """Return (record, changed). Renames `error` on hook-derived events only."""
    if record.get("event") != HOOK_EVENT_TYPE:
        return record, False
    if LEGACY_KEY in record:
        return record, False  # already quarantined
    if "error" not in record:
        return record, False
    value = record.pop("error")
    if value is None:
        # A null error asserts nothing; drop it rather than preserve a
        # meaningless legacy key.
        record["error"] = None
        return record, False
    record[LEGACY_KEY] = value
    record["error"] = None
    return record, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events-file", type=Path, default=EVENTS_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")
    parser.add_argument("--no-backup", action="store_true", help="Skip the .bak copy (not recommended).")
    args = parser.parse_args()

    path: Path = args.events_file
    if not path.exists():
        print(f"No events file at {path} — nothing to do.")
        return 0

    total = changed = malformed = 0
    tmp_path = None

    if args.dry_run:
        for line in path.open(errors="replace"):
            if not line.strip():
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            _, did = quarantine_line(record)
            changed += did
    else:
        if not args.no_backup:
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            print(f"Backup written: {backup}")
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".events-quarantine-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as out, path.open(errors="replace") as src:
                for line in src:
                    if not line.strip():
                        continue
                    total += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        # Preserve malformed lines verbatim rather than
                        # silently dropping data we cannot interpret.
                        malformed += 1
                        out.write(line if line.endswith("\n") else line + "\n")
                        continue
                    record, did = quarantine_line(record)
                    changed += did
                    out.write(json.dumps(record, separators=(",", ":")) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    verb = "would quarantine" if args.dry_run else "quarantined"
    print(f"Scanned {total:,} events; {verb} {changed:,} unreliable error values.")
    if malformed:
        print(f"  {malformed:,} malformed lines preserved verbatim.")
    if changed == 0 and not args.dry_run:
        print("  Nothing to do — history already clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
