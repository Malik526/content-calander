# Milestone 3.0 — Backend Package / Source Organization Refactor

Structural refactor evidence — behavior-preserving only. No ADR: the target shape (responsibility-based package, thin CLI entry points, separate evaluation tooling) was the milestone's own stated goal, not a new decision this work arrived at independently. Recorded 2026-09-18.

## Objective

Reorganize the backend from a flat collection of 29 root-level Python files into a package structure that reflects the system's responsibilities, with zero behavior change: same scheduling/publishing/retry/reconciliation/crash-recovery/auth semantics, same DB schema, same timestamp conventions, same CLI behavior, same real database contents. Test baseline: 596 passed before, 596 passed after.

## Phase 1 — Inventory

Enumerated all 29 root-level `.py` files, their import graph (`grep` across every file for `^import`/`^from`), every CLI entry point (`argparse` + `if __name__ == "__main__"`), every `__file__`-relative path, and every test file's import/monkeypatch targets before moving anything.

**Real hazards found before moving anything** (this is why Phase 1 came first):

- `config.py` resolved `.env`, `data/calendar_state.json`, `data/content.db`, and `content/` via `Path(__file__).with_name(...)` — correct only while `config.py` sat directly at repo root. Moving it under `src/content_automation/` without fixing this would have silently pointed every one of those at the wrong directory.
- `migrate_relocated_paths.py` computed `_NEW_ROOT = Path(__file__).resolve().parent` — the *current repo root*, used to rewrite stored absolute paths in the real DB. Moving it under `cli/` without fixing this would have made a future re-run of this (idempotent, rarely-invoked, but real) migration script rewrite paths to `cli/` instead of the actual repo root.
- Three evaluation-tooling scripts (`evaluate_classifier.py`, `download_shofo_samples.py`, `evaluate_transcription.py`) all resolved the `evaluation/` dataset directory the same file-relative way.
- 34 of 35 test files imported root-level modules directly (`import content_store`, `from generate_calendar import build_schedule`, etc.) and 19 of them used `monkeypatch.setattr` on those same module objects — every one of those needed its patch *target* re-verified against where the patched code actually performs its attribute lookup at call time, not just its import line updated (see Phase 9).

## Phase 2/3 — Classification and Package Structure

Organized by responsibility, not by database table, per the brief's explicit preference:

```text
src/content_automation/
  config.py
  media/          inspection.py (was media.py), transcription.py, caption.py, classification.py, processing.py (was process_content.py's logic)
  scheduling/     slot_matcher.py, due_post_selector.py, platform_post_materializer.py, retry_classification.py,
                  worker.py, reconciliation.py, crash_recovery.py, publish_tiktok.py (all logic, CLI split out — see Phase 5)
  publishing/     publisher.py (platform-neutral contract) + tiktok/auth.py (was tiktok_auth.py), tiktok/publisher.py (was tiktok_publisher.py)
  persistence/    content_store.py
  calendar/       calendar_manager.py, generate_calendar.py (logic), cadence.py (was scheduling.py), prompts.py
```

## Phase 14 — Deviations From the Proposed Structure (and why)

Two deliberate departures from the brief's suggested tree, both found during Phase 1's investigation rather than assumed in advance:

1. **`scheduling.py` renamed to `cadence.py`.** The brief's proposed tree puts `worker.py`, `due_post_selector.py`, `reconciliation.py`, etc. under a package literally named `scheduling/`. The *pre-existing* root-level `scheduling.py` is a completely different concern — posting-date generation and pillar allocation for the Google Calendar generator (`generate_posting_dates`, `allocate_pillars`, `filter_future_dates`) — that happens to share the English word "scheduling." Moving it into the new `scheduling/` package unchanged would have created a real name collision and conflated two unrelated concerns under one name. Renamed to `content_automation/calendar/cadence.py` (it belongs with `generate_calendar.py`, which is its only real caller) — this is the one filename change in this milestone beyond what the brief's own tree already implied for the `tiktok/` subpackage.
2. **Evaluation tooling under `tools/evaluation/`, not `evaluation/`.** The repository already has a top-level `evaluation/` *directory* — a gitignored golden dataset for `evaluate_classifier.py` (transcripts, labels, the Shofo video corpus), documented by its own `README.md`. Following the brief's alternative suggestion (`tools/evaluation/`) instead of its primary suggestion avoids two unrelated things — benchmarking scripts and a private test-data corpus — sharing one directory name.

`tiktok_auth.py` was also given a thin `cli/tiktok_auth.py` wrapper even though the brief's Phase 5 list of six named CLI files didn't include it — for consistency with the same "package holds logic, `cli/` is a thin entry point" shape applied everywhere else, and because its own `main()`/`parse_args()` were exactly as separable as the other six.

