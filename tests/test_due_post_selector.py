"""Unit tests for deterministic due-post detection (due_post_selector.py +
content_store.get_due_platform_posts). Mirrors tests/test_slot_matcher.py's
style: a real (temp-file) ContentStore, a fixed NOW, injected time — no
wall-clock dependence."""

import itertools
from datetime import datetime, timedelta

import pytest

import due_post_selector
from content_store import ContentStore

NOW = datetime(2026, 9, 14, 8, 0, 0)
_video_counter = itertools.count()


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def _insert_video(store, name):
    record = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())
    return record.id


def _add_platform_post(
    store, *, scheduled_at, status="PENDING", platform="tiktok", platform_post_id=None, video_id=None
):
    if video_id is None:
        video_id = _insert_video(store, f"video-{next(_video_counter)}")
    record = store.insert_platform_post(video_id, platform, created_at=NOW.isoformat(), scheduled_at=scheduled_at)
    fields = {"status": status}
    if platform_post_id is not None:
        fields["platform_post_id"] = platform_post_id
    store.update_platform_post(record.id, updated_at=NOW.isoformat(), **fields)
    return store.get_platform_post(video_id, platform)


# 1. past PENDING post is returned
def test_past_pending_post_is_returned(store):
    post = _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


# 2. post scheduled exactly at now is returned (inclusive boundary)
def test_post_scheduled_exactly_at_now_is_returned(store):
    post = _add_platform_post(store, scheduled_at=NOW.isoformat(), status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


# 3. future post is excluded
def test_future_post_is_excluded(store):
    _add_platform_post(store, scheduled_at=(NOW + timedelta(hours=1)).isoformat(), status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


# 4. PUBLISHED post is excluded
def test_published_post_is_excluded(store):
    _add_platform_post(
        store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="PUBLISHED", platform_post_id="pub_1"
    )

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


def test_failed_post_is_excluded(store):
    """FAILED is documented in publish_tiktok.py as a terminal, non-
    retried record — this selector treats it the same way (see this
    module's docstring)."""
    _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="FAILED")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


# 5. unrelated platform is excluded
def test_unrelated_platform_is_excluded(store):
    _add_platform_post(
        store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="PENDING", platform="instagram"
    )

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


# 6. NULL scheduled_at is excluded
def test_null_scheduled_at_is_excluded(store):
    _add_platform_post(store, scheduled_at=None, status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


# 7. multiple due posts return earliest-first
def test_multiple_due_posts_return_earliest_first(store):
    later = _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="PENDING")
    earliest = _add_platform_post(store, scheduled_at=(NOW - timedelta(days=2)).isoformat(), status="PENDING")
    middle = _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=12)).isoformat(), status="PUBLISHING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [earliest.id, middle.id, later.id]


# 8. stable ordering when scheduled_at values are equal (tie-break by id)
def test_stable_ordering_for_equal_scheduled_at(store):
    same_time = (NOW - timedelta(hours=1)).isoformat()
    first = _add_platform_post(store, scheduled_at=same_time, status="PENDING")
    second = _add_platform_post(store, scheduled_at=same_time, status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == sorted([first.id, second.id])


# 9. naive local-time boundary behavior (this repo's actual time model —
# scheduled_at is naive local time, not UTC/aware; see module docstring)
def test_now_is_naive_local_time_matching_storage_convention(store):
    # Guards against a regression to aware datetimes, which would raise
    # TypeError or silently miscompare against the naive scheduled_at
    # strings this table actually stores.
    assert NOW.tzinfo is None
    post = _add_platform_post(store, scheduled_at=NOW.isoformat(), status="PENDING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


def test_default_now_uses_config_timezone_without_error(store):
    """Smoke test for the no-`now`-supplied path (now_in_config_timezone()).
    No wall-clock-dependent assertions — just confirms it resolves and
    returns a list rather than erroring."""
    result = due_post_selector.get_due_posts(store, "tiktok")
    assert isinstance(result, list)


# 10. selector has no mutation/side effects
def test_selector_has_no_side_effects(store):
    post = _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=1)).isoformat(), status="PENDING")
    before = store.get_platform_post(post.video_id, "tiktok")

    due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    after = store.get_platform_post(post.video_id, "tiktok")
    assert after == before
