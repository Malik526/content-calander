"""Tests for publishing.publish_status (Milestone 3.11: User-Facing Publish
States + Errors) — the one resolver from stored slot/platform_posts truth to
OPEN/SCHEDULED/PUBLISHING/PUBLISHED/FAILED/NEEDS_ATTENTION. Pure: records are
built directly and `now` is fixed, so no store or wall clock is involved."""

from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from content_automation.persistence.content_store import PlatformPostRecord, SlotRecord
from content_automation.publishing.publish_status import resolve_slot_publish_status, resolve_video_publish_status

# 2026-10-05 09:00 America/New_York == 13:00 UTC.
NOW = datetime(2026, 10, 5, 13, 0, 0, tzinfo=timezone.utc)
FUTURE = "2026-10-06T09:00:00"
PAST = "2026-10-04T09:00:00"


def _slot(scheduled_at=FUTURE, status="ASSIGNED", video_id=1, tz="America/New_York"):
    return SlotRecord(
        id=1, scheduled_at=scheduled_at, pillar_key=None, prompt=None, status=status,
        assigned_video_id=video_id, google_calendar_event_id=None, created_at="2026-01-01T00:00:00",
        user_id=1, timezone=tz, cadence_id=None,
    )


def _post(**fields):
    base = PlatformPostRecord(
        id=1, video_id=1, platform="tiktok", status="PENDING", platform_post_id=None, scheduled_at=FUTURE,
        published_at=None, failure_reason=None, created_at="2026-10-01T00:00:00+00:00",
        updated_at="2026-10-05T12:59:00+00:00", retry_count=0, next_retry_at=None,
        next_status_check_at=None, status_check_count=0, user_id=1, failure_code=None,
    )
    return replace(base, **fields)


def _resolve(slot, posts):
    return resolve_slot_publish_status(slot, posts, now_utc=NOW)


# --- the five product states -------------------------------------------------------

def test_open_slot():
    result = _resolve(_slot(status="OPEN", video_id=None), [])
    assert result.display_status == "OPEN"
    assert result.can_unassign is False


def test_pending_future_post_is_scheduled():
    result = _resolve(_slot(), [_post()])
    assert result.display_status == "SCHEDULED"
    assert result.message is None
    assert result.can_unassign is True


def test_publishing_in_progress_is_publishing():
    result = _resolve(_slot(), [_post(status="PUBLISHING", updated_at="2026-10-05T12:58:00+00:00")])
    assert result.display_status == "PUBLISHING"
    assert result.can_unassign is False


def test_published_with_platform_post_id_is_published():
    post = _post(status="PUBLISHED", platform_post_id="pub_1", published_at="2026-10-05T12:30:00+00:00")
    result = _resolve(_slot(), [post])
    assert result.display_status == "PUBLISHED"
    assert result.published_at == "2026-10-05T12:30:00+00:00"
    assert result.message is None
    assert result.can_unassign is False


def test_failed_is_failed_with_sanitized_explanation():
    post = _post(status="FAILED", failure_code="REAUTHORIZATION_REQUIRED",
                 failure_reason="TikTok token endpoint error (HTTP 400): {'access_token': '[redacted]'}")
    result = _resolve(_slot(), [post])
    assert result.display_status == "FAILED"
    assert result.reason_code == "AUTH_REQUIRED"
    assert result.message == "TikTok connection needs to be renewed. Reconnect your account in Settings."
    assert result.action_hint == "RECONNECT_ACCOUNT"
    assert "HTTP 400" not in result.message


# --- NEEDS_ATTENTION triggers ------------------------------------------------------

def test_overdue_pending_post_needs_attention():
    result = _resolve(_slot(scheduled_at=PAST), [_post(scheduled_at=PAST)])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "SCHEDULE_MISSED"
    assert result.can_unassign is True  # still only PENDING — removing is safe


def test_pending_within_grace_window_is_still_scheduled():
    result = _resolve(_slot(scheduled_at="2026-10-05T08:45:00"), [_post()])
    assert result.display_status == "SCHEDULED"


def test_overdue_uses_the_slots_own_timezone():
    # 09:00 Los Angeles is 16:00 UTC — still three hours away at NOW.
    result = _resolve(_slot(scheduled_at="2026-10-05T09:00:00", tz="America/Los_Angeles"), [_post()])
    assert result.display_status == "SCHEDULED"


def test_pending_with_a_future_automatic_retry_is_scheduled_with_note():
    post = _post(retry_count=1, next_retry_at="2026-10-05T09:30:00", failure_code="NETWORK_ERROR",
                 failure_reason="ConnectionError: HTTPSConnectionPool(host='open.tiktokapis.com')")
    result = _resolve(_slot(scheduled_at=PAST), [post])
    assert result.display_status == "SCHEDULED"
    assert result.reason_code == "NETWORK_ERROR"
    assert result.message == "A previous attempt didn't go through; another attempt is scheduled."


