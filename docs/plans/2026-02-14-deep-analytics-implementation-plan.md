# Deep Analytics Engine — Implementation Plan

**Date**: 2026-02-14 (revised post flux-drive review)
**Design**: `docs/plans/2026-02-14-deep-analytics-engine-design.md`
**Brainstorm**: `docs/brainstorms/2026-02-14-deep-analytics-engine-brainstorm.md`
**Reviews**: `docs/research/{architecture,correctness,user-product}-review-of-plan.md`

## Review Findings Incorporated

Key fixes from flux-drive review:
- **Event type contract**: `is_call_event()` helper used everywhere — PreToolUse/ToolUse for call counts, PostToolUse/ToolUse for error counts. Prevents double-counting and missed errors.
- **Tool chain fix**: Only count PreToolUse/ToolUse in bigrams/trigrams (prevents fake self-loops from Pre→Post pairs).
- **Retry scope**: Only file-based tools (Read/Edit/Write) — others have no `file` field, causing false positives.
- **ISO week key**: `(iso_year, iso_week)` not just `week` — prevents year-boundary merging.
- **Division guards**: All ratio computations guarded against total=0.
- **Dashboard reorder**: Diagnostic-first (retry patterns, tool chains) then descriptive (overview, trends).
- **Skill is text-first**: Findings printed in chat, dashboard is optional deep-dive.
- **Timezone**: Time-of-day uses system local timezone, not UTC.
- **Normalization in trends**: Yes — cross-source data mixes within weeks.

## Module Overview

| Module | Files | Dependencies | Estimated Size |
|--------|-------|-------------|---------------|
| A. Hook data quality fix | `hooks/hook.sh` | None | ~5 lines changed |
| B. Analysis engine | `analyze.py`, `test_analyze.py` | None (stdlib only) | ~500 lines + ~350 test lines |
| C. Local D3 dashboard | `local-dashboard/{index.html,dashboard.js,style.css,serve.sh}` | D3.js v7 (CDN) | ~700 lines total |
| D. Skill update | `skills/tool-time/SKILL.md` | Module B | ~30 lines changed |

## Module A: Hook Data Quality Fix

**Goal**: Fix hook.sh to write `source` field on every event. Verify model extraction.

### Task A1: Add source field to hook.sh
- **File**: `hooks/hook.sh`
- **Change**: In the jq JSON builder (the `jq -nc` call starting around line 62), add `--arg source "claude-code"` and include `+ {source: $source}` in the JSON template. Hooks only fire in Claude Code, so hardcode the value.
- **Exact location**: After `--arg model "$MODEL"` add `--arg source "claude-code"`, then after the model conditional add `+ {source: $source}`.

### Task A2: Verify model field extraction
- **File**: `hooks/hook.sh`
- **Action**: The hook already attempts to extract `.model` (line 25 in jq, line 72 as `--arg model`). Run a test session, inspect events.jsonl. If model is empty on all hook events, document that model is not available via Claude Code hooks (only from transcript parsing). Do not spend time finding an alternative — model data is nice-to-have, not critical.

### Task A3: Test hook changes
- **Manual**: After A1, run a Claude Code session in tool-time project. Inspect last 5 lines of `~/.claude/tool-time/events.jsonl` to verify `"source": "claude-code"` appears on new events.

---

## Module B: Analysis Engine (`analyze.py`)

**Goal**: Read events.jsonl, produce rich `analysis.json` with session classification, tool chains, trends, time patterns, and source comparison.

### Core Convention: Event Type Contract

All analysis functions must follow this convention (matching summarize.py):
- **Call events** (for counting tool invocations): `event["event"] in ("PreToolUse", "ToolUse")`
- **Error events** (for counting failures): `event["event"] in ("PostToolUse", "ToolUse")` and `event["error"] is not None`
- **Chain events** (for bigrams/trigrams): Only call events — never PostToolUse

