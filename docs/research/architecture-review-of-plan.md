# Architecture Review: Deep Analytics Implementation Plan

**Review Date**: 2026-02-14
**Plan**: `docs/plans/2026-02-14-deep-analytics-implementation-plan.md`
**Reviewer**: Flux-drive Architecture & Design Reviewer

---

## Executive Summary

The plan proposes a clean separation between:
- **summarize.py** (existing): lightweight 7-day stats, community upload pipeline
- **analyze.py** (new): deep session-level analytics, local dashboard data prep

Overall structure is sound with three **P0 issues** requiring fixes before implementation, four **P1 improvements** recommended, and two **P2 enhancements** for consideration.

---

## 1. Module Boundaries & Coupling

### Current State (summarize.py)
- **Responsibility**: Parse events.jsonl → aggregate stats → community upload pipeline
- **Contract**: 7-day window, CWD project filter, output to stats.json
- **Coupling**: None (pure data prep, called by hook.sh and upload.py)

### Proposed State (analyze.py)
- **Responsibility**: Parse events.jsonl → session-level analytics → local dashboard data
- **Contract**: All events by default, multiple filter options, output to analysis.json

### Boundary Analysis

**P0: Module responsibility overlap**
The plan states analyze.py `load_all_events` reads ALL events by default, but then provides `--project` filter. This contradicts summarize.py's contract where CWD project is **always** applied. Two modules with different filtering defaults creates confusion.

**Decision required**: Should analyze.py:
1. Always filter to CWD project (matches summarize.py, safe default)
2. Default to all projects but allow override (matches "personal analytics" use case)

**Recommendation**: Option 1 for consistency. Users who want cross-project analytics can explicitly pass `--project=""` or similar.

---

**P1: Shared utility extraction opportunity**
Both modules will implement:
- Event loading from JSONL with timestamp filtering
- Session ID extraction from event.id
- Project path filtering

These are prime candidates for `parsers.py` (which already exists for shared parsing). Extract:
```python
# parsers.py additions
def load_events_raw(since, until, project=None) -> list[dict]
def extract_session_id(event: dict) -> str
def group_by_session(events: list[dict]) -> dict[str, list[dict]]
```

Both summarize.py and analyze.py would import from parsers.py. This:
- Eliminates duplication
- Ensures consistent session ID parsing
- Provides single test surface for core parsing

**Impact**: Reduces analyze.py from ~400 lines to ~300 lines, improves maintainability.

---

**P2: TOOL_ALIASES placement**
Task B2 hardcodes TOOL_ALIASES in analyze.py. If this normalization proves valuable, summarize.py or community dashboard may want it later. Consider moving to parsers.py as:
```python
TOOL_ALIASES = {...}
def normalize_tool_name(name: str) -> str: ...
```

Not urgent (YAGNI until second caller), but worth noting.

---

## 2. Data Flow Architecture

### Current Flow (Community Dashboard)
```
hook.sh → events.jsonl
    ↓
summarize.py → stats.json
    ↓
upload.py → community API → D1 database
    ↓
dashboard.js fetches /v1/api/stats
```

### Proposed Flow (Local Dashboard)
```
hook.sh → events.jsonl
    ↓
analyze.py → analysis.json
    ↓
serve.sh copies analysis.json to local-dashboard/
    ↓
dashboard.js (local) fetches analysis.json via http.server
```

### Gap Analysis

**P0: Hook data quality regression**
Module A (Tasks A1-A3) proposes adding `source: "claude-code"` to hook.sh. However:

1. **Line 62-76 claim is incorrect**: Inspecting hook.sh shows the JSON builder is at **lines 62-76** using `jq -nc`, but the plan says "Add `--arg source "claude-code"` to the jq JSON builder (line 62-76)". The actual JSON construction happens in a single `jq` call that already includes conditional fields for skill/file/model.

2. **Model field extraction is already attempted**: Line 72 shows `--arg model "$MODEL"`, and line 76 includes `+ (if $model != "" then {model:$model} else {} end)`. The plan's Task A2 says "check if Claude Code actually provides `.model`" — this investigation should happen BEFORE the plan, not during implementation.

3. **Source field is missing**: The JSON output (line 73-76) does NOT include a `source` field. This is the actual gap. However, hardcoding `"claude-code"` is correct — hooks only fire in Claude Code, not Codex CLI or OpenClaw.

**Fix**: Task A1 is valid but under-specified. The actual change is:
```bash
# Add to jq args (around line 72)
--arg source "claude-code" \

# Add to JSON construction (around line 76)
+ {source: $source}
```

**Fix**: Task A2 should be: "Verify model extraction works by testing a session, inspecting events.jsonl for non-empty model field. If empty, investigate transcript_path or other hook payload fields."

