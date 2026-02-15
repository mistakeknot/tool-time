# User & Product Review: Deep Analytics Implementation Plan

**Date**: 2026-02-14
**Reviewer**: Flux-drive (User & Product Reviewer agent)
**Plan under review**: `docs/plans/2026-02-14-deep-analytics-implementation-plan.md`

## Primary User & Job to Be Done

**Primary user**: Claude Code users who use /tool-time skill (themselves — the agent operator, doing workflow optimization)

**Job to be done**: Understand *why* their Claude Code sessions have specific patterns (errors, tool choices, workflow types) so they can write better CLAUDE.md rules or adjust their prompting habits.

**Current state**: User invokes `/tool-time`, gets a 7-day summary focused on *problems* (error rates, edit-without-read, bash overuse). Simple, actionable, focused on "here's what's broken."

**Proposed state**: User can request "deep analysis" and get session classification, tool chains, trends, time patterns, source comparison, and a visual dashboard. Much richer, but also much more complex.

## Priority Findings

### P0 Findings (Blocks Value Delivery)

#### P0-1: Dashboard information hierarchy inverts value discovery

**Issue**: The plan presents dashboard sections in this order:
1. Overview (KPIs + session classification donut)
2. Tool Chains (Sankey + retries)
3. Trends (weekly stacked area)
4. Time Patterns (heatmap)
5. Source Comparison (grouped bars)
6. Projects (sortable table)

**Why this blocks value**: The *most actionable* information is buried. A user looking at this dashboard for the first time has no idea what to fix because:
- Session classification (Overview) is descriptive, not diagnostic — knowing you had 20 "debugging" sessions doesn't tell you *what* to change.
- Tool chains (position 2) and retry patterns are **the smoking gun** — they show "Read → Bash → Read → Bash" loops or "Edit (no prior Read)" sequences. This is where workflow problems live.
- Time patterns (position 4) are interesting but not actionable unless you're trying to debug "why do I get more errors at 2am" (which is rarely the job).

**What should a user see FIRST**:
1. **Retry patterns table** — tools with high retry counts signal fragile workflows
2. **Tool chain Sankey** — visualizes the loops and transitions that waste time
3. **Session classification + error rate** — context for interpreting the chains
4. **Projects table** — lets you drill into one project's patterns
5. **Trends** — useful for "am I getting better over time" but not for immediate action
6. **Time patterns** — curiosity, not action
7. **Source comparison** — only useful if you actually use multiple sources (Codex CLI + Claude Code)