Helper function:
```python
def is_call_event(event: dict) -> bool:
    return event.get("event") in ("PreToolUse", "ToolUse")

def is_error_event(event: dict) -> bool:
    return (event.get("event") in ("PostToolUse", "ToolUse")
            and event.get("error") is not None)
```

### Task B1: Create `analyze.py` with event loader + helpers
- **File**: `analyze.py`
- **Functions**:
  - `load_all_events(since, until, project, source)` — reads events.jsonl with optional filters. Default: last 90 days, all projects (unlike summarize.py which scopes to CWD).
    - **Timestamp parsing**: Handle both ISO strings (`"2026-01-19T..."`) and numeric ms (from backfill). Store parsed datetime as `event["_ts"]` for internal use.
    - **Missing fields**: Treat missing `source` as `"unknown"`, missing `model` as None.
  - `extract_session_id(event) -> str` — `event["id"].rsplit("-", 1)[0]`. Document that this relies on the hook.sh convention of `"uuid-seq"` format with seq always being a positive integer.
  - `group_by_session(events) -> dict[str, list[dict]]` — group by session ID, sort each group by `_ts`.
  - `is_call_event(event) -> bool` and `is_error_event(event) -> bool` — the core convention helpers.
- **Pattern**: Follow summarize.py conventions (Path constants, type hints, no external deps)
- **Tests**: Write `TestLoadAllEvents`, `TestExtractSessionId`, `TestGroupBySession` alongside this task.

### Task B2: Implement tool name normalization
- **File**: `analyze.py`
- **Function**: `normalize_tool_name(name: str) -> str`
- **Map** (audit events.jsonl first for completeness):
  ```python
  TOOL_ALIASES = {
      "shell": "Bash",
      "shell_command": "Bash",
      "exec_command": "Bash",
      "write_stdin": "Write",
      "update_plan": "TaskUpdate",
  }
  ```
- **Pre-task**: Run a quick audit of unique tool names across sources to check for any additional aliases needed.
- **Usage**: Applied in cross-source comparison, trends, and tool chains. Raw names preserved in project breakdowns.
- **Tests**: `TestNormalizeToolName` — alias mapping, passthrough for unknown tools.

### Task B3: Implement session classification
- **File**: `analyze.py`
- **Function**: `classify_session(events: list[dict]) -> str`
- **Guard**: If no call events in session, return `"other"` immediately (prevents division by zero).
- **Algorithm** (priority order, computed only over call events):
  1. **Planning**: `EnterPlanMode`/`ExitPlanMode` present, OR skill matches `{brainstorm, writing-plans, strategy, write-plan}` — but only if planning-related events are >10% of total (prevents 1 brainstorm skill in a 500-event building session from misclassifying)
  2. **Debugging**: `error_count / total > 0.15` OR `(bash_pct > 0.4 AND error_count > 3)` — error_count from `is_error_event()`, total from `is_call_event()`
  3. **Building**: `(edit + write) / total > 0.25`
  4. **Reviewing**: `read / total > 0.50` AND `(edit + write) == 0`
  5. **Exploring**: `(read + glob + grep) / total > 0.55` AND `edit / total < 0.10`
  6. **Other**: fallback
- **Returns**: One of `"planning"`, `"debugging"`, `"building"`, `"reviewing"`, `"exploring"`, `"other"`
- **Tests**: One test per classification type, priority ordering test, empty session test, division-by-zero test.

### Task B4: Implement session metrics
- **File**: `analyze.py`
- **Function**: `compute_session_metrics(sessions: dict[str, list[dict]]) -> dict`
- **Computes**:
  - Total session count
  - Average/median tool calls per session (count `is_call_event` only; exclude sessions with <2 call events as noise)
  - Average session duration in minutes (from first to last `_ts`; 0 if <2 events)
  - Classification distribution (calls B3 per session)
- **Output**: The `"sessions"` key of analysis.json
- **Tests**: Empty, single session, multiple sessions, noise filtering, duration computation.

