"""Tests for backfill_ownership.py: the one-time attribution of
pre-Milestone-3.2 videos/content_slots/platform_posts rows to the local
bootstrap user, and the TikTok-token-file-to-platform_connection bridge.
Uses a real (temp-file) ContentStore; never touches a real TikTok token
file (TIKTOK_TOKEN_PATH is always redirected to tmp_path)."""

from datetime import datetime

import pytest

import backfill_ownership as bo
from content_automation.persistence.content_store import LOCAL_BOOTSTRAP_USER_EMAIL, ContentStore

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture(autouse=True)
def isolated_token_path(monkeypatch, tmp_path):
    """Redirect the token file backfill_ownership reads so this test suite
    never touches (or depends on) the real ~/.config/content-calendar/
    token file — matches the existing test_tiktok_auth.py pattern."""
    monkeypatch.setattr(bo.tiktok_auth, "TIKTOK_TOKEN_PATH", tmp_path / "tiktok_token.json")


def _legacy_video(store, *, name="video", scheduled_at="2026-09-20T09:00:00"):
    """A video/slot pair created the pre-3.2 way — no user_id at all,
    exactly what every real row in this repository's database looked like
    before this milestone."""
    store.insert_slot_if_missing(scheduled_at, None, None, NOW.isoformat())
    slot_row = store._conn.execute(
        "SELECT id FROM content_slots WHERE scheduled_at = ?", (scheduled_at,)
    ).fetchone()
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())
    store.assign_slot(video.id, slot_row["id"])
    store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=scheduled_at)
    return video.id


def test_creates_local_user_and_attributes_legacy_rows(store):
    video_id = _legacy_video(store)

    report = bo.backfill_ownership(store)

    assert report["user_created"] is True
    user = store.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL)
    assert report["user_id"] == user.id
    assert store.get_video(video_id).user_id == user.id
    assert store._conn.execute(
        "SELECT user_id FROM content_slots"
    ).fetchone()["user_id"] == user.id
    assert store.get_platform_post(video_id, "tiktok").user_id == user.id


def test_creates_tiktok_platform_connection(store):
    report = bo.backfill_ownership(store)

    connection = store.get_platform_connection(report["user_id"], "tiktok")
    assert connection is not None
    assert connection.id == report["connection_id"]
    assert report["connection_created"] is True


def test_platform_connection_picks_up_cached_open_id(store, tmp_path):
    (tmp_path / "tiktok_token.json").write_text(
        '{"access_token": "a", "refresh_token": "r", '
        '"access_token_expires_at": "2026-09-20T00:00:00+00:00", '
        '"refresh_token_expires_at": "2027-09-20T00:00:00+00:00", '
        '"open_id": "real_open_id_123"}',
        encoding="utf-8",
    )

    report = bo.backfill_ownership(store)

    connection = store.get_platform_connection(report["user_id"], "tiktok")
    assert connection.external_account_id == "real_open_id_123"


def test_no_token_file_leaves_external_account_id_none(store):
    report = bo.backfill_ownership(store)

    connection = store.get_platform_connection(report["user_id"], "tiktok")
    assert connection.external_account_id is None


def test_dry_run_reports_without_writing(store):
    video_id = _legacy_video(store)

    report = bo.backfill_ownership(store, dry_run=True)

    assert report["videos_backfilled"] == 1
    assert report["content_slots_backfilled"] == 1
    assert report["platform_posts_backfilled"] == 1
    assert store.get_video(video_id).user_id is None
    assert store.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL) is None


def test_idempotent_rerun_touches_nothing_new(store):
    _legacy_video(store)

    first = bo.backfill_ownership(store)
    second = bo.backfill_ownership(store)

    assert second["user_created"] is False
    assert second["connection_created"] is False
    assert second["videos_backfilled"] == 0
    assert second["content_slots_backfilled"] == 0
    assert second["platform_posts_backfilled"] == 0
    assert second["user_id"] == first["user_id"]
    assert second["connection_id"] == first["connection_id"]


def test_never_reassigns_an_already_owned_row(store):
    """A row created by a real 3.2-aware caller (already carrying a real,
    non-bootstrap user_id) must never be silently reattributed to the
    bootstrap user by a later backfill run — the backfill only ever
    touches rows where user_id IS NULL."""
    other_user = store.create_user("someone@example.com", "Someone", NOW.isoformat())
    video = store.insert_video("hash-owned", "owned.mp4", "/incoming/owned.mp4", NOW.isoformat(), user_id=other_user.id)

    report = bo.backfill_ownership(store)

    assert report["videos_backfilled"] == 0
    assert store.get_video(video.id).user_id == other_user.id


def test_publishing_state_is_never_touched(store):
    video_id = _legacy_video(store)
    post_before = store.get_platform_post(video_id, "tiktok")
    video_before = store.get_video(video_id)

    bo.backfill_ownership(store)

    post_after = store.get_platform_post(video_id, "tiktok")
    video_after = store.get_video(video_id)
    assert post_after.status == post_before.status
    assert post_after.scheduled_at == post_before.scheduled_at
    assert post_after.platform_post_id == post_before.platform_post_id
    assert video_after.status == video_before.status
    assert video_after.canonical_media_path == video_before.canonical_media_path