def test_stale_publishing_without_platform_id_needs_attention():
    result = _resolve(_slot(), [_post(status="PUBLISHING", updated_at="2026-10-05T12:00:00+00:00")])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "PUBLISH_STALLED"


def test_publishing_with_overdue_status_check_is_unconfirmed():
    post = _post(status="PUBLISHING", platform_post_id="pub_1", next_status_check_at="2026-10-05T12:00:00+00:00")
    result = _resolve(_slot(), [post])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "PUBLISH_UNCONFIRMED"
    assert result.message == "Publishing status could not be confirmed."


def test_publishing_with_a_scheduled_status_check_is_still_publishing():
    post = _post(status="PUBLISHING", platform_post_id="pub_1", updated_at="2026-10-05T11:00:00+00:00",
                 next_status_check_at="2026-10-05T13:05:00+00:00")
    assert _resolve(_slot(), [post]).display_status == "PUBLISHING"


def test_published_without_platform_post_id_is_never_shown_as_published():
    result = _resolve(_slot(), [_post(status="PUBLISHED", published_at="2026-10-05T12:30:00+00:00")])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "STATE_INCONSISTENT"


def test_unrecognized_post_status_needs_attention():
    result = _resolve(_slot(), [_post(status="QUEUED_SOMEWHERE")])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "STATE_INCONSISTENT"


def test_assigned_video_with_no_platform_post_needs_attention():
    result = _resolve(_slot(), [])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "NOT_SET_UP_TO_PUBLISH"


@pytest.mark.parametrize("status, video_id", [("ASSIGNED", None), ("PUBLISHED", 1), ("OPEN", 1)])
def test_contradictory_slot_rows_need_attention(status, video_id):
    result = _resolve(_slot(status=status, video_id=video_id), [])
    assert result.display_status == "NEEDS_ATTENTION"
    assert result.reason_code == "STATE_INCONSISTENT"


# --- hints, aggregation ------------------------------------------------------------

def test_edit_caption_hint_only_while_caption_is_still_editable():
    before_submission = _post(status="FAILED", failure_code="CAPTION_TOO_LONG")
    result = _resolve(_slot(), [before_submission])
    assert result.message == "Caption is too long for TikTok."
    assert result.action_hint == "EDIT_CAPTION"

    after_submission = _post(status="FAILED", platform_post_id="pub_1", failure_code="spam_risk_text")
    result = _resolve(_slot(), [after_submission])
    assert result.reason_code == "CAPTION_INVALID"
    assert result.action_hint is None


def test_multi_platform_slot_shows_most_urgent_state_and_every_publication():
    published = _post(status="PUBLISHED", platform_post_id="pub_1", published_at="2026-10-05T12:30:00+00:00")
    failed = _post(id=2, platform="instagram", status="FAILED", failure_code=None)
    result = _resolve(_slot(), [published, failed])
    assert result.display_status == "FAILED"
    assert result.message == "Publishing to Instagram failed for an unexpected reason."
    assert [p.display_status for p in result.publications] == ["PUBLISHED", "FAILED"]


def test_slot_is_published_only_when_every_platform_is():
    published = _post(status="PUBLISHED", platform_post_id="pub_1", published_at="2026-10-05T12:30:00+00:00")
    pending = _post(id=2, platform="instagram")
    assert _resolve(_slot(), [published, pending]).display_status == "SCHEDULED"


# --- Library video status (Milestone 3.14 final follow-up) ---------------------------

def _video(slot_id=1):
    return SimpleNamespace(id=1, assigned_slot_id=slot_id)


def test_video_without_slot_is_unscheduled():
    assert resolve_video_publish_status(_video(slot_id=None), None, [], now_utc=NOW).display_status == "UNSCHEDULED"


def test_video_status_matches_its_slot_status():
    for post in (_post(), _post(status="PUBLISHED", platform_post_id="p1"), _post(status="FAILED"), _post(status="UNKNOWN")):
        assert (
            resolve_video_publish_status(_video(), _slot(), [post], now_utc=NOW)
            == resolve_slot_publish_status(_slot(), [post], now_utc=NOW)
        )


def test_video_whose_slot_is_missing_or_points_elsewhere_needs_attention():
    for slot in (None, _slot(video_id=99), _slot(status="OPEN", video_id=None)):
        result = resolve_video_publish_status(_video(), slot, [_post()], now_utc=NOW)
        assert (result.display_status, result.reason_code) == ("NEEDS_ATTENTION", "STATE_INCONSISTENT")