### Task B5: Implement tool chain analysis
- **File**: `analyze.py`
- **Functions**:
  - `compute_bigrams(sessions)` — **only count transitions between call events** (PreToolUse/ToolUse). For each session, extract tool names from call events in timestamp order. Compute consecutive pairs. Filter to count >= 5. Return sorted by count desc, top 50.
  - `compute_trigrams(sessions)` — sliding window of 3 over call-event tool sequences. Top 30 by count.
  - `compute_retry_patterns(sessions)` — **only for file-based tools** (Read, Edit, Write). Detect: error on PostToolUse/ToolUse for file X, followed by call event for same tool + same file. Track per-tool: total retries, max retries in single session, sessions affected. Tools without `file` field are excluded (prevents false positives from Bash→Bash, Grep→Grep).
- **Output**: The `"tool_chains"` key of analysis.json
- **Tests**: Bigram with Pre+Post (verify no self-loops), trigram sliding window, retry detection with file matching, retry exclusion for non-file tools.

### Task B6: Implement trends
- **File**: `analyze.py`
- **Function**: `compute_weekly_trends(events: list[dict]) -> list[dict]`
- **Approach**: Group events by `(iso_year, iso_week)` from `_ts.isocalendar()`. Per week: event count, session count, error rate (from `is_error_event`), tool breakdown (top 10, **normalized**), classification mix.
- **Output format**: `{"week": "2026-W03", "iso_year": 2026, "iso_week": 3, ...}` — include both human-readable and machine-parseable week identifiers.
- **Output**: The `"trends"` key of analysis.json
- **Tests**: Single week, multi-week, year-boundary (Dec 31 → W01 of next year), empty.

### Task B7: Implement time-of-day patterns
- **File**: `analyze.py`
- **Function**: `compute_time_patterns(events: list[dict], tz_name: str | None = None) -> dict`
- **Timezone**: If `tz_name` provided, use it. Otherwise detect system timezone. Convert all `_ts` to local time before bucketing.
- **Computes**:
  - By hour (0-23): event count, error rate
  - By day of week (Monday-Sunday): event count, session count, error rate
  - Peak hour, peak day, most error-prone hour
  - `"timezone"` field in output so dashboard can display it
- **Output**: The `"time_patterns"` key of analysis.json
- **Tests**: Hour bucketing, day bucketing, peak detection, timezone conversion.

### Task B8: Implement source comparison
- **File**: `analyze.py`
- **Function**: `compute_source_comparison(events, sessions) -> dict`
- **Per source**: event count, session count, avg tools/session, error rate, top 5 tools (**normalized**), classification mix
- **Handle missing source**: Group events with `source=None` or `source="unknown"` as `"unknown"`. If only 1 source exists, still produce the output (dashboard handles single-source gracefully with a message).
- **Output**: The `"by_source"` key of analysis.json
- **Tests**: Multi-source, single-source, missing-source, normalization applied.

### Task B9: Implement project breakdown
- **File**: `analyze.py`
- **Function**: `compute_project_breakdown(events, sessions) -> dict`
- **Per project**: event count, session count, top 5 tools (raw names), primary classification, error rate
- **Output**: The `"projects"` key of analysis.json
- **Tests**: Multi-project.

### Task B10: Wire it all together — `main()`
- **File**: `analyze.py`
- **Function**: `main()` with argparse:
  - `--project` (default: all projects)
  - `--source` (default: all sources)
  - `--since` (default: 90 days ago)
  - `--until` (default: now)
  - `--timezone` (default: system timezone)
- **Flow**: load events → group by session → compute all dimensions → write analysis.json
- **Empty input**: If no events match filters, write analysis.json with zero counts and empty lists. Never error on missing data.
- **Output path**: `~/.claude/tool-time/analysis.json`
- **Print**: Path to output file (matches summarize.py convention)
- **Tests**: End-to-end integration test (write events → run main → validate output schema).

