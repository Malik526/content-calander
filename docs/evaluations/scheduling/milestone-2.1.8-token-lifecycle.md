# Milestone 2.1.8 — TikTok Token Lifecycle and Automatic Refresh

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-18.

## Purpose

Answer: if a TikTok access token expires before a scheduled post becomes due, can Pickle Batch silently refresh credentials and keep publishing without user interaction? The product rule this milestone proves:

> A creator can authorize TikTok once, leave Pickle Batch alone for days, and scheduled publishing can keep working because Pickle Batch maintains the authorization itself.

## Phase 1 — Investigation: most of this contract already existed

Traced `tiktok_auth.py`, `tiktok_publisher.py`, `publisher.py`, `publish_tiktok.py`, `worker.py`, `crash_recovery.py`, `config.py`, and the real token cache before writing anything. Confirmed, not assumed:

- **One centralized token path already existed.** Every TikTok API caller (`query_creator_info`, `publish`'s init call, `get_status`) goes through `TikTokPublisher._headers()`, which calls `tiktok_auth.get_access_token()` — never a direct token-file read anywhere else in the codebase (grepped `TIKTOK_TOKEN_PATH`/`load_token` usage).
- **`get_access_token()` already implemented the token-manager contract** the brief asks for: return the cached token if still valid, silently refresh (and persist) if expired or inside a skew window, raise an actionable error if the refresh token itself has also expired.
- **Absolute, aware-UTC expiry timestamps already existed** (`access_token_expires_at`/`refresh_token_expires_at`, computed once at persistence time in `_token_response_to_stored`) — not re-derived from process start time, and already the opposite convention from `scheduled_at`'s naive-local-time (correctly never conflated — see Milestone 2.1.7's own finding about that boundary elsewhere in the codebase).
- **A refresh safety skew already existed** (`_REFRESH_SKEW`, previously a hardcoded 5-minute `timedelta`, not yet env-configurable).
- **Refresh-token rotation was already handled correctly**: `_token_response_to_stored` unconditionally persists whatever `refresh_token` TikTok's response contains, satisfying TikTok's documented "you must use the newly-returned token if the value is different" requirement without any special-casing.
- **Two real gaps existed**, not previously visible because nothing had exercised them:
  1. Every TikTok-auth failure — a network blip reaching the token endpoint, TikTok's token endpoint returning a 5xx, and an actually-revoked/expired refresh token — collapsed into the same undifferentiated `TikTokAuthError`, which `tiktok_publisher._headers()` then hardcoded into `PublishError(reason_code="AUTH_ERROR")`, unconditionally terminal per `retry_classification.py`. A transient failure to *reach* TikTok while refreshing therefore burned zero retry budget and failed the post immediately — identical treatment to an actually-revoked refresh token.
  2. No protection existed against two separate processes (e.g., an overlapping cron invocation and a manual `worker.py` run — `worker.py` is a plain CLI process, not a supervised singleton) concurrently observing the same expired token and both calling TikTok's refresh endpoint, a real risk given TikTok may rotate the refresh token on every call.

Conclusion of Phase 1: this milestone's real work was closing those two specific gaps, not building the token manager from scratch.

## Phase 2 — Official TikTok Behavior Verified

Checked against TikTok's current OAuth developer documentation (`developers.tiktok.com/doc/oauth-user-access-token-management`) rather than assuming the existing code's values were still current:

- **Access token lifetime:** 24 hours (`expires_in: 86400`) — matches the existing code/tests.
- **Refresh token lifetime:** 365 days (`refresh_expires_in: 31536000`) — matches.
- **Refresh request fields:** `client_key`, `client_secret`, `grant_type=refresh_token`, `refresh_token` — matches `refresh_access_token()` exactly.
- **Response fields:** `access_token`, `expires_in`, `refresh_token`, `refresh_expires_in`, `open_id`, `scope`, `token_type` — matches what's persisted (`token_type` isn't stored; not needed, since the Bearer scheme is hardcoded at the one call site that uses it).
- **Refresh-token rotation:** confirmed explicitly documented — "The returned `refresh_token` may be different than the one passed in the payload. You must use the newly-returned token if the value is different." The pre-existing `_token_response_to_stored` behavior was already correct.
- **Invalid/expired/revoked refresh-token error signal:** **not distinctly documented.** TikTok's docs show exactly one error example for this endpoint — `"error": "invalid_request"`, HTTP 400, for malformed request parameters — and do not publish a separate code for a revoked/expired refresh token specifically. This directly shaped Phase 9's design below: rather than inventing an undocumented error-code match (e.g. guessing `invalid_grant`, which is not confirmed anywhere in TikTok's docs), classification uses HTTP status alone, the one signal TikTok's docs actually support.

## Phase 3 — Token Manager Contract

Unchanged in shape: `tiktok_auth.get_access_token()` remains the one function every TikTok API caller goes through. No new class/abstraction was introduced — the brief's `get_valid_access_token()`/`TikTokTokenManager` sketch already existed as a plain module function, and there was no meaningful duplication to consolidate (`TikTokPublisher._headers()` was already the only caller).

## Phase 4 — Expiration Representation

Unchanged: `access_token_expires_at`/`refresh_token_expires_at` were already persisted absolute aware-UTC isoformat timestamps, computed once at persistence time rather than re-derived from process start. Confirmed distinct from (and correctly never mixed with) `scheduled_at`'s naive-local-time convention.

## Phase 5 — Refresh Safety Window

`_REFRESH_SKEW` moved from a hardcoded 5-minute constant in `tiktok_auth.py` into `config.TIKTOK_TOKEN_REFRESH_SKEW_SECONDS` (default `300`, env-overridable via `CONTENT_CALENDAR_TIKTOK_TOKEN_REFRESH_SKEW_SECONDS`) — centrally configured, matching the exact pattern `RETRY_BACKOFF_MINUTES`/`PLATFORM_POST_STALE_MINUTES` already established, rather than a magic number scattered in `tiktok_auth.py`.

## Phase 6 — Refresh Flow

Unchanged in mechanism (request shape, persistence, `chmod 0o600`, rotation handling), all confirmed correct in Phase 1/2. What changed: `_post_token_request`'s failure paths now raise structured `TikTokAuthError` (see Phase 9) instead of one undifferentiated exception type, so `refresh_access_token()` and its callers can tell transport failure from explicit rejection.

## Phase 7 — Concurrent Refresh Protection

**New.** `get_access_token()`'s refresh path now acquires an OS-level advisory lock (`fcntl.flock` on a new `TIKTOK_REFRESH_LOCK_PATH` file, `TIKTOK_TOKEN_PATH.with_name("tiktok_refresh.lock")` — same sibling-file pattern `TIKTOK_PENDING_AUTH_PATH` already used) before reading, refreshing, and persisting. After acquiring the lock, it **re-reads and re-checks freshness** (compare-and-reload) before making a network call: a caller that blocked on the lock while another was already refreshing observes that other caller's already-persisted token and returns it directly, without a redundant refresh request.

Deliberately a process-local/host-local file lock, not a distributed lock (Redis, etc.) — this repository's worker is a single-host CLI process by design (`worker.py`), and the brief is explicit that a process-local lock is the right scope for that architecture. `flock` is per-open-file-description (not per-process), so it correctly serializes both separate OS processes and separate threads within one process — verified directly: `test_concurrent_refresh_attempts_only_hit_tiktok_once` runs 5 real threads racing an expired token and asserts exactly one network call reached the fake token endpoint and all 5 threads observed the identical final token.

The fast path (token already valid) takes no lock at all — it's the overwhelmingly common case and a pure read with nothing to race on, so it was left unlocked for the common case.

## Phase 8 — Publisher Integration

Unchanged in shape (`TikTokPublisher._headers()` remains the single path every call — `query_creator_info`, `publish`'s init call, `get_status` — obtains its Bearer token through), verified explicitly by test: `test_worker_publish_silently_refreshes_and_all_calls_use_new_token` runs a real `TikTokPublisher` through `worker.run_due_posts_once` starting from an already-expired cached token, and asserts the refreshed token (not the stale pre-refresh one) was sent on all three downstream calls (creator_info, init, status) — exactly one refresh, zero calls carrying the stale token.

## Phase 9 — Authentication Failure Semantics

**The core fix.** `TikTokAuthError` gained the same structured `reason_code`/`http_status` shape `publisher.PublishError` already has (added in Milestone 2.1.6), defaulting to `reason_code="AUTH_ERROR"` (fail-closed, same philosophy as `retry_classification`'s unrecognized-code default). A new subclass, `TikTokReauthorizationRequiredError` (always `reason_code="REAUTHORIZATION_REQUIRED"`), marks the specific cases that definitively require a human to re-run `--authorize`:

- no token was ever saved (never authorized),
- the refresh token is already past its own locally-known `refresh_token_expires_at`,
- TikTok's token endpoint returns HTTP 4xx to a refresh request.

Per Phase 2's finding that TikTok does not publish a distinct error code for "refresh token is invalid/expired/revoked" specifically, the 4xx-vs-5xx HTTP status is the classification signal used (matching `tiktok_publisher._parse_response`'s own existing convention for the publish API) — a 4xx rejection of a refresh request means "do not keep auto-retrying this without a human," whether the precise cause is a revoked token or a malformed request, since neither resolves itself through more retries. A 5xx or network failure reaching the token endpoint stays a plain (non-reauthorization) `TikTokAuthError`, reusing the existing `NETWORK_ERROR` reason code (transport failures) or a new `AUTH_HTTP_ERROR` reason code classified via the existing `http_status` fallback (5xx retryable) — both already-understood shapes in `retry_classification.py`, not new special cases invented for auth specifically.

`retry_classification._TERMINAL_REASON_CODES` gained `"REAUTHORIZATION_REQUIRED"` explicitly (defense-in-depth: stays terminal even though this subclass may itself carry a 4xx `http_status` that would otherwise route through the ordinary fallback path) — verified by test that it's terminal even when constructed with a 5xx status, i.e. the explicit-list membership always wins over the fallback, exactly the same priority rule Milestone 2.1.6 established for every other code.

`TikTokPublisher._headers()` now propagates the caught exception's own `reason_code`/`http_status` onto the `PublishError` it raises, instead of hardcoding `"AUTH_ERROR"` for every case — the one-line change that makes the rest of this phase's classification actually reach the retry system.

## Phase 10 — Interaction with Worker Retry Logic

Verified end to end through the real `worker.run_due_posts_once` -> `publish_tiktok._schedule_retry_or_fail` path (Milestone 2.1.6), not just at the classification-function level:

- **`test_worker_transient_refresh_failure_schedules_retry_not_immediate_failure`**: a `requests.ConnectionError` during the refresh call results in `retry_scheduled=1`, `failed=0`, the row back at `PENDING` with `retry_count=1` and a `next_retry_at` set — composes with the normal backoff system exactly like any other transient publishing failure, which is the behavior the pre-2.1.8 hardcoded `"AUTH_ERROR"` classification did **not** provide (it would have gone straight to `FAILED`).
- **`test_worker_revoked_refresh_token_ends_failed_not_retried`**: TikTok's token endpoint returning HTTP 401 to a refresh attempt results in `failed=1`, `retry_scheduled=0`, the row `FAILED` with `retry_count` unchanged at 0 and `failure_reason` containing the actionable `--authorize` message — no retry budget burned on a state more retries can never fix.

## Phase 11 — Tests

New file `tests/test_token_lifecycle.py` (20 tests). Deliberately does not duplicate `tests/test_tiktok_auth.py`'s existing, still-passing-unchanged coverage of the basic valid/near-expiry/expired-refresh-token paths, PKCE/state, or the interactive/manual authorization flows (that file's full 35 tests all still pass, confirming Phase 3/4/6's "unchanged in shape" claims are not just assertions). This file's 20 tests cover: malformed-refresh-response safety, network/5xx-refresh-failure-is-not-reauthorization, 4xx-refresh-failure-is-reauthorization (both at the `refresh_access_token()` level and end-to-end through `get_access_token()`), successful refresh persisting both the new access token and its new expiry, refresh-token rotation replacing the old token, cache file permissions (`0o600`), no secret value ever appearing in an exception message, a refreshed token being visible to a wholly separate `get_access_token()` call (there is no in-memory manager state to go stale), concurrent-refresh protection (5 real threads, exactly one network call), `TikTokPublisher._headers()` propagating both failure classes correctly, `retry_classification.py`'s handling of `REAUTHORIZATION_REQUIRED` vs. transient auth codes, and the two end-to-end worker/retry-composition scenarios from Phase 10.

**A real bug in this test file itself was caught and fixed during authoring**: an early version of `test_worker_revoked_refresh_token_ends_failed_not_retried` didn't mock `tiktok_publisher.media.inspect_media`, so the test was actually failing on a real `ffprobe` `CORRUPT_MEDIA` error (the fixture's video file is fake bytes, not a real MP4) rather than exercising the intended auth path at all — and a loose substring assertion (`"revoked" in failure_reason.lower()`) produced a false-positive pass, because pytest's own `tmp_path` naming embeds the test function's name (`..._revoked_refresh_to0...`) into the file path inside the ffprobe error text. Fixed by mocking `media.inspect_media` (matching the sibling test that already did) and asserting the specific `TikTokReauthorizationRequiredError` message content instead of a loose keyword.

Focused: `pytest tests/test_token_lifecycle.py -v` -> **20 passed**.
Full suite: `pytest` -> **571 passed** (551 prior + 20 new), no regressions.

**Test-isolation finding (fixed in scope):** the existing `tests/test_tiktok_auth.py` fixture redirected `TIKTOK_TOKEN_PATH`/`TIKTOK_PENDING_AUTH_PATH` to `tmp_path` but not the newly-added `TIKTOK_REFRESH_LOCK_PATH` (a separate module-level constant, same pattern `TIKTOK_PENDING_AUTH_PATH` already uses and required the same treatment). Running the suite once before this fix left a stray empty `tiktok_refresh.lock` file in the real `~/.config/content-calendar/` directory — caught immediately, the file was deleted, and the fixture now redirects all three paths. Exactly the "a previous ad hoc auth test accidentally touched the real token cache; do not repeat that" risk the brief calls out — caught here rather than repeated.

## Phase 12 — Local Long-Horizon Simulation

Ran directly via `tiktok_auth.get_access_token()` against isolated temp token/lock files and a fake token endpoint, no pytest involved:

**Scenario A (expired access token, valid refresh token):** one call silently refreshes and returns the new access token; persisted state confirms it. **PASS.**

**Scenario B (refresh-token rotation):** two sequential forced refreshes; confirmed the second refresh request actually sent the first refresh's *rotated* token (`old_refresh` -> `rotated_refresh_1` -> `rotated_refresh_2`), not the original — proving rotation is honored across repeated refreshes, not just persisted-and-ignored. **PASS.**

**Scenario C (revoked authorization):** a 401 from the fake token endpoint raises `TikTokReauthorizationRequiredError` (`reason_code="REAUTHORIZATION_REQUIRED"`, `http_status=401`) with no publish ever attempted and no blind retry. **PASS.**

**Scenario D (concurrent refresh, 5 threads):** exactly 1 real refresh call reached the fake endpoint; all 5 threads observed the identical resulting access token; final persisted state matches. **PASS.**

All four: **PASS.**

## Phase 13 — Real Credential Validation

Read the real token cache (`~/.config/content-calendar/tiktok_token.json`) directly, reporting only safe metadata — no secret values were printed or logged at any point:

- cache exists: **yes**
- required fields present (`access_token`, `refresh_token`, `access_token_expires_at`, `refresh_token_expires_at`): **yes**
- `access_token_expires_at`: 2026-09-18T01:31:34Z — **already expired** as of this check (2026-09-18T05:23:39Z)
- `refresh_token_expires_at`: 2027-09-17T01:31:34Z — **not expired**
- refresh needed now: **yes**

Per the brief's own guardrail ("if there is any uncertainty around safely touching the real token cache, stop after read-only metadata inspection and report that live refresh should be validated in 2.1.9 instead"): a real refresh here would mutate live production credentials and make a real call to TikTok's OAuth endpoint with no prior explicit authorization for that specific external mutation in this conversation. **Stopped at read-only metadata inspection.** Live refresh validation (and, per the brief, a harmless authenticated read like `creator_info` afterward) is deferred to Milestone 2.1.9, alongside the real unattended end-to-end validation that milestone already covers. **No live refresh was performed. No TikTok publish was performed.**

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.8-token-lifecycle.md`. `tiktok_auth.py`'s module docstring was updated in place with a Milestone 2.1.8 section describing both fixed gaps. No prior evaluation doc required correction — Milestone 2.0's token/auth foundation and 2.1.6's retry-classification design were both already accurate; this milestone extends the latter's pattern (structured reason_code/http_status, explicit-list-before-fallback priority) to a case it hadn't yet been applied to.

## Conclusion

Most of this milestone's brief was already implemented — a single centralized token-acquisition path, absolute aware-UTC expiry tracking, a configurable refresh skew, and correct refresh-token rotation handling all predate this milestone. The real, previously-invisible gap was that every TikTok-auth failure collapsed into one unconditionally-terminal classification, so a transient failure to reach TikTok's token endpoint while refreshing was treated identically to an actually-revoked refresh token — fixed by giving `TikTokAuthError` the same structured `reason_code`/`http_status` shape `PublishError` already has, and a `TikTokReauthorizationRequiredError` subclass for the cases TikTok's own (thin) documentation supports distinguishing. The second gap — no protection against two processes concurrently refreshing the same soon-to-rotate token — is closed with a process/host-local `fcntl` lock and a compare-and-reload check, deliberately scoped to this repository's single-host CLI architecture rather than building distributed coordination it doesn't need yet. No ADR was warranted: nothing here is a new architectural decision — it extends `retry_classification.py`'s already-established reason_code/http_status/explicit-list-priority pattern to a code path (auth) it simply hadn't reached yet, and adds a lock using only the stdlib, matching this repository's existing "smallest local abstraction that solves the real problem" pattern throughout the scheduling milestones.

**Milestone 2.1.8: COMPLETE.**
