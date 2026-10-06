# Content Automation Implementation Roadmap

This directory holds human-approved implementation briefs for Autobuild. It is
an execution index, not a second product roadmap: current product status stays
in [`PROJECT_STATE.md`](../../PROJECT_STATE.md), durable decisions stay in
[`docs/decisions`](../decisions), and validation history stays in
[`docs/evaluations`](../evaluations).

## Layout

```text
docs/roadmap/
  README.md
  <milestone>/
    <implementation-id>-<short-name>.md
```

Use milestone names that match the existing project sequence, for example
`milestone-4.1/`. Each brief must use Autobuild's installed
`templates/implementation-brief.md` contract.

## Planning Workflow

From the repository root, start `codex` or `claude` and ask it to use the
implementation-planning workflow. The planner inspects the relevant project
state, decisions, architecture and code, then works through scope, risks,
dependencies, acceptance criteria and autonomy with you.

The planner writes nothing executable until you explicitly approve the plan.
After approval it creates the milestone directory and brief, updates the index
below, runs `autobuild brief`, reports the autonomy gate and stops. It never
starts `autobuild run`.

## Approved Implementations

No approved Autobuild briefs yet.

## Handoff

Use the exact path reported by the planner:

```bash
autobuild brief docs/roadmap/<milestone>/<brief>.md
```

Content Automation has `require_clean_git_before_start: true`. Review and
commit the approved roadmap files after validation; the planner does not commit
them. Then:

```bash
autobuild run docs/roadmap/<milestone>/<brief>.md --project . --dry-run
autobuild run docs/roadmap/<milestone>/<brief>.md --project .
```