Three one-off operational scripts (`backfill_platform_posts.py`, `migrate_relocated_paths.py`, `clear_calendar.py`) moved to `cli/` as complete files, **not** split into package logic + thin wrapper — they're rarely-invoked migration/ops tools, not part of the ongoing runtime surface a future FastAPI service would call directly, so splitting them would have added risk and files without a real consumer.

## Phase 5/6 — CLI Thinning and Import Strategy

Six files (`worker.py`, `reconciliation.py`, `crash_recovery.py`, `publish_tiktok.py`, `process_content.py`, `generate_calendar.py`) plus `tiktok_auth.py` were split: all logic (the functions a future service would call directly) moved into the package; `parse_args()`/`main()`/`if __name__ == "__main__"` became a thin `cli/<name>.py` wrapper importing from the package. Verified no logic was duplicated between a CLI file and its package counterpart — each CLI file's `main()` only orchestrates calls into functions defined exactly once, in the package.

Package-qualified imports throughout (`from content_automation.scheduling.worker import run_due_posts_once`, etc.) — no `sys.path` mutation in the final state. An editable install (`pyproject.toml`, `pip install -e .` in the existing `.venv`, `src/` layout via `setuptools.packages.find`) is what makes `content_automation` importable from anywhere, replacing `conftest.py`'s previous `sys.path.insert(0, repo_root)` hack, which was deleted as now-redundant. `pytest.ini` gained `pythonpath = cli tools/evaluation` so the one-off `cli/` ops scripts and `tools/evaluation/` scripts remain importable by their bare historical module name in tests (`import backfill_platform_posts as bpp`, `import evaluate_classifier as ec`, etc.) without needing every test file rewritten to reference them differently — they were never split, so there's nothing for those bare imports to be ambiguous about.

## Phase 8 — Path Safety (verified, not assumed)

- `config.py`: replaced four `Path(__file__).with_name(...)` call sites with one exported `REPO_ROOT = Path(__file__).resolve().parents[2]`, computed once for the file's new two-levels-deeper location. Verified directly: `python3 -c "from content_automation.config import REPO_ROOT; print(REPO_ROOT)"` -> `/home/malik/content-automation` (correct), and a real `python3 cli/process_content.py` run against the real (empty) `content/incoming/` printed `No videos found in /home/malik/content-automation/content/incoming` — the real path, not a path relative to `cli/`.
- `migrate_relocated_paths.py`: `_NEW_ROOT` now imports `REPO_ROOT` from `content_automation.config` instead of recomputing its own `Path(__file__).resolve().parent` — same single source of truth, correct regardless of this script's own location.
- `evaluate_classifier.py` / `download_shofo_samples.py` / `evaluate_transcription.py`: `DEFAULT_DATASET_DIR`/`DEFAULT_OUTPUT_DIR`/`DEFAULT_MANIFEST`/`DEFAULT_RESULTS` all switched to `REPO_ROOT / "evaluation" / ...`, verified to still resolve to the real repo-root `evaluation/` directory regardless of the scripts' new `tools/evaluation/` location.
- `TIKTOK_TOKEN_PATH`, `CALENDAR_OAUTH_TOKEN_PATH`, and friends were already `Path.home()`-based (not file-relative) — confirmed unaffected, no change needed.
- `data/content.db`, `content/incoming|processed|failed/`, `~/.config/content-calendar/` all confirmed to resolve to their real, unchanged locations via the CLI smoke validation below (Phase 11) and the real-DB guardrail (Phase 12).

## Phase 9 — Test and Mock-Target Repair

The real complexity in this milestone: for every test that monkeypatches a module attribute, the patch must target the module where the *code under test* performs that attribute lookup at call time — not merely wherever the test happens to import the name from. Splitting six files into package-logic + thin-CLI meant some previously-single-module patch targets now live in *two different files*.

Concrete example (`tests/test_calendar_target.py`, `tests/test_future_only_schedule.py`): `generate_calendar.now_in_config_timezone` is patched by tests that call `build_schedule()` directly — `build_schedule` is defined in the **package** module and resolves `now_in_config_timezone` from that module's own globals, so the patch must target `content_automation.calendar.generate_calendar`. But `generate_calendar.build_calendar_service`/`ContentStore` are patched by tests that call `main()` — `main()` lives in the **CLI** wrapper and resolves those names from *its own* globals (a separate, independently-patchable binding created by its own `from ... import` line), so those patches must target the CLI module instead. Both files now import both — `from content_automation.calendar import generate_calendar` (package, for `now_in_config_timezone`/`build_schedule`/`allocate_pillars`/`distribute_pillars` — all of which `build_schedule` itself references) and `import generate_calendar as generate_calendar_cli` (the thin wrapper, resolved via `pytest.ini`'s `pythonpath = cli`, for `main`/`parse_args`/`build_calendar_service`/`ContentStore`) — with each test's patches routed to the correct one. `calendar_manager` needed no such split: both the package and CLI import it as a *module* (`from content_automation.calendar import calendar_manager`), so patching the one shared module object affects both callers regardless of which file imported it — module-level imports share state; individually-named-function imports (`from x import func`) do not.