---

**P1: Data freshness disconnect**
The plan doesn't specify WHEN analyze.py runs. Options:

1. **On-demand only** (user runs manually or via skill)
2. **Hook-triggered** (SessionEnd runs analyze.py like it runs summarize.py)
3. **Serve-time** (serve.sh runs analyze.py before copying)

Current plan implies option 1 (Task D1 says "run analyze.py before reading stats"), but this creates stale data risk: user opens dashboard, sees week-old analysis because they forgot to re-run analyze.py.

**Recommendation**: Option 3 (serve.sh integration). Change Task C9 to:
```bash
# serve.sh
cd "$(dirname "$0")/.."
python3 analyze.py  # always refresh before serving
cp ~/.claude/tool-time/analysis.json local-dashboard/
cd local-dashboard
python3 -m http.server 8742
```

This ensures dashboard always shows current data without requiring user to remember separate commands.

---

**P0: Session ID extraction inconsistency**
Task B1 says `extract_session_id` parses `"uuid-seq"` → `"uuid"`. Looking at hook.sh line 59, ID is constructed as `"${SESSION_ID}-${SEQ}"` where SESSION_ID comes from hook payload `.session_id` (line 32). This is correct.

However, summarize.py line 116 uses `session_id = ev["id"].rsplit("-", 1)[0]` which correctly splits on the LAST hyphen. But session UUIDs themselves contain hyphens (e.g., `abc-def-ghi-123`), so a naive split would break.

**Current code is correct** (rsplit with maxsplit=1), but plan doesn't specify this. Task B1 should explicitly say:
```python
def extract_session_id(event: dict) -> str:
    """Extract session UUID from event.id (format: 'uuid-seq')."""
    return event["id"].rsplit("-", 1)[0]  # split on LAST hyphen only
```

---

## 3. Local vs Community Dashboard Separation

### Separation Analysis

**Clean separation achieved**:
- Community dashboard: aggregate cross-user stats, no session-level data
- Local dashboard: personal session-level analytics, no upload

**Shared concerns**:
- Both use GitHub Dark theme (consistency is good)
- Both use similar rendering patterns (Chart.js vs D3.js)

### Potential Issues

**P1: Visualization library divergence**
Community dashboard uses Chart.js (simpler, pre-built chart types). Local dashboard uses D3.js (flexible, custom layouts). This creates:

1. **Maintenance burden**: Two charting APIs to understand
2. **Code reuse barrier**: Can't share any chart components
3. **User experience drift**: Different interactions, animations, responsiveness

**Why the divergence?** Plan Task C4 says "Sankey diagram of top 20 tool transitions" which Chart.js doesn't support natively, requiring d3-sankey plugin.

**Alternative**: Use Chart.js for local dashboard too, render bigrams as a simple horizontal bar chart instead of Sankey. Sankey diagrams are visually impressive but not essential for understanding tool transitions — a sorted bar chart of "Read→Edit: 45, Edit→Bash: 32" conveys the same information with less complexity.

**Recommendation**: Reconsider Sankey. If keeping it, document why D3.js is necessary (enables Sankey, heatmaps, complex layouts). If not essential, use Chart.js for consistency.

---

**P2: Dashboard code duplication**
Local dashboard will reimplement:
- KPI card rendering
- Chart color schemes
- Responsive layout patterns
- Dark theme CSS

Community dashboard already has these (style.css, dashboard.js utilities). However, extracting shared components would create coupling between "local dev tool" and "public web app" which violates separation of concerns.

**Acceptable duplication**: Keep separate. The dashboards serve different audiences and will evolve independently.

---

## 4. Tool Name Normalization Strategy

### Proposed Approach
- **Apply in**: Cross-source comparison (Task B8), trends (Task B6)
- **Raw names in**: Project breakdown (Task B9), chain analysis (Task B5)

### Rationale Analysis

**Correct decision**: Normalization is for comparing "equivalent tools across different agents" (e.g., Codex's `shell` vs Claude Code's `Bash`). Within a single project or chain analysis, preserving raw names shows what the user actually sees.

### Implementation Concerns

**P1: Alias map completeness**
Proposed TOOL_ALIASES:
```python
{
    "shell": "Bash",
    "shell_command": "Bash",
    "exec_command": "Bash",
    "write_stdin": "Write",
    "update_plan": "TaskUpdate",
}
```

This list came from... where? The plan should reference actual data. Before implementing:

1. Query events.jsonl for unique tool names across all sources
2. Manually inspect for semantic equivalents
3. Document rationale for each alias in a comment

**Missing aliases**: Likely candidates:
- `read_file` → `Read` (if any source uses this)
- `edit_file` → `Edit`
- `grep` → `Grep` (if raw grep is logged vs Grep tool)

