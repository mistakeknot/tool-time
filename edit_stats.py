#!/usr/bin/env python3
"""Per-call diagnostic for file-editing tools, from session transcripts.

Why this exists
---------------
tool-time's hook path can count calls but cannot count failures: Claude Code
does not fire PostToolUse when a tool fails, so a hook only ever witnesses
successes. Session transcripts do carry ground truth — every `tool_result`
block has an explicit `is_error` flag — so error analysis reads transcripts
directly rather than events.jsonl.

What it answers
---------------
`summarize.py` reports *that* edits fail. This reports *why*, by pairing each
Edit/Write/MultiEdit call with its result and classifying the failure, then
testing the one hypothesis that is actionable from the model side: that edits
fail because `old_string` is padded with unchanged context.

"Inflation" is that measure — the ratio of bytes the model sent to bytes that
actually differ between old and new. An edit that rewrites one token inside a
40-line quoted block has an inflation ratio around 40; each extra byte is
another chance to mismatch whitespace the model reconstructed from memory.

Inflation is only tested against *string-matching* failures
(`old_string_not_found`, `not_unique`). Workflow failures — editing a file
that was never read, or whose content moved since it was read — are caused by
sequencing, not by string size, and pooling them would dilute the signal.

Usage
-----
    python3 edit_stats.py                     # last 30 days, human report
    python3 edit_stats.py --days 7 --json
    python3 edit_stats.py --tool Edit --ext .py
    python3 edit_stats.py --failed-only --top 30
    python3 edit_stats.py --auto-since-path ~/.claude/CLAUDE.md
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

PROJECTS_DIR = Path.home() / ".claude" / "projects"
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
DEFAULT_DAYS = 30
DEFAULT_TOP = 15

# Failure taxonomy, derived from the observed corpus rather than assumed.
# Order matters: the first matching pattern wins, so more specific phrasings
# are listed before the generic ones they would otherwise fall into.
ERROR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("not_read_first", re.compile(r"has not been read yet|read it first|must read|read the file first", re.I)),
    ("stale_read", re.compile(r"modified since read|has been modified since", re.I)),
    ("worktree_isolation", re.compile(r"isolated (its changes|in the worktree)|call enterworktree|hasn't isolated", re.I)),
    ("symlink_refused", re.compile(r"refusing to write through symlink", re.I)),
    ("identical_strings", re.compile(r"(old_string and new_string are exactly the same|no changes to make)", re.I)),
    ("not_unique", re.compile(r"found \d+ matches|not unique|appears \d+ times|multiple occurrences|replace_all", re.I)),
    ("old_string_not_found", re.compile(r"string to replace not found|could not find the exact text|not found in file", re.I)),
    ("file_not_found", re.compile(r"file does not exist|file not found|no such file", re.I)),
    ("user_rejected", re.compile(r"the user doesn't want|user denied|user rejected|user cancelled|requested changes", re.I)),
    ("validation_error", re.compile(r"inputvalidationerror|required parameter|invalid (argument|parameter)", re.I)),
    ("model_unavailable", re.compile(r"temporarily unavailable", re.I)),
]

# Failure kinds whose cause is plausibly the size/shape of the match string.
STRING_MATCH_FAILURES = {"old_string_not_found", "not_unique"}
# Failure kinds caused by call sequencing, not by string content.
WORKFLOW_FAILURES = {"not_read_first", "stale_read", "worktree_isolation"}

INFLATION_BUCKETS: list[tuple[str, float, float]] = [
    ("<2x", 0.0, 2.0),
    ("2-4x", 2.0, 4.0),
    ("4-10x", 4.0, 10.0),
    ("10-25x", 10.0, 25.0),
    ("25x+", 25.0, float("inf")),
]


# --- Classification ---

def classify_error(text: str) -> str:
    """Map an error message to a failure kind."""
    for kind, pattern in ERROR_PATTERNS:
        if pattern.search(text):
            return kind
    return "other"


def core_change_bytes(old: str, new: str) -> int:
    """Bytes that actually differ, after stripping the common prefix/suffix.

    This is the minimum the model had to send to express the change. Anything
    beyond it is context it chose to include for uniqueness.
    """
    if old == new:
        return 0
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < (limit - start) and old[len(old) - 1 - end] == new[len(new) - 1 - end]:
        end += 1
    return (len(old) - start - end) + (len(new) - start - end)


def extract_edits(tool_name: str, tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    """Return [(old, new)] pairs for any supported edit tool shape."""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return [
                (str(e.get("old_string", "")), str(e.get("new_string", "")))
                for e in edits
                if isinstance(e, dict)
            ]
        return []
    if tool_name in ("Edit", "NotebookEdit"):
        old = tool_input.get("old_string", tool_input.get("old_source", ""))
        new = tool_input.get("new_string", tool_input.get("new_source", ""))
        return [(str(old), str(new))]
    if tool_name == "Write":
        # A Write has no match string, so it has no inflation. It is tracked
        # for the failure taxonomy only.
        return []
    return []


def result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        return " ".join(
            c.get("text", "") for c in content if isinstance(c, dict)
        )
    return str(content)


# --- Scanning ---

def iter_transcripts(since: datetime | None) -> Iterator[Path]:
    if not PROJECTS_DIR.exists():
        return
    cutoff = since.timestamp() if since else None
    for path in PROJECTS_DIR.glob("**/*.jsonl"):
        try:
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        yield path


def scan(args: argparse.Namespace, since: datetime | None) -> tuple[list[dict], dict]:
    records: list[dict] = []
    meta = {"transcripts_scanned": 0, "transcripts_unreadable": 0, "unresolved_calls": 0}

    for path in iter_transcripts(since):
        meta["transcripts_scanned"] += 1
        pending: dict[str, dict] = {}
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            meta["transcripts_unreadable"] += 1
            continue

        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                if block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                    tool_name = block["name"]
                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        continue
                    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
                    pairs = extract_edits(tool_name, tool_input)
                    total_bytes = sum(len(o) + len(n) for o, n in pairs)
                    core_bytes = sum(core_change_bytes(o, n) for o, n in pairs)
                    pending[str(block.get("id"))] = {
                        "tool": tool_name,
                        "path": file_path,
                        "extension": (os.path.splitext(file_path)[1] or "<none>").lower(),
                        "project": entry.get("cwd", ""),
                        "model": message.get("model", "unknown"),
                        "ts": entry.get("timestamp", ""),
                        "edits_count": len(pairs) if pairs else 1,
                        "mode": "multi" if tool_name == "MultiEdit" else "single",
                        "total_edit_bytes": total_bytes,
                        "core_bytes": core_bytes,
                        # None means "no measurable change to inflate", which
                        # is not the same as a ratio of zero.
                        "inflation_ratio": (total_bytes / core_bytes) if core_bytes > 0 else None,
                        "session": path.stem,
                        "transcript": str(path),
                    }

                elif block.get("type") == "tool_result":
                    record = pending.pop(str(block.get("tool_use_id")), None)
                    if record is None:
                        continue
                    failed = block.get("is_error") is True
                    record["success"] = not failed
                    record["error_kind"] = classify_error(result_text(block)) if failed else None
                    record["error_text"] = re.sub(r"\s+", " ", result_text(block))[:300] if failed else None
                    records.append(record)

        # A call with no result never resolved (session ended mid-flight).
        # It is neither a success nor a failure and must stay out of both.
        meta["unresolved_calls"] += len(pending)

    if args.tool:
        records = [r for r in records if r["tool"] == args.tool]
    if args.ext:
        records = [r for r in records if r["extension"] == args.ext.lower()]
    if args.model:
        records = [r for r in records if args.model in r["model"]]
    if args.project:
        records = [r for r in records if args.project in r["project"]]
    if args.failed_only:
        records = [r for r in records if not r["success"]]

    return records, meta


# --- Aggregation ---

def rate(numerator: int, denominator: int) -> float | None:
    """A rate, or None when there is no population to measure."""
    return round(numerator / denominator, 4) if denominator else None


def bucket_for(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    for label, low, high in INFLATION_BUCKETS:
        if low <= ratio < high:
            return label
    return INFLATION_BUCKETS[-1][0]


def group_stats(records: list[dict], key) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[str(key(record))].append(record)
    out = []
    for name, group in groups.items():
        failed = sum(1 for r in group if not r["success"])
        out.append({
            "name": name,
            "calls": len(group),
            "failed": failed,
            "failure_rate": rate(failed, len(group)),
        })
    return sorted(out, key=lambda g: (-g["calls"], g["name"]))


def build_summary(records: list[dict], meta: dict, window: str) -> dict:
    total = len(records)
    failed = [r for r in records if not r["success"]]

    kinds = Counter(r["error_kind"] for r in failed)

    # Inflation is only a candidate cause for string-matching failures.
    # Restrict both numerator and denominator to calls that could exhibit it:
    # a Write has no match string, and a workflow failure would have happened
    # at any inflation.
    inflation_pool = [
        r for r in records
        if r["inflation_ratio"] is not None
        and (r["success"] or r["error_kind"] in STRING_MATCH_FAILURES)
    ]
    inflation_buckets = []
    for label, _, _ in INFLATION_BUCKETS:
        group = [r for r in inflation_pool if bucket_for(r["inflation_ratio"]) == label]
        bucket_failed = sum(1 for r in group if not r["success"])
        inflation_buckets.append({
            "bucket": label,
            "calls": len(group),
            "failed": bucket_failed,
            "failure_rate": rate(bucket_failed, len(group)),
        })

    ratios = sorted(r["inflation_ratio"] for r in records if r["inflation_ratio"] is not None)

    def percentile(p: float) -> float | None:
        if not ratios:
            return None
        idx = min(len(ratios) - 1, int(p * len(ratios)))
        return round(ratios[idx], 2)

    # Same-file clusters: consecutive single edits to one file within a
    # session, which a single batched call could have expressed.
    clusters = 0
    by_session: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_session[record["session"]].append(record)
    for group in by_session.values():
        run = 1
        for prev, cur in zip(group, group[1:]):
            if cur["path"] == prev["path"] and cur["mode"] == "single":
                run += 1
            else:
                if run >= 3:
                    clusters += 1
                run = 1
        if run >= 3:
            clusters += 1

    worst = sorted(
        (r for r in records if r["inflation_ratio"] is not None),
        key=lambda r: (-r["inflation_ratio"], -r["total_edit_bytes"]),
    )

    return {
        # Consumers (maintain.py's digest tripwire) gate on freshness, so a
        # stale artifact never alarms as if it were current.
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": window,
        "scan": meta,
        "totals": {
            "resolved_calls": total,
            "succeeded": total - len(failed),
            "failed": len(failed),
            "failure_rate": rate(len(failed), total),
        },
        "failure_kinds": [
            {
                "kind": kind,
                "count": count,
                "share_of_failures": rate(count, len(failed)),
                "class": (
                    "workflow" if kind in WORKFLOW_FAILURES
                    else "string_match" if kind in STRING_MATCH_FAILURES
                    else "other"
                ),
            }
            for kind, count in kinds.most_common()
        ],
        "inflation": {
            "note": "String-matching failures only; workflow failures excluded.",
            "pool_size": len(inflation_pool),
            "buckets": inflation_buckets,
            "p50": percentile(0.50),
            "p90": percentile(0.90),
            "p99": percentile(0.99),
        },
        "by_tool": group_stats(records, lambda r: r["tool"]),
        "by_model": group_stats(records, lambda r: r["model"]),
        "by_extension": group_stats(records, lambda r: r["extension"])[:12],
        "by_batch_size": group_stats(
            [r for r in records if r["mode"] == "multi"],
            lambda r: "1" if r["edits_count"] == 1 else "2" if r["edits_count"] == 2 else "3+",
        ),
        "same_file_clusters": clusters,
        "worst_inflation": [
            {
                "path": r["path"],
                "inflation_ratio": round(r["inflation_ratio"], 1),
                "total_edit_bytes": r["total_edit_bytes"],
                "core_bytes": r["core_bytes"],
                "failed": not r["success"],
                "error_kind": r["error_kind"],
            }
            for r in worst[:DEFAULT_TOP]
        ],
    }


# --- Reporting ---

def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:5.1f}%"


def print_report(summary: dict, top: int) -> None:
    scan_meta = summary["scan"]
    totals = summary["totals"]
    print(f"\ntool-time edit diagnostic — {summary['window']}")
    print(f"  transcripts scanned : {scan_meta['transcripts_scanned']:,}")
    if scan_meta["transcripts_unreadable"]:
        print(f"  unreadable          : {scan_meta['transcripts_unreadable']:,}")
    print(f"  resolved calls      : {totals['resolved_calls']:,}")
    print(f"  unresolved (no result, excluded from both numerator and denominator): {scan_meta['unresolved_calls']:,}")

    if totals["resolved_calls"] == 0:
        print("\nNo resolved edit calls in window — nothing to measure.")
        return

    print(f"\nOverall: {totals['failed']:,} failed / {totals['resolved_calls']:,} resolved = {pct(totals['failure_rate'])}")

    print("\nFailure kinds (the causal breakdown)")
    if not summary["failure_kinds"]:
        print("  none")
    for kind in summary["failure_kinds"]:
        print(f"  {kind['kind']:<22} {kind['count']:5,}  {pct(kind['share_of_failures'])} of failures   [{kind['class']}]")

    infl = summary["inflation"]
    print(f"\nFailure rate by inflation bucket  (pool={infl['pool_size']:,}; {infl['note']})")
    for bucket in infl["buckets"]:
        print(f"  {bucket['bucket']:<8} {bucket['calls']:6,} calls   failed={bucket['failed']:4,}   rate={pct(bucket['failure_rate'])}")
    print(f"  inflation p50={infl['p50']}  p90={infl['p90']}  p99={infl['p99']}")

    print("\nBy tool")
    for group in summary["by_tool"]:
        print(f"  {group['name']:<14} {group['calls']:6,} calls   failed={group['failed']:4,}   rate={pct(group['failure_rate'])}")

    print("\nBy model")
    for group in summary["by_model"][:8]:
        print(f"  {group['name']:<34} {group['calls']:6,} calls   rate={pct(group['failure_rate'])}")

    print("\nBy extension")
    for group in summary["by_extension"]:
        print(f"  {group['name']:<12} {group['calls']:6,} calls   rate={pct(group['failure_rate'])}")

    if summary["by_batch_size"]:
        print("\nMultiEdit batch size")
        for group in summary["by_batch_size"]:
            print(f"  edits={group['name']:<4} {group['calls']:6,} calls   rate={pct(group['failure_rate'])}")

    print(f"\nSame-file clusters (3+ consecutive single edits to one file): {summary['same_file_clusters']:,}")

    print(f"\nWorst inflation (top {top})")
    for item in summary["worst_inflation"][:top]:
        flag = "FAIL" if item["failed"] else "ok  "
        print(f"  {flag} {item['inflation_ratio']:8.1f}x  {item['total_edit_bytes']:7,}B sent / {item['core_bytes']:6,}B changed  {item['path'][-58:]}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"Lookback window (default {DEFAULT_DAYS}).")
    parser.add_argument("--since", help="ISO timestamp; overrides --days.")
    parser.add_argument(
        "--auto-since-path",
        help="Use this file's mtime as the window start — scopes stats to "
             "'since the guidance/tool last changed' for before/after comparison.",
    )
    parser.add_argument("--all", action="store_true", help="Scan every transcript regardless of age.")
    parser.add_argument("--tool", choices=EDIT_TOOLS, help="Restrict to one tool.")
    parser.add_argument("--ext", help="Restrict to one file extension, e.g. .py")
    parser.add_argument("--model", help="Substring match on model id.")
    parser.add_argument("--project", help="Substring match on project cwd.")
    parser.add_argument("--failed-only", action="store_true", help="Keep only failed calls.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="Examples to show.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a report.")
    parser.add_argument("--out", type=Path, help="Also write the JSON summary here.")
    args = parser.parse_args()

    if args.all:
        since, window = None, "all transcripts"
    elif args.auto_since_path:
        path = Path(os.path.expanduser(args.auto_since_path))
        if not path.exists():
            print(f"--auto-since-path does not exist: {path}", file=sys.stderr)
            return 2
        since = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        window = f"since {path.name} changed ({since:%Y-%m-%d %H:%M} UTC)"
    elif args.since:
        try:
            since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        except ValueError:
            print(f"Unparseable --since: {args.since}", file=sys.stderr)
            return 2
        window = f"since {since:%Y-%m-%d}"
    else:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        window = f"last {args.days}d"

    records, meta = scan(args, since)
    summary = build_summary(records, meta, window)

    if args.out:
        args.out.write_text(json.dumps(summary, indent=2))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_report(summary, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