---

## Module C: Local D3 Dashboard

**Goal**: D3.js-based personal analytics dashboard reading analysis.json. Diagnostic-first information hierarchy.

### Task C1: Create dashboard HTML shell
- **File**: `local-dashboard/index.html`
- **Structure**: Single page with section nav. **Diagnostic-first order**:
  1. Retry Patterns (table — most actionable)
  2. Tool Chains (Sankey diagram)
  3. Overview (KPI cards + session classification donut)
  4. Projects (sortable table with drill-down)
  5. Trends (stacked area + error rate line)
  6. Source Comparison (grouped bars — hidden if only 1 source)
  7. Time Patterns (heatmap — bottom, curiosity not action)
- **Dependencies**: D3.js v7 via CDN, d3-sankey via CDN, local `dashboard.js` and `style.css`
- **Data loading**: Fetch `analysis.json` from same directory (served via python http.server)
- **Empty states**: Each section has a fallback message when data is insufficient (e.g., "No retry patterns detected — tool calls succeed on the first try.")

### Task C2: Create dark theme CSS
- **File**: `local-dashboard/style.css`
- **Theme**: GitHub Dark palette (consistent with community dashboard). Colors: `#0d1117` background, `#58a6ff` blue, `#3fb950` green, `#f85149` red, `#d2a8ff` purple, `#f0883e` orange.
- **Layout**: CSS grid, responsive sections, card-based KPIs

### Task C3: Implement Retry Patterns + Tool Chains section (top of page)
- **File**: `local-dashboard/dashboard.js`
- **Renders**:
  - Retry patterns table (tool, avg retries, max, sessions affected) — **first thing user sees**
  - Sankey diagram of top 20 tool transitions (bigrams) — visual "smoking gun"
- **D3**: `d3-sankey` layout via CDN
- **Empty state**: "No retry patterns detected" / "Need more data for tool chain visualization (minimum 50 tool calls)"

### Task C4: Implement Overview section
- **File**: `local-dashboard/dashboard.js`
- **Renders**: KPI cards (total sessions, total events, date range, avg session duration) + donut chart for session classification
- **D3**: `d3.arc()` for donut chart

### Task C5: Implement Trends section
- **File**: `local-dashboard/dashboard.js`
- **Renders**:
  - Stacked area chart: weekly tool usage (top 5 tools stacked)
  - Line chart overlay: error rate trend
- **D3**: `d3.area()`, `d3.stack()`, `d3.line()`
- **Empty state**: "Need at least 2 weeks of data to show trends. Current data spans X days."

### Task C6: Implement Time Patterns section
- **File**: `local-dashboard/dashboard.js`
- **Renders**:
  - Heatmap: 7 rows (days) × 24 columns (hours), colored by event density
  - Annotations: peak hour, most error-prone hour, timezone label
- **D3**: `d3.scaleSequential()` with interpolateYlOrRd color scale
- **Empty state**: "Need 100+ events across 3+ weeks for meaningful patterns."

### Task C7: Implement Source Comparison section
- **File**: `local-dashboard/dashboard.js`
- **Renders**:
  - Grouped bar chart: per-source metrics (events, sessions, avg tools, error rate)
  - Stacked bar: classification mix per source
- **D3**: `d3.scaleBand()` for grouped bars
- **Visibility**: Hidden entirely if only 1 source exists. Show message: "All events from [source]. Use multiple AI clients to compare."

### Task C8: Implement Projects section
- **File**: `local-dashboard/dashboard.js`
- **Renders**:
  - Sortable table with columns: project name, events, sessions, top tool, classification, error rate
  - Click to expand: full tool breakdown for that project
- **D3**: Table rendering with `d3.select().selectAll().data().join()`