The same reasoning applied to `tests/test_fifo_process_content.py` and `tests/test_process_content_integration.py` for `process_content.PROCESSED_DIR`/`FAILED_DIR` (must target the package — `process_one()` reads them from its own globals) versus `process_content.ROUTING_MODE`/`INCOMING_DIR`/`ContentStore` in the one test that drives `main()` (must target the CLI wrapper).

No test assertion was weakened to make this pass — every case above was resolved by pointing the patch at the correct module, not by loosening what was being verified.

**One assertion genuinely changed, not just relocated**: `cli/tiktok_auth.py`'s manual-fallback message now says `python3 cli/tiktok_auth.py --exchange-code ...` (the real, current invocation) instead of the old `python3 tiktok_auth.py ...`. `tests/test_token_lifecycle.py`'s literal-string assertion was updated to match — a deliberate, correct change to what the message says, not an accidental behavior change (the substring-only assertions in `test_reconciliation.py`/`test_tiktok_auth.py` still passed unmodified, since `"tiktok_auth.py --authorize"` remains a substring of the new message either way).

## Phase 10 — Behavior-Preservation Validation

Ran the full suite after each major package move, not only at the end. Final result:

```text
596 passed, 0 failures
```

**Test count is unchanged (596 -> 596).** No test was deleted, added, or skipped because of this milestone — every test that existed before still exists and still exercises the same behavior; only import statements and (where genuinely necessary, per Phase 9) monkeypatch targets changed.

## Phase 11 — CLI Smoke Validation

All 10 entry points (`worker`, `reconciliation`, `crash_recovery`, `publish_tiktok`, `process_content`, `generate_calendar`, `tiktok_auth`, `backfill_platform_posts`, `migrate_relocated_paths`, `clear_calendar`) run as real subprocesses (`python3 cli/<name>.py --help`) from a fresh shell — confirming the editable install resolves `content_automation` imports correctly outside pytest, not only inside it. Also ran, for real (safe, no mutation possible):

- `worker`/`reconciliation`/`crash_recovery` against an isolated temp SQLite DB (`CONTENT_CALENDAR_DB_PATH` override) — all three printed clean zero-work summaries.
- `cli/process_content.py` against the real (currently empty) `content/incoming/` — printed `No videos found in /home/malik/content-automation/content/incoming`, the correct real path, proving `REPO_ROOT` resolves correctly for a real subprocess, not just under pytest.
- `cli/generate_calendar.py --month 12 --year 2026 --dry-run` — built and printed a real 17-post FIFO schedule with no Calendar/DB writes.

No live TikTok API calls. No video published. No real scheduling row touched.

## Phase 12 — Real DB Guardrail

Read `data/content.db` directly and read-only (`sqlite3 -readonly`) before and after the refactor. All 5 rows byte-identical throughout: video 1 `FAILED`, videos 2/3 `PUBLISHED` (`platform_post_id`s unchanged from Milestones 2.0/2.1.9), videos 4/5 `PENDING`. No new migration was introduced by this milestone (`_PLATFORM_POSTS_MIGRATION_COLUMNS` untouched) — schema identical. No row was modified. No TikTok API calls were made.

## Phase 13 — Documentation

`README.md`: every `python3 <script>.py` command updated to its real new invocation path (`cli/...` or `tools/evaluation/...`), plus a new "Project Structure" section giving the package/CLI/tooling layout at a glance. `PROJECT_STATE.md`: the same command-path updates, plus its "Testing" section's stale pass count/`conftest.py` description corrected, plus a short forward-pointing note added to the top of "Directory Ownership" (not a full rewrite of its 25 bullets — those describe ownership/responsibility, which is unchanged; only the physical path changed, and the note routes readers to `README.md`'s "Project Structure" and this record for that). `docs/decisions/` and `docs/evaluations/` (other than this record) were deliberately **not** rewritten — they are historical evidence, accurate at the time they were written; rewriting them to reference paths that didn't exist yet would misrepresent when those milestones actually ran, exactly the brief's own instruction.

## Additional Phase — Project-Level `AGENTS.md`