**Recommendation**: Reorder dashboard sections to prioritize **diagnostic** over **descriptive**. Put retry patterns and tool chains at the top. Move time-of-day heatmap to the bottom (it's a novelty).

---

#### P0-2: The skill narrative buries the lead

**Issue**: The plan says skill narrative sections are:
> session classification, tool chains, source comparison, time patterns, trends

**Why this blocks value**: Same problem as the dashboard — session classification is presented first, but it's the *least* actionable. The user reads "you had 15 debugging sessions, 8 building sessions, 3 exploring sessions" and thinks "...okay, so what?"

**Current /tool-time skill strength**: It immediately surfaces **problems** with concrete numbers and proposed fixes. Example from existing skill:
- "Edit-without-read: 12 occurrences — the agent is editing files it hasn't read, leading to blind edits"
- "Bash dominance: 68% of calls, but 40% are file reads (cat, grep) that Read/Grep tools handle better"

**Proposed narrative order should be**:
1. **Retry patterns** — "Grep retried 4.2x on average across 8 sessions — you're searching the wrong way or for the wrong thing"
2. **Tool chain problems** — "You have a Read → Bash (cat) → Bash (grep) loop in 6 sessions — use Grep tool instead"
3. **Session classification** — "Most of your debugging sessions have 2x the error rate of building sessions — errors cluster in specific workflows"
4. **Source comparison** — only if multiple sources exist
5. **Time patterns** — only if they reveal something actionable (e.g., "error rate spikes 3x at 11pm — you're tired")
6. **Trends** — "Your error rate dropped 15% in the last 2 weeks — whatever you changed in CLAUDE.md is working"

**Recommendation**: Flip the narrative priority. Start with **retry patterns and tool chains**, then add classification as context. Omit time patterns unless they show a clear problem (like 5x error rate at a specific hour).

---

#### P0-3: Empty states will break value discovery for most users

**Issue**: The plan doesn't specify what happens when:
- Only 1 source exists (so source comparison is empty/useless)
- Zero retries detected (so retry patterns table is empty)
- Fewer than 2 weeks of data (so trends section has 1 bar)
- All sessions are "other" classification (no clear patterns)

**Why this blocks value**: A user who runs "deep analysis" for the first time and sees 3 empty sections will assume it's broken or useless. They won't understand that the emptiness *is* the signal (e.g., "no retries = healthy workflow").

**Recommendation**: Define empty states for every section:
- **Source comparison**: If only 1 source, show a message: "All events are from Claude Code. Install Codex CLI to compare workflows across tools."
- **Retry patterns**: If zero retries, show: "No retry patterns detected — your tool calls succeed on the first try."
- **Trends**: If <2 weeks of data, show: "Need at least 2 weeks of data to show trends. Current data spans X days."
- **Time patterns heatmap**: If data is too sparse (<100 events), show: "Need more data to show meaningful patterns. Run more sessions and try again."

Also: **The skill should warn the user** if analyze.py output will be mostly empty. For example: "You have 150 events over 5 days — this is enough for basic stats but not enough for trends or time patterns. Deep analysis works best with 500+ events over 2+ weeks."

---

### P1 Findings (Reduces Value)

#### P1-1: serve.sh friction is high for a "just show me the data" request

**Issue**: To see the visual dashboard, the user must:
1. Invoke `/tool-time` with "deep analysis" → runs analyze.py
2. Run `bash $CLAUDE_PLUGIN_ROOT/local-dashboard/serve.sh` (copies file, starts server)
3. Open `http://localhost:8742` in a browser
4. Remember to kill the server when done

**Why this reduces value**: The user's mental model is "I asked the agent to analyze my data, the agent should show me the results." Forcing a manual script + browser workflow breaks that expectation.

**Could the skill just output the analysis inline?** Not for visualizations (Sankey, heatmap, stacked area charts), but **yes for the textual findings**. The skill should:
1. Run `analyze.py`
2. Read `analysis.json`
3. Surface the **most actionable findings** as prose in the chat (same as current /tool-time skill)
4. Say: "For visual breakdowns (charts, heatmap, Sankey), run `bash $CLAUDE_PLUGIN_ROOT/local-dashboard/serve.sh` and open http://localhost:8742"

**Alternative**: Auto-open the browser if the user says "show dashboard" or "visualize". The skill can run `serve.sh &` in the background and call `xdg-open http://localhost:8742` (or `open` on macOS). But this only works if the user's environment supports it (SSH sessions, headless servers won't work).

**Recommendation**: Make the skill output **text-first, visuals-optional**. The default "deep analysis" flow should:
1. Run analyze.py
2. Print the 3-5 most actionable findings from retry patterns and tool chains
3. Offer to open the dashboard if the user wants to explore further

This way, the user gets value immediately in the chat, and the dashboard is an optional deep-dive for the curious.

---

#### P1-2: Value discovery is unclear — users won't know "deep analysis" exists

**Issue**: Current /tool-time triggers are:
- `tool stats`
- `tool usage`
- `tool-time`
- `show patterns`
- `analyze tools`

Proposed "deep analysis" trigger in the plan:
> "deep dive" trigger: if user says "deep analysis" or "full analysis", run analyze.py with full date range. Otherwise, suggest running it if events.jsonl has >1000 events.

**Why this reduces value**: Users who currently invoke `/tool-time` have no idea "deep analysis" is an option. They'll keep getting the simple 7-day summary and never discover the richer version.

**The plan says**: "suggest running it if events.jsonl has >1000 events" — but this is a one-time suggestion. If the user ignores it, they'll never see it again.

**Recommendation**: Make deep analysis **the default** if sufficient data exists (>500 events), with a flag to fall back to simple mode. Reasoning:
- If the user has 1000+ events, they're a power user who wants richer insights.
- If they want the simple version, they can say "quick summary" or "just errors."
- The skill should auto-detect data volume and choose the appropriate mode.

Alternatively: Add a new trigger `deep-tool-time` or `/tool-time --deep` so users can explicitly request it.

---

#### P1-3: "Deep analysis" vs regular trigger is ambiguous

**Issue**: The plan proposes:
- **Default /tool-time**: 7-day window, basic stats (current behavior)
- **"Deep analysis" /tool-time**: full date range, all dimensions

