"""Tests for backfill_platform_posts.py: the one-time backfill for videos
assigned a content_slot before Milestone 2.1.2's automatic materialization
existed. Uses a real (temp-file) ContentStore."""

from datetime import datetime

import pytest

import backfill_platform_posts as bpp
from content_store import ContentStore

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture(autouse=True)
def one_platform(monkeypatch):
    monkeypatch.setattr(bpp.platform_post_materializer, "TARGET_PUBLISHING_PLATFORMS", ["tiktok"])


def _assigned_video(store, *, name="video", scheduled_at="2026-09-20T09:00:00"):
    store.insert_slot_if_missing(scheduled_at, None, None, NOW.isoformat())
    slot_row = store._conn.execute(
        "SELECT id FROM content_slots WHERE scheduled_at = ?", (scheduled_at,)
    ).fetchone()
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())
    store.assign_slot(video.id, slot_row["id"])
    return video.id


def test_backfills_missing_row_for_assigned_video_without_one(store):
    video_id = _assigned_video(store, scheduled_at="2026-09-20T09:00:00")

    report = bpp.backfill_missing_platform_posts(store)

    assert report == [{"video_id": video_id, "platform": "tiktok", "scheduled_at": "2026-09-20T09:00:00"}]
    post = store.get_platform_post(video_id, "tiktok")
    assert post is not None
    assert post.status == "PENDING"
    assert post.scheduled_at == "2026-09-20T09:00:00"


def test_leaves_video_with_existing_row_untouched(store):
    video_id = _assigned_video(store)
    post = store.insert_platform_post(video_id, "tiktok", created_at=NOW.isoformat(), scheduled_at="2026-09-20T09:00:00")
    store.update_platform_post(post.id, updated_at=NOW.isoformat(), status="PUBLISHED", platform_post_id="real_id")
    before = store.get_platform_post(video_id, "tiktok")

    report = bpp.backfill_missing_platform_posts(store)

    assert report == []
    assert store.get_platform_post(video_id, "tiktok") == before


def test_unassigned_video_is_ignored(store):
    store.insert_video("hash-unassigned", "video.mp4", "/incoming/video.mp4", NOW.isoformat())

    report = bpp.backfill_missing_platform_posts(store)

    assert report == []


def test_dry_run_reports_without_writing(store):
    video_id = _assigned_video(store)

    report = bpp.backfill_missing_platform_posts(store, dry_run=True)

    assert len(report) == 1
    assert store.get_platform_post(video_id, "tiktok") is None


def test_idempotent_rerun_creates_nothing_on_second_pass(store):
    _assigned_video(store)

    bpp.backfill_missing_platform_posts(store)
    second_report = bpp.backfill_missing_platform_posts(store)

    assert second_report == []


def test_multiple_assigned_videos_all_backfilled(store):
    v1 = _assigned_video(store, name="v1", scheduled_at="2026-09-19T09:00:00")
    v2 = _assigned_video(store, name="v2", scheduled_at="2026-09-21T09:00:00")

    report = bpp.backfill_missing_platform_posts(store)

    assert {r["video_id"] for r in report} == {v1, v2}
    assert store.get_platform_post(v1, "tiktok").scheduled_at == "2026-09-19T09:00:00"
    assert store.get_platform_post(v2, "tiktok").scheduled_at == "2026-09-21T09:00:00"
