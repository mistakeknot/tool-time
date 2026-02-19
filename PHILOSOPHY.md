# tool-time Philosophy

## Purpose
Tool usage analytics for Claude Code. Tracks tool patterns via hooks, detects inefficiencies, and offers community comparison with anonymized data.

## North Star
Turn tool telemetry into behavior change: prioritize insights that improve workflows over vanity analytics.

## Working Priorities
- Actionable telemetry
- Workflow optimization
- Privacy-safe analytics

## Brainstorming Doctrine
1. Start from outcomes and failure modes, not implementation details.
2. Generate at least three options: conservative, balanced, and aggressive.
3. Explicitly call out assumptions, unknowns, and dependency risk across modules.
4. Prefer ideas that improve clarity, reversibility, and operational visibility.

## Planning Doctrine
1. Convert selected direction into small, testable, reversible slices.
2. Define acceptance criteria, verification steps, and rollback path for each slice.
3. Sequence dependencies explicitly and keep integration contracts narrow.
4. Reserve optimization work until correctness and reliability are proven.

## Decision Filters
- Does this reduce ambiguity for future sessions?
- Does this improve reliability without inflating cognitive load?
- Is the change observable, measurable, and easy to verify?
- Can we revert safely if assumptions fail?

## Evidence Base
- Brainstorms analyzed: 5
- Plans analyzed: 5
- Source confidence: artifact-backed (5 brainstorm(s), 5 plan(s))
- Representative artifacts:
  - `docs/brainstorms/2026-01-30-community-analytics-brainstorm.md`
  - `docs/brainstorms/2026-02-01-ecosystem-observatory-brainstorm.md`
  - `docs/brainstorms/2026-02-14-deep-analytics-engine-brainstorm.md`
  - `docs/plans/2026-01-29-feat-agent-driven-analysis-plan.md`
  - `docs/plans/2026-01-30-feat-community-analytics-dashboard-plan.md`
  - `docs/plans/2026-02-01-feat-ecosystem-observatory-plan.md`
  - `docs/plans/2026-02-14-deep-analytics-engine-design.md`
  - `docs/plans/2026-02-14-deep-analytics-implementation-plan.md`
