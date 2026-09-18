# Content Automation / Pickle Batch

This file is the project-level entry point for any coding agent (Claude Code, Codex, or otherwise) working in this repository. Keep it thin: enough to orient a new agent and stop it from making a bad change, with pointers into the deeper deterministic documentation for anything task-specific. Do not turn this into a repository encyclopedia — detailed domain knowledge belongs in the docs this file points to, not copied in here.

## Project Purpose

Automates short-form video content scheduling and publishing: ingests recorded videos, transcribes and captions them locally, schedules them against a Google Calendar-backed content calendar, and publishes them to TikTok on schedule, unattended.

- **Internal/backend project name:** Content Automation — the name of this repository, its Python package (`content_automation`), its database, and its module/table naming throughout.
- **Public product name:** Pickle Batch — the external brand shown in `web/` (the Next.js frontend) only.

Do not rename internal modules, database tables, or backend concepts to "Pickle Batch" merely because that's the external brand — the two names are deliberately kept separate (see `CHANGELOG.md`'s 2026-09 brand-rename entry). Only rename something backend-side if a specific task explicitly calls for it.

## Current Product Stage

- **Milestone 2.1 (TikTok scheduled-publishing reliability): complete.** Due detection, atomic claiming, worker execution, crash recovery, retry/backoff, missed-schedule handling, token lifecycle/auto-refresh, and asynchronous publish reconciliation are all implemented and validated against the real TikTok Sandbox account — see `docs/evaluations/scheduling/`.
- **Milestone 3 (productization) is underway. Milestone 3.0 (this backend package refactor — behavior-preserving only, no new functionality) is complete** — see `docs/evaluations/productization/milestone-3.0-backend-package-refactor.md`.
- **Next up: Milestone 3.1 (hosted architecture boundary).** As of this writing, no hosted service layer exists yet: no FastAPI (or other web framework) app, no Postgres/Supabase, no user authentication, no object storage, no hosted scheduler. Do not introduce any of these, and do not change existing scheduling/publishing/retry/reconciliation/auth behavior, unless the task you were given explicitly asks for it. Check `CHANGELOG.md`'s most recent entries and `PROJECT_STATE.md` before assuming what "current" means — both change often.

## Instruction Hierarchy

```text
global policies (~/.agents/)
    ↓
this file (AGENTS.md)
    ↓
task-relevant project docs (docs/decisions/, docs/evaluations/, PROJECT_STATE.md, README.md)
    ↓
implementation
```

Global policy always applies first and is never overridden by this file. This file adds only the Content-Automation-specific context needed to apply those policies correctly here — it does not restate them.

## Global Policies

Follow the shared, vendor-neutral policies before changing anything in this repository:

- `~/.agents/CODING.md` — modularity, existing-pattern conventions.
- `~/.agents/DOCUMENTATION.md` — code comments, changelog policy (`CHANGELOG.md` here), context boundaries.
- `~/.agents/GIT.md` — commit workflow and granularity.
- `~/.agents/SECURITY.md` — secrets/credential handling.
- `~/.agents/VERIFICATION.md` — required testing/verification before declaring work complete.

## Repository Structure

```text
src/content_automation/   runtime application package (import as content_automation.*)
  config.py                 shared configuration (REPO_ROOT, DB_PATH, TikTok/Calendar/Claude settings)
  media/                     video inspection, transcription, captions, classification, the ingestion pipeline
  scheduling/                 due-post selection, worker, retry/backoff, reconciliation, crash recovery
  publishing/                   platform-neutral Publisher contract; publishing/tiktok/ (auth, publisher)
  persistence/                    ContentStore (SQLite)
  calendar/                        Google Calendar generation/management, posting cadence (cadence.py)

cli/                       thin CLI entry points — `python3 cli/<name>.py` — argument parsing only;
                            all logic lives in the package above and is directly importable by a future service
tools/evaluation/          engineering evaluation tooling (classifier/transcription benchmarking) —
                            not runtime code; distinct from the evaluation/ data directory below

tests/                     automated verification (pytest)
docs/decisions/            ADRs — genuine architecture decisions only
docs/evaluations/          recorded validation evidence, organized by domain (scheduling/, tiktok/, productization/)
evaluation/                golden dataset for tools/evaluation/evaluate_classifier.py (gitignored contents)
data/, content/            real SQLite DB + incoming/processed/failed video files — real business data
web/                       public Next.js frontend ("Pickle Batch") — separate Node toolchain, no shared code
                            with the Python backend in either direction; its own AGENTS.md is Next.js
                            tooling-generated (framework version notes), not a hand-authored project policy
```

An editable install (`pip install -e .`, via `pyproject.toml`) makes `content_automation` importable from anywhere in the venv. Never add a `sys.path` mutation as a substitute for this.

## Source Package Boundaries

Preserve these boundaries — place new runtime code in the package matching what it does, not which table it touches, and do not add new files at repository root:

- **`media/`** — anything about a video file itself or turning it into scheduling-ready metadata: inspection, transcription, captioning, classification, the ingestion pipeline.
- **`scheduling/`** — anything about *when* and *whether* a platform post executes: slot matching, due-post selection, atomic claiming, the worker, retry classification/backoff, reconciliation, crash recovery. Not to be confused with `calendar/cadence.py` (see next bullet) — different concern that happens to share the word "scheduling."
- **`publishing/`** — the platform-neutral `Publisher` contract and platform-specific implementations (`publishing/tiktok/`). A future second platform gets its own `publishing/<platform>/`, not a branch inside `tiktok/`.
- **`persistence/`** — `ContentStore` and all direct SQLite access. No other module should touch the database directly.
- **`calendar/`** — Google Calendar integration and posting-cadence math (`cadence.py` — posting dates, pillar allocation; unrelated to `scheduling/`'s due-post concerns above despite the name).
- **`cli/`** — thin wrappers only (argument parsing, constructing the real publisher/store, printing the result). If you find yourself adding a conditional or a loop to a `cli/*.py` file, that logic almost certainly belongs in the package instead.
- **`tools/evaluation/`** — engineering benchmarking tooling, not part of the runtime application; safe to have heavier/optional dependencies (`requirements-eval.txt`) that the runtime package should never need.

## Coding / Refactor Rules

Follow `~/.agents/CODING.md`. In addition, specific to this repository's own established conventions (see `docs/evaluations/` for examples of the pattern in practice):

- Prefer the smallest existing abstraction that already solves the problem (e.g. reuse `ContentStore.update_platform_post_if_unchanged`'s optimistic-concurrency pattern rather than inventing a new one) over adding a new one.
- A one-pass, no-daemon, no-busy-loop shape is the established convention for anything that "runs periodically" (`worker.py`, `reconciliation.py`, `crash_recovery.py`) — a future scheduler is expected to invoke these repeatedly, not for them to loop internally.
- Timestamp conventions are deliberate and inconsistent on purpose — `scheduled_at` is naive local time (matches Google Calendar's stored convention); `updated_at`/`published_at`/`next_status_check_at` are aware UTC. Check which convention a field already uses before adding logic that compares it against `now`; do not silently normalize one into the other.

## Testing Requirements

Follow `~/.agents/VERIFICATION.md`. Specific to this repository:

- `python3 -m pytest` — current baseline: **596 passing**. Check `CHANGELOG.md`'s most recent entries for the current count before trusting this number; it moves often.
- No live TikTok API calls, no real Google Calendar writes, and no real-DB (`data/content.db`) mutation from automated tests — every test that needs a `ContentStore` uses `tmp_path`; every test that needs a TikTok/Calendar client mocks it. If a task requires touching the real TikTok account or real Calendar, treat that as a hard-to-reverse external action requiring explicit user confirmation first (see `~/.agents/SECURITY.md` and this repository's own pattern in `docs/evaluations/scheduling/milestone-2.1.9-real-unattended-tiktok-validation.md` for how that confirmation was sought and scoped).
- Never delete or weaken an existing test to make a refactor pass. If a test count changes, the reason must be explainable (see Milestone 3.0's evaluation doc for what that explanation should look like).

## Documentation / Evaluation Requirements

Follow `~/.agents/DOCUMENTATION.md` for changelog/comment policy — not restated here. Specific to this repository:

- `docs/decisions/` — ADRs for genuine architecture decisions only. Most milestone work is *not* an ADR; do not create one unless the work genuinely changes an architectural direction.
- `docs/evaluations/<domain>/milestone-X.Y-<name>.md` — the canonical record of validation evidence for a completed milestone (what was built, how it was tested, real-data guardrail results). Read the relevant prior one before starting related work; write a new one when completing milestone-shaped work, following the existing ones' structure.
- `PROJECT_STATE.md` — current architecture/behavior narrative. `README.md` — setup, run commands, project structure. Both are live documents; update them when a change makes them wrong, not just when a milestone doc is written.
- Do not rewrite historical `docs/evaluations/` records to match current file paths after a refactor — they are evidence of what was true when they were written. Add a short forward-pointing note instead (see Milestone 3.0's own doc, and the note it added to `PROJECT_STATE.md`'s "Directory Ownership" section, for the pattern).

## CLI / Runtime Rules

- Supported invocation: `python3 cli/<name>.py [args]`, from the repository root, inside the project's `.venv`. Do not invoke a package module's file path directly as a script.
- `cli/*.py` files exist so a future hosted service can import the same package functions directly instead of shelling out to these scripts — keep that true by never putting logic only the CLI can reach.

## Data and Credential Safety

Follow `~/.agents/SECURITY.md`. Specific to this repository's credentials, none of which are ever committed:

- `data/content.db` — the real production SQLite database. Real business data. Read real rows read-only when investigating; never mutate them outside an explicitly-confirmed, explicitly-scoped task.
- `~/.config/content-calendar/` — TikTok OAuth token (`tiktok_token.json`), Google Calendar OAuth token/client secrets. Never print token values to terminal output or into a file.
- `.env` — `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`, `ANTHROPIC_API_KEY`. Never commit, never echo into logs or docs.
- `~/growth_agency/credentials/service-account.json` — shared Google service account, used only for the explicit `--calendar <id>` override path, not the normal OAuth path.

## Scope Guardrails

Do not introduce, unless a task explicitly asks for it:

- A hosted service layer: FastAPI or any other web framework, Postgres/Supabase, user authentication/ownership, object storage, a hosted/cron scheduler.
- New publishing platforms, new TikTok API behavior, or changes to already-validated scheduling/retry/reconciliation semantics (Milestone 2.1's own scope guardrails still apply post-3.0 — see the relevant `docs/evaluations/scheduling/` records before changing any of that behavior).
- Database schema redesign or timestamp-convention normalization (see "Coding / Refactor Rules" above for why the current inconsistency is deliberate).
- Changes to `web/` as a side effect of backend work — it has no shared code with the Python backend in either direction.

## Where to Find More Detailed Instructions

- `README.md` — setup, run commands, current project structure.
- `PROJECT_STATE.md` — current architecture and behavior, by area (routing strategy, captions, TikTok publishing, credentials, testing, etc.).
- `docs/decisions/` — why the architecture is shaped the way it is (ADRs).
- `docs/evaluations/<domain>/` — what was built and how it was validated, per milestone.
- `CHANGELOG.md` — the most current, dated record of what actually changed and why; check it first when "current state" matters and this file might be stale.