### Task C9: Create serve.sh helper
- **File**: `local-dashboard/serve.sh`
- **Function**:
  1. Run `python3 analyze.py` first (auto-refresh, never serve stale data)
  2. Copy `~/.claude/tool-time/analysis.json` to `local-dashboard/`
  3. Find available port (start at 8742, increment if busy)
  4. Start `python3 -m http.server $PORT`
  5. Print `Dashboard: http://localhost:$PORT`
- **Usage**: `bash local-dashboard/serve.sh`

---

## Module D: Skill Update

### Task D1: Update /tool-time skill to use analyze.py
- **File**: `skills/tool-time/SKILL.md`
- **Key change**: Skill is **text-first**. Findings are printed in chat. Dashboard is optional.
- **Flow**:
  1. Run `python3 $CLAUDE_PLUGIN_ROOT/analyze.py` (produces analysis.json)
  2. Read `~/.claude/tool-time/analysis.json`
  3. **Auto-detect mode**: If >500 events, use deep analysis narrative. Otherwise, fall back to current stats.json behavior.
  4. Present findings in diagnostic order:
     - Retry patterns (most actionable — "Edit retried 3.2x avg, max 7 in one session")
     - Tool chain problems ("Read → Bash (cat) loop 42 times — use Read tool instead")
     - Session classification (context — "60% building, 20% debugging, 10% exploring")
     - Source comparison (only if multiple sources)
     - Time patterns (only if they reveal a problem — error rate >2x at specific hours)
     - Trends (week-over-week changes)
  5. Offer CLAUDE.md/AGENTS.md fixes as before
  6. **Dashboard prompt**: "For visual exploration (Sankey diagram, heatmap, trend charts), run: `bash $CLAUDE_PLUGIN_ROOT/local-dashboard/serve.sh`"
- **Backward compat**: If analyze.py fails, fall back to summarize.py + stats.json (current behavior)
- **Empty data warning**: If <100 events, say so: "Only N events — deep analysis needs more data. Run more sessions and try again."

### Task D2: Add dashboard and deletion triggers
- **File**: `skills/tool-time/SKILL.md`
- **Triggers**: Add "dashboard", "visual", "show charts" to trigger list
- **Flow**: If triggered by dashboard keyword, skip text analysis, go straight to running serve.sh and printing the URL.

---

## Execution Order (TDD-style, tests alongside features)

```
A1 → A2 → A3 (hook fix — independent, do first)

B1 + tests → B2 + tests → B3 + tests → B4 + tests
                                          ↓
B5 + tests → B6 + tests → B7 + tests → B8 + tests → B9 + tests
                                                       ↓
                                                      B10 + integration test
                                                       ↓
                                                 C1 → C2 (dashboard shell)
                                                       ↓
                                           C3 → C4 → C5 → C6 → C7 → C8 → C9
                                                                             ↓
                                                                       D1 → D2
```

**Parallelizable**: Module A is fully independent. Within Module B, B1-B4 must be sequential; B5-B9 can be done in any order after B4. Module C depends on B10 (needs the output format). Module D depends on both B and C.

## Testing Strategy

- `uv run --with pytest pytest test_analyze.py -v` — unit tests for all analysis functions (written alongside each task)
- **Edge cases to cover**: empty sessions, division-by-zero, ISO week year boundary, Pre+Post event pairs, missing source/model fields, tools without file paths
- Manual: run `python3 analyze.py`, inspect `analysis.json` structure
- Manual: `bash local-dashboard/serve.sh`, open in browser, verify all charts render with real data
- Compare analysis.json numbers against raw event counts for sanity

## YAGNI Cuts

- No incremental analysis (full re-read every time — 330K events takes <1s in Python)
- No database (JSONL is fine for this volume)
- No API for local dashboard (static file serving is sufficient)
- No auth on local dashboard (localhost only)
- No mobile layout (desktop-first)
- No automated scheduling (on-demand via skill or manual)
- No ML/clustering (rule-based classification, tune thresholds on real data later)
- No shared utilities refactor to parsers.py yet (YAGNI until summarize.py needs the same helpers)