The repository had no project-specific agent-instruction file. Investigated before drafting: this environment's global policies (`~/.agents/CODING.md`/`DOCUMENTATION.md`/`GIT.md`/`SECURITY.md`/`VERIFICATION.md`) contain no existing convention for a project-level `AGENTS.md`'s structure or an established thin-harness/fat-skills pattern of their own — so `~/w2_pipeline/AGENTS.md` was read purely as a **structural** example (its shape: Required Global Policies -> Project Contracts -> Skills -> Scope Guard -> Data Handling -> Changelog), not for its W2/tax-domain content, none of which appears in the result (verified by grep). `~/growth_agency/AGENTS.md`/`CLAUDE.md` (this user's other established pattern: a thin adapter routing to deeper project docs, plain-path references rather than Claude-specific `@import` syntax, for portability) confirmed the same shape independently.

Created root-level `AGENTS.md` (138 lines — comparable to `w2_pipeline`'s 122): Project Purpose (Content Automation/Pickle Batch naming split), Current Product Stage (2.1 complete, 3.0 complete, 3.1 not started, explicit guardrails against assuming a hosted layer exists), Instruction Hierarchy, Global Policies (referenced, not duplicated), Repository Structure and Source Package Boundaries (the post-refactor tree from this milestone, with the `scheduling/`-vs-`cadence.py` naming distinction called out explicitly since it's the one place this repository's own naming could mislead), Coding/Testing/Documentation rules (each pointing at the relevant global policy file rather than restating it, adding only Content-Automation-specific specifics: the 596 test baseline, the naive-local-vs-aware-UTC timestamp convention, the real-TikTok-confirmation pattern), Data and Credential Safety (every real credential path in this repository, none committed), Scope Guardrails, and a final routing section to `README.md`/`PROJECT_STATE.md`/`docs/decisions/`/`docs/evaluations/`/`CHANGELOG.md`.

**Validation performed:**
- Every path/file referenced in `AGENTS.md` confirmed to actually exist (`docs/decisions/`, both cited `docs/evaluations/` records, `PROJECT_STATE.md`, `README.md`, `CHANGELOG.md`, `requirements-eval.txt`, `web/`, `pyproject.toml`, `data/content.db`, `~/.config/content-calendar/`, the service-account path) — one inaccurate claim caught and fixed in review (an early draft asserted `web/` "has its own AGENTS.md-equivalent" as if it were a hand-authored policy doc; it's actually Next.js tooling's auto-generated framework-version notice — corrected to say so explicitly).
- No stale bare `python3 <script>.py` command references (grepped) — every command example uses the real `cli/`/`tools/evaluation/` path.
- No W2/tax/IRS/Symage content (grepped) — confirmed structural-reference-only, per the constraint.
- Does not duplicate any global policy file's content — every global-policy section is a pointer plus repository-specific specifics only.
- Full test suite rerun after the doc-only addition: **596 passed**, unchanged.

## Tests Changed Only Because of Import/Path Movement

All 34 test files that imported a moved module needed their import statements updated (the mechanical majority of this milestone's test diff). 19 of those also needed monkeypatch-target verification per Phase 9's reasoning; most needed no target change (the module they patched wasn't split), a handful needed the dual package/CLI-module import described above, and exactly one assertion's expected string content changed (also Phase 9). `tests/test_transcript_metrics.py` needed zero changes — its only dependency (`transcript_metrics.py`) moved but was never imported by name inside the test in a way that broke (bare `import transcript_metrics as tm`, resolved via `pytest.ini`'s `pythonpath`).

## Conclusion

29 root-level files reorganized into a five-package `src/content_automation/` tree, a 10-file thin `cli/`, and a 4-file `tools/evaluation/`, with zero behavior change: the full test suite is 596 passed both before and after, every relocated CLI entry point runs correctly as a real subprocess, the real database is byte-identical, and two genuine path-resolution hazards (`config.py`'s and the evaluation scripts' `Path(__file__)`-relative defaults) were found and fixed *before* they could silently break anything, rather than discovered after the fact. The two deliberate structural deviations from the brief's proposed tree (`cadence.py`'s rename, `tools/evaluation/`'s location) were both driven by real naming collisions this investigation found, not by preference. A root-level `AGENTS.md` now gives any coding agent (not only the one that built this refactor) a thin, verified-accurate entry point into that structure, the current milestone stage, and the repository's own guardrails, without duplicating the global policies or the deeper docs it routes to. Milestone 3.1 (hosted architecture boundary) can now build on `content_automation.*` package imports directly — the explicit reason Phase 5 asked for thin CLI entry points in the first place — rather than needing to either shell out to scripts or untangle a flat file layout first.

**Milestone 3.0: COMPLETE.**