**Recommendation**: Add Task B2a: "Audit events.jsonl for cross-source tool name variations, document findings."

---

**P0: Normalization application inconsistency**
Task B6 (trends) says "tool breakdown (top 10)" but doesn't specify if normalized. Task B8 (source comparison) explicitly says "top 5 tools (normalized)". Task B9 (project breakdown) says "top 5 tools (raw names)".

Trends span time, not sources, so normalization isn't necessary... but trends may include data from multiple sources if user switches clients.

**Decision required**: Should weekly trends normalize tool names?

**Recommendation**: Yes, normalize in trends. If a user switched from Codex to Claude Code mid-week, `shell` and `Bash` should be counted together. Update Task B6 spec to clarify.

---

## 5. serve.sh Architecture

### Proposed Approach (Task C9)
```bash
# local-dashboard/serve.sh
# 1. Copy analysis.json from ~/.claude/tool-time/ to local-dashboard/
# 2. Start python3 -m http.server 8742
# 3. Print URL
```

### Concerns

**P1: Port conflict handling**
Port 8742 is arbitrary. If already in use (e.g., user runs serve.sh twice), http.server fails with "Address already in use". No error handling specified.

**Recommendation**: Add to Task C9:
```bash
# Check if port is in use, auto-increment if needed
PORT=8742
while lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done
python3 -m http.server $PORT
echo "Dashboard: http://localhost:$PORT"
```

---

**P1: File copy vs symlink**
Task C9 says "copy analysis.json" but this creates staleness: if user re-runs analyze.py (to refresh stats), dashboard won't update until they restart serve.sh.

**Alternative**: Symlink instead of copy:
```bash
ln -sf ~/.claude/tool-time/analysis.json local-dashboard/analysis.json
```

But symlinks don't work with http.server if the target is outside the serving directory (security restriction).

**Better alternative**: Integrate analyze.py run into serve.sh (as noted in section 2, "Data freshness disconnect"). Then copy is fine because it's always fresh.

---

**P2: Background server management**
serve.sh starts http.server in foreground, blocking the terminal. User must Ctrl+C to stop, which isn't documented. Consider:

```bash
# Background mode with cleanup
trap "kill $SERVER_PID 2>/dev/null" EXIT
python3 -m http.server $PORT &
SERVER_PID=$!
echo "Dashboard: http://localhost:$PORT (Ctrl+C to stop)"
wait $SERVER_PID
```

This allows serve.sh to handle cleanup. But adds complexity for a dev tool. Probably YAGNI — foreground is fine, just document it.

---

## 6. Additional Architectural Observations

### Missing: Error handling strategy
Neither summarize.py nor the plan specify behavior when:
- events.jsonl is empty (no sessions to analyze)
- events.jsonl is malformed (JSON parse errors)
- No events match filters (project or date range)

**Current summarize.py behavior**: Returns empty lists/dicts, which is correct (no data = no stats). Plan should explicitly state analyze.py follows this pattern:
- Empty events → analysis.json with all metrics = 0 or empty lists
- Never raises exceptions, always produces valid JSON

Add to Task B10: "Handle empty input gracefully: write analysis.json with zero counts, empty lists. Never error on missing data."

---

### Missing: Output schema documentation
Task B10 says analyze.py outputs to `~/.claude/tool-time/analysis.json` but doesn't define the schema. Dashboard tasks (C3-C8) imply structure:

```json
{
  "generated": "ISO timestamp",
  "date_range": {"since": "...", "until": "..."},
  "sessions": {
    "total": 42,
    "avg_tools_per_session": 15.3,
    "median_tools_per_session": 12,
    "avg_duration_minutes": 18.5,
    "classification": {
      "planning": 5,
      "debugging": 12,
      ...
    }
  },
  "tool_chains": {
    "bigrams": [{"from": "Read", "to": "Edit", "count": 45}, ...],
    "trigrams": [{"tools": ["Read", "Edit", "Bash"], "count": 12}, ...],
    "retry_patterns": [{"tool": "Bash", "avg_retries": 2.3, ...}, ...]
  },
  "trends": [{"week": "2026-W06", "events": 1200, ...}, ...],
  "time_patterns": {
    "by_hour": [...],
    "by_day": [...],
    "peak_hour": 14,
    ...
  },
  "by_source": {"claude-code": {...}, "codex": {...}},
  "projects": {"/root/projects/foo": {...}, ...}
}
```

**Recommendation**: Add Task B0 (before B1): "Define analysis.json schema in docstring or separate schema.json file for reference by dashboard developers."

---

### Test coverage gaps
Task B11 defines test classes but doesn't mention:

1. **Integration test**: Full end-to-end flow (write events.jsonl → run analyze.py → read analysis.json → validate schema)
2. **Edge cases**:
   - Events out of order by timestamp
   - Missing optional fields (skill, file, model)
   - Session with only 1 event (can't form bigrams)
   - Empty tool name (should be filtered or counted as "unknown")

**Recommendation**: Add `TestEndToEnd` class covering the full pipeline.

---

### Performance considerations
Plan says "330K events takes <1s in Python" (YAGNI Cuts section) but doesn't validate this claim.

**Recommendation**: Add to Task B11: "Performance test with 100K+ event corpus, verify <2s runtime on typical hardware."

If analyze.py will run on every serve.sh invocation (per section 2 recommendation), sub-second performance is important for good UX.

---

## 7. Execution Order Review

Proposed order:
```
A1 → A2 → A3 (hook fix)
     ↓
B1 → B2 → B3 → B4 (core engine)
                 ↓
          B5 → B6 → B7 → B8 → B9 → B10 (dimensions)
                                          ↓
                                         B11 (tests)
                                          ↓
                                    C1 → C2 (dashboard shell)
                                          ↓
                              C3 → C4 → C5 → C6 → C7 → C8 → C9
                                                                ↓
                                                          D1 → D2
```

### Issues

**P1: Testing too late**
Tests (B11) come after all implementation (B1-B10). This is waterfall, not TDD. If session classification logic (B3) is complex, writing tests first would clarify requirements.

**Recommendation**: Reorder to:
```
A1 → A2 → A3
B1 → B2 (core utilities + tests)
B3 + B3-tests (session classification)
B4 + B4-tests (session metrics)
... (write tests alongside each dimension)
B10 (integration)
B11 (remaining integration/edge case tests)
```

---

**P0: Hook changes block analysis development**
Module A adds `source` field to events. Module B (Task B8) consumes `source` for cross-source comparison. If B8 runs before A is deployed, source will be missing from events.

But analyze.py should handle missing fields gracefully (same as model, which may be empty). This isn't a blocker, just a data quality issue.

**Clarification needed**: Add to Task B8: "Handle missing source field (treat as 'unknown' or skip in source comparison)."

---

## Summary of Findings

### P0 (Must fix before implementation)

1. **Module A spec error**: Hook.sh line numbers and field extraction details are incorrect. Revise Task A1-A2 with actual code locations.
2. **Session ID parsing**: Explicitly specify `rsplit("-", 1)[0]` to handle UUIDs with hyphens.
3. **Normalization inconsistency**: Clarify whether trends (Task B6) normalize tool names.

### P1 (Should fix)

1. **Extract shared parsing to parsers.py**: Avoid duplication between summarize.py and analyze.py.
2. **Data freshness**: Integrate analyze.py into serve.sh to auto-refresh before serving.
3. **Tool alias audit**: Query real data for cross-source tool name variations before hardcoding aliases.
4. **Port conflict handling**: serve.sh should check if port 8742 is in use.
5. **Test-driven order**: Write tests alongside features, not after all implementation.
6. **Visualization library justification**: Document why D3.js is necessary for local dashboard vs reusing Chart.js.

### P2 (Nice to have)

1. **TOOL_ALIASES in parsers.py**: Future-proof if multiple modules need normalization.
2. **serve.sh background mode**: Add trap-based cleanup for better UX.

### Strengths

- Clean separation between summarize.py (community pipeline) and analyze.py (local analytics)
- Session-scoped analysis is the right abstraction layer
- YAGNI cuts are appropriate (no incremental, no database, no auth)
- Classification algorithm is well-defined with clear priority ordering
- Test plan is comprehensive (just needs reordering)

---

## Recommendations Summary

1. **Before starting implementation**:
   - Fix Task A1-A2 specifications (verify actual hook.sh code)
   - Define analysis.json schema (Task B0)
   - Audit events.jsonl for tool name variations (Task B2a)
   - Decide: should analyze.py default to CWD project or all projects?

2. **During implementation**:
   - Extract shared parsing utilities to parsers.py first
   - Write tests alongside each analysis dimension
   - Integrate analyze.py into serve.sh for data freshness

3. **Before merging**:
   - Run performance test with 100K+ events
   - Validate all charts render with real data
   - Document why D3.js was chosen (if Sankey is kept)

---

## Conclusion

The implementation plan is **structurally sound** with clear module boundaries and appropriate separation of concerns. The three P0 issues are specification errors, not architecture flaws — easily fixed before coding begins. The P1 recommendations would improve maintainability and user experience without changing the core design.

**Proceed with implementation** after addressing P0 issues and considering P1 improvements.
