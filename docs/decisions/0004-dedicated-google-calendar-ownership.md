# ADR-0004: Dedicated, App-Owned Google Calendar via OAuth

## Status

Accepted

## Context

Every prior milestone pushed generated events into whatever calendar `config.DEFAULT_CALENDAR_ID` (env `GOOGLE_CALENDAR_ID`, defaulting to `"primary"`) happened to name, authenticated as the shared `growth_agency` service account. `clear_calendar.py` deleted every event in a date range from that same calendar ID with no scoping beyond the range — a real risk to a human's actual primary calendar or to any other calendar the service account had been granted access to, and a risk shared with every other tool that uses the same service account key (the prospecting pipeline, and any future tool — see `credentials/README.md`).

This milestone's job was narrow: give the app a calendar boundary it fully owns, so "clear the schedule" can never touch anything the app didn't create.

## Decision

- **The app creates and owns exactly one dedicated secondary calendar**, display name `"Content Automation"` (`config.APP_CALENDAR_SUMMARY`), and normal operation (`generate_calendar.py`, `clear_calendar.py` with no `--calendar` flag) only ever targets that calendar. `"primary"` is never a default anywhere in this codebase anymore.
- **Ownership is via OAuth (the human account), not the service account.** Google's current guidance for apps that create secondary calendars is to authenticate as the intended human data owner rather than create the calendar as a service account — a service-account-created calendar is owned by the service account's own hidden identity, requiring an extra ACL-sharing step just to make it visible/writable by the human, and leaves calendar ownership somewhere the human doesn't directly control. OAuth sidesteps this: the calendar is created by, and belongs to, the same Google account that already sees it in their own Calendar app. `calendar_manager.py` uses `google-auth-oauthlib`'s `InstalledAppFlow` (same pattern already used for YouTube OAuth in `internal-tools/content-analytics`), caching a refresh token at `config.CALENDAR_OAUTH_TOKEN_PATH` (`~/.config/content-calendar/`) so only the very first real run needs an interactive browser consent.
- **The shared service account is kept, scoped down to the explicit `--calendar` override only.** `generate_calendar.py --calendar <id>` and `clear_calendar.py --calendar <id>` still authenticate via the service account exactly as before — unchanged code, unchanged risk profile, because the caller is explicitly naming a specific calendar they already understand, not relying on an implicit default. This is deliberately the *only* place the service account still touches Calendar in this tool: it was never right for creating/owning a new calendar (see above), and there was no reason to also migrate the "I know exactly what I'm targeting" advanced path.
- **Calendar identity is persisted, not name-derived.** `config.APP_CALENDAR_STATE_PATH` (`data/calendar_state.json`) stores `{"calendar_id", "summary"}`. `calendar_manager.resolve_app_calendar()` reuses the persisted ID after verifying it's still accessible; if it's gone or access was revoked, it recovers by searching the account's own calendars for a `summary` match with `accessRole == "owner"` (never adopting a merely-shared calendar), and only creates a new one if neither works. A calendar is therefore created at most once per account, and display-name collisions can't cause the wrong calendar to be adopted as a fallback (owner-only filtering) — though display names still aren't unique in general, which is why persisted ID stays the primary path and search is only a recovery mechanism.
- **Clearing is scoped to what `content_slots` actually tracks, not a calendar scan.** `clear_calendar.py`'s default path deletes only `content_slots` rows in `OPEN` status and their exact `google_calendar_event_id` — never a broad date-range wipe of the calendar. `--all` extends this to `ASSIGNED` slots too, which requires resetting the referencing video (`assigned_slot_id = NULL`, `status = 'CLASSIFIED'`) before the slot row can be deleted (SQLite foreign-key enforcement on `videos.assigned_slot_id` would otherwise reject it) — a video's transcript/classification history is never touched, only its scheduling state. The calendar row itself is never deleted by either mode; only its events are.
- **Fail closed.** If the dedicated calendar can't be resolved and creation isn't allowed (`clear_calendar.py`'s default path passes `create_if_missing=False`), `resolve_app_calendar` raises rather than falling back to any other calendar. If OAuth can't be completed (missing client secrets, failed refresh, failed consent), `build_oauth_calendar_service` raises a clear, actionable error rather than silently proceeding some other way.

## Rationale

Splitting "who owns the calendar" (OAuth/human) from "who is allowed to write events to an explicitly-named calendar" (service account, opt-in only) means each auth mechanism is used exactly where it's structurally appropriate, without either replacing the other wholesale. Scoping clears to `content_slots` rather than a calendar-wide date range means the app can never delete something it didn't create, even within its own dedicated calendar — a stronger guarantee than "trust the date range is right."

## Consequences

- First real (non-dry-run) `generate_calendar.py` run after this change requires one-time manual setup: create a Google Cloud OAuth 2.0 Client ID (Desktop app type) on a project with the Calendar API enabled, save its JSON at `config.CALENDAR_OAUTH_CLIENT_SECRETS_PATH`, and complete one browser consent. This could not be performed as part of this change — it requires the actual user's Google Cloud Console access and an interactive browser, neither available in this environment. See README.md for the exact steps.
- `data/calendar_state.json` and `~/.config/content-calendar/calendar_oauth_token.json` are new local state; back them up like `data/content.db` if this pipeline is depended on. Losing `calendar_state.json` is non-destructive (recovered by name search, or a new calendar is created); losing the OAuth token just requires re-consenting.
- `clear_calendar.py`'s default behavior changed meaningfully: it no longer takes `--start`/`--end` for its primary path (scope is "OPEN slots" or "OPEN + ASSIGNED slots", not a date range) — the explicit `--calendar` override keeps the old date-range behavior for anyone who still wants it.

## Guardrails

- Do not give `--calendar`'s default any value other than `None` in either `generate_calendar.py` or `clear_calendar.py` — a non-`None` default silently reintroduces an implicit target.
- Do not let `resolve_app_calendar` return a calendar found via name search unless `accessRole == "owner"`.
- Do not let `clear_calendar.py`'s default path call `calendars().delete()` on the dedicated calendar, or scan/delete calendar events that aren't referenced by a `content_slots` row.
- Do not let the service-account path (`build_calendar_service`) become reachable from any default/no-argument invocation of either script.
- A future publishing milestone that needs to *read* the dedicated calendar's schedule should reuse `calendar_manager.resolve_app_calendar`, not re-derive a calendar ID another way.

## Current Implementation

- `calendar_manager.py` — `build_oauth_calendar_service`, `resolve_app_calendar`, `load_calendar_state`/`save_calendar_state`, `CalendarAuthError`, `CalendarStateError`.
- `config.py` — `APP_CALENDAR_SUMMARY`, `APP_CALENDAR_DESCRIPTION`, `APP_CALENDAR_STATE_PATH`, `CALENDAR_OAUTH_SCOPES`, `CALENDAR_OAUTH_CLIENT_SECRETS_PATH`, `CALENDAR_OAUTH_TOKEN_PATH`.
- `generate_calendar.py` — `--calendar` defaults to `None`; `None` resolves the dedicated calendar via OAuth, a value uses the service account (advanced/debug override, unchanged from before this milestone).
- `clear_calendar.py` — rewritten: default path clears `content_slots`-tracked events on the dedicated calendar (`OPEN`, or `OPEN`+`ASSIGNED` with `--all`); `--calendar <id>` preserves the original full-range service-account clear as an explicit override.
- `content_store.py` — `list_slots_by_status`, `delete_slot`, `unassign_video_for_slot`.
- `tests/test_calendar_manager.py`, `tests/test_calendar_target.py`, `tests/test_clear_calendar.py`, and additions to `tests/test_content_store.py` — all Google API calls mocked, no live credentials required.