**Ambiguity**: What does "full date range" mean?
- All events ever recorded? (Could be years of data, causing performance issues)
- Last 90 days? (More reasonable, but not specified)
- Since project creation? (Inconsistent across projects)

**Performance risk**: If a user has 50,000+ events from 6 months of work, analyze.py will process all of it. The plan says "330K events takes <1s in Python" but this assumes:
- Fast disk I/O (SSD, not NFS)
- No complex filtering (e.g., project name matching across 50 projects)
- D3.js can render 50,000 data points in a Sankey without browser lag (it can't — Sankey with >500 nodes/links becomes unusable)

**Recommendation**: Define "full date range" as **last 90 days** and document it. Add a `--since` / `--until` flag to the skill if the user wants a custom range. Also: warn the user if analyze.py output will exceed reasonable visualization limits (e.g., "You have 1200 unique tool transitions — Sankey will only show the top 50").

---

#### P1-4: Session classification priority order may misclassify

**Issue**: The plan defines classification priority as:
1. Planning (if EnterPlanMode present OR skill matches brainstorm/writing-plans/etc.)
2. Debugging (if error_count / total > 0.15 OR bash_pct > 0.4 AND error_count > 3)
3. Building (if (edit + write) / total > 0.25)
4. Reviewing (if read / total > 0.50 AND (edit + write) == 0)
5. Exploring (if (read + glob + grep) / total > 0.55 AND edit / total < 0.10)
6. Other

**Edge case misclassifications**:
- **Planning**: A session with 1 brainstorm skill invocation + 50 Bash/Edit calls will classify as "planning" even though it's clearly building. The skill check should be weighted by event count, not binary presence.
- **Debugging**: `bash_pct > 0.4` is too low — many build workflows involve heavy bash usage (test runners, git, docker). This will misclassify "running tests in a TDD loop" as debugging.
- **Building vs Reviewing**: A session with 30 Read + 10 Edit (25% edit rate) classifies as Building. A session with 30 Read + 0 Edit classifies as Reviewing. But what if the user read 30 files, *decided not to change anything*, and moved on? That's still reviewing, but the classification makes it sound like two different workflows.

**Recommendation**: Revise thresholds based on real data. Run analyze.py on a sample of 100+ sessions and manually label 20-30 of them, then tune the thresholds to match human judgment. Also: allow "hybrid" classifications (e.g., "debugging + building") if multiple conditions are near thresholds.

---

#### P1-5: Tool chain analysis may produce noisy results

**Issue**: The plan filters bigrams to `count >= 5` and takes top 30 trigrams by count.

**Noise sources**:
- **Read → Read → Read**: This will be a top trigram for any session with heavy file exploration, but it's not actionable (it just means "reading multiple files").
- **Bash → Bash → Bash**: Same issue — normal for git workflows (`git status` → `git diff` → `git log`).
- **Tool → Error → Retry**: The plan detects retries, but does it distinguish between "same tool, same file, same error" (bad) vs "same tool, different file" (fine) vs "same tool, same file, different approach" (learning)?

**Recommendation**: Add filters to tool chain analysis:
- **Exclude self-transitions from bigrams** unless they represent retries (e.g., Read → Read is noise, but Edit → Edit on the same file is interesting).
- **Annotate trigrams with context**: "Read → Bash (cat) → Bash (grep)" is different from "Read → Bash (git status) → Bash (git diff)" — the former is a workflow problem, the latter is normal git usage.
- **Group Bash by command**: Instead of treating all Bash calls as one tool, extract the first word of the command (e.g., `git`, `pytest`, `cat`) and use that for chain analysis. This will surface patterns like "cat → grep" (should use Grep tool) vs "git → git" (normal).

---

### P2 Findings (Polish)

#### P2-1: Dashboard has no guidance on "what to do about it"

**Issue**: The dashboard will show rich visualizations (Sankey, heatmap, trend charts), but it doesn't explain what the user should *change*.

**Example**: User sees a Sankey diagram with a thick "Read → Bash → Bash" flow. They think "okay, that's happening a lot" but don't know:
- Is this bad?
- Which Bash calls are the problem?
- What should I write in CLAUDE.md to fix it?

**Recommendation**: Add a "Recommendations" panel to the dashboard that interprets the data and suggests CLAUDE.md rules. For example:
- "You have 42 'Read → Bash (cat)' transitions — add to CLAUDE.md: 'Never use cat via Bash — use Read tool instead.'"
- "Grep retries average 3.8x in debugging sessions — you may need better glob patterns. Add examples to AGENTS.md."

This requires the skill to generate recommendations, not the dashboard (dashboard is static HTML/JS). So the skill should write a `recommendations.json` file alongside `analysis.json`, and the dashboard can display it.

---

#### P2-2: Source comparison assumes user knows what "source" means

**Issue**: The dashboard will show "claude-code vs codex-cli" comparison, but many users won't know:
- What "codex-cli" is (they may have never used it)
- Why the comparison matters (what insights does it provide?)
- How to act on differences (if codex-cli has lower error rates, does that mean Claude Code is bad?)

**Recommendation**: Add tooltips/explanations to the source comparison section:
- "Source: The AI client you used. Claude Code is the desktop app. Codex CLI is the terminal agent (codex)."
- "Why compare? Different tools may trigger different workflows. If one has lower error rates, check what you do differently in that tool."

---

#### P2-3: Trends section lacks context for "is this good?"

**Issue**: The weekly trends chart will show a stacked area of tool usage and an error rate line. But without context, the user can't tell if:
- "Error rate dropped from 12% to 8%" is good (yes, improvement) or still bad (8% is high for some tools)
- "Bash usage went from 60% to 45%" is progress (if they were asked to reduce it) or just noise (if bash is fine for this project)

**Recommendation**: Add a "Community Baseline" overlay to the trends chart (if community sharing is enabled). Show the community's average error rate as a dotted line, so the user can see "I'm at 8%, community average is 5%, I still have room to improve."

---

#### P2-4: Projects table doesn't explain "why does this project have high errors?"

**Issue**: The projects table will show per-project error rates, but clicking a project only expands the tool breakdown. It doesn't explain *what sessions* had the errors or *what patterns* caused them.

**Recommendation**: Make the project table rows clickable to filter the entire dashboard to that project. When clicked:
- All charts update to show only that project's data
- Session classification, tool chains, and retry patterns are scoped to that project
- A "Back to all projects" button resets the view

This turns the dashboard into a drill-down tool, not just a summary.

---

#### P2-5: Time patterns heatmap may mislead users

**Issue**: The heatmap will show "events per hour per day" colored by density. But:
- **Timezone assumption**: If the user works across timezones (travels, remote team), the heatmap will show a blurred pattern that doesn't reflect actual work hours.
- **Sparse data**: If the user only has 10 sessions, the heatmap will have mostly empty cells, making it look like they work 2 hours/week (when really they just haven't used Claude Code much).

**Recommendation**: Add a data quality warning to the time patterns section:
- "This heatmap is based on X events over Y days. For accurate patterns, run at least 30 sessions across 3+ weeks."
- "Timezone: All times shown in [system timezone]. If you travel frequently, this heatmap may not reflect your actual schedule."

---

## Answers to Specific Questions

### 1. Dashboard information hierarchy — are the 6 sections in the right priority order?

**No.** The current order prioritizes descriptive metrics (session classification, trends, time patterns) over diagnostic ones (retry patterns, tool chains). See **P0-1** above.

**Recommended order**:
1. Retry Patterns (diagnostic)
2. Tool Chains (diagnostic)
3. Overview (contextual KPIs + session classification)
4. Projects (drill-down)
5. Trends (historical context)
6. Source Comparison (only if multiple sources)
7. Time Patterns (curiosity)

---

### 2. Does the skill narrative priority match what a user would find most actionable?

**No.** The current narrative order (session classification → tool chains → source comparison → time patterns → trends → anomalies) buries the actionable findings. See **P0-2** above.

**Recommended narrative order**:
1. Retry patterns
2. Tool chain problems
3. Session classification (as context)
4. Trends (if showing improvement/regression)
5. Source comparison (only if multiple sources)
6. Time patterns (only if they reveal a specific problem)

---

### 3. What happens when there's no data for a section?

**Currently undefined.** The plan doesn't specify empty states. See **P0-3** above.

**Recommendation**: Define empty states for every section, with helpful messages that explain why the section is empty and what the user needs to do to populate it.

---

### 4. Is "copy file + start python server + open browser" too manual?

**Yes, it's too manual for the default flow.** See **P1-1** above.

**Recommendation**: Make the skill output text-first (actionable findings in chat), with the dashboard as an optional visual deep-dive. The skill can auto-start the server and open the browser if the user says "show dashboard" or "visualize."

---

### 5. How does a user LEARN that these features exist?

**Currently unclear.** The plan suggests "if events.jsonl has >1000 events, suggest running deep analysis" — but this is a one-time suggestion. See **P1-2** above.

**Recommendation**: Make deep analysis the default for power users (>500 events), or add an explicit trigger like `/tool-time --deep`. Also: mention it in the simple analysis output ("For richer insights, try 'deep analysis'").

---

### 6. Is the "deep analysis" vs regular trigger clear enough?

**No.** The plan doesn't define what "full date range" means (all time? 90 days? since project creation?). See **P1-3** above.

**Recommendation**: Define "full date range" as last 90 days and document it. Add flags for custom ranges. Warn if data volume exceeds reasonable visualization limits.

---

## Summary of Recommendations

### High Priority (P0)
1. **Reorder dashboard sections** to prioritize retry patterns and tool chains over session classification and time patterns.
2. **Flip skill narrative priority** to start with retry patterns and tool chains, not session classification.
3. **Define empty states** for all dashboard sections, with helpful messages explaining why they're empty and what to do.

### Medium Priority (P1)
4. **Make skill output text-first** — surface actionable findings in chat, offer dashboard as optional deep-dive.
5. **Auto-detect data volume** and switch to deep analysis mode for power users (>500 events).
6. **Define "full date range"** as last 90 days and warn if data volume exceeds visualization limits.
7. **Tune session classification thresholds** using real data (manually label 20-30 sessions, adjust thresholds to match).
8. **Filter tool chain noise** — exclude self-transitions, group Bash by command, annotate trigrams with context.

### Polish (P2)
9. **Add recommendations panel** to dashboard (interprets data, suggests CLAUDE.md rules).
10. **Add tooltips** to source comparison explaining what "source" means and why it matters.
11. **Add community baseline overlay** to trends chart (if sharing enabled).
12. **Make projects table clickable** to filter entire dashboard to one project (drill-down).
13. **Add data quality warnings** to time patterns heatmap (timezone, sparse data).

---

## Evidence Quality Assessment

**Data-backed**:
- The plan's session classification thresholds (error_rate > 0.15, bash_pct > 0.4, etc.) are not validated against real user data — they're educated guesses.
- The claim that "330K events takes <1s in Python" is likely true for in-memory processing, but the plan doesn't account for disk I/O, project filtering, or D3.js rendering limits.

**Assumption-based**:
- The assumption that users will manually run `serve.sh` and open a browser (instead of expecting inline results in chat).
- The assumption that session classification will be accurate enough to inform action (without validating thresholds on real sessions).
- The assumption that users will understand how to act on Sankey diagrams, heatmaps, and trend charts without guidance.

**Unresolved questions**:
- What does "full date range" mean for deep analysis? (All time? 90 days? Since project creation?)
- What happens if a user has 100,000+ events? (Performance, visualization limits?)
- How will users discover "deep analysis" exists if they've never invoked it before?
- Do the session classification thresholds actually match how users would manually label sessions?

---

## Final Assessment

**Overall product direction: Strong, but execution needs user-centric adjustments.**

The deep analytics engine adds substantial value for power users who want to understand *why* their workflows have specific patterns, not just *what* the patterns are. However, the current plan prioritizes technical completeness (all dimensions implemented, all charts rendered) over user value delivery (actionable findings surfaced first, visuals optional).

**Key product risks**:
1. **Buried value**: Users won't discover deep analysis, or will try it once and find the dashboard overwhelming (too many charts, unclear what to do).
2. **Empty sections**: Users with sparse data (new projects, infrequent usage) will see mostly empty dashboard sections and assume it's broken.
3. **Manual friction**: Requiring `serve.sh` + browser breaks the mental model of "I asked the agent, the agent shows me results."

**Key opportunities**:
1. **Text-first, visuals-optional**: Surface actionable findings in chat, offer dashboard for exploration.
2. **Auto-detect mode**: Switch to deep analysis for power users (>500 events), simple analysis for new users.
3. **Recommendations panel**: Interpret the data and suggest CLAUDE.md rules (turn insights into action).

**Recommendation**: Implement the plan as specified, but prioritize P0 and P1 fixes before releasing to users. The deep analytics engine should enhance the current /tool-time skill's strengths (problem-focused, actionable, concise), not replace them with a data science dashboard that requires interpretation.
