"""Tests for the Milestone 3.2 user/ownership model: users, auth_identities,
platform_connections, the user_id scoping added to videos/content_slots/
platform_posts, and the multi-tenant execution invariant it exists to
enforce ("a hosted background job must never operate on one user's records
using another user's credentials" — see
docs/architecture/hosted-product-boundary.md §5).

Two users, A and B, are used throughout to prove isolation directly, rather
than only asserting that scoping parameters exist. A FakePublisher (same
shape as test_worker.py's) stands in for TikTokPublisher."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from content_automation.persistence.content_store import ContentStore, OwnershipMismatchError
from content_automation.publishing.publisher import PublishResult, PublishStatusResult
from content_automation.scheduling import crash_recovery, due_post_selector, reconciliation, worker

NOW = datetime(2026, 9, 14, 8, 0, 0)
NOW_UTC = NOW.replace(tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@dataclass
class FakePublisher:
    publish_result: PublishResult = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption))
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        self.status_calls.append(platform_post_id)
        return self.status_result


def _due_video_for_user(store, tmp_path, user_id, *, name="video", scheduled_at=None):
    """A fully assigned, publishable video with a due PENDING tiktok
    platform_post, owned by user_id — the same shape test_worker.py's
    due_video fixture builds, but ownership-stamped and parameterized so
    it can be built twice, once per user."""
    scheduled_at = scheduled_at or (NOW - timedelta(hours=1)).isoformat()
    video_path = tmp_path / f"{name}.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    video = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat(), user_id=user_id)
    store.update_video(
        video.id,
        canonical_media_path=str(video_path),
        container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello world", caption_source="transcript_auto",
        status="ASSIGNED",
    )
    store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=scheduled_at, user_id=user_id)
    return store.get_video(video.id)


# --- users / auth_identities / platform_connections --------------------

def test_create_user_and_get_user_roundtrip(store):
    user = store.create_user("a@example.com", "Creator A", NOW.isoformat())

    assert store.get_user(user.id) == user
    assert store.get_user_by_email("a@example.com") == user


def test_get_or_create_local_user_is_idempotent(store):
    first = store.get_or_create_local_user()
    second = store.get_or_create_local_user()

    assert first.id == second.id
    count = store._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert count == 1


def test_create_auth_identity_maps_to_user(store):
    user = store.create_user("a@example.com", "Creator A", NOW.isoformat())
    store.create_auth_identity(user.id, "google", "google-subject-1", "a@example.com", NOW.isoformat())

    resolved = store.get_user_by_auth_identity("google", "google-subject-1")
    assert resolved.id == user.id


def test_duplicate_provider_subject_cannot_map_to_two_users(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    store.create_auth_identity(user_a.id, "google", "same-subject", None, NOW.isoformat())

    with pytest.raises(sqlite3.IntegrityError):
        store.create_auth_identity(user_b.id, "google", "same-subject", None, NOW.isoformat())


def test_get_or_create_platform_connection_idempotent(store):
    user = store.create_user("a@example.com", "A", NOW.isoformat())

    first = store.get_or_create_platform_connection(user.id, "tiktok", external_account_id="open_id_1")
    second = store.get_or_create_platform_connection(user.id, "tiktok", external_account_id="different_id")

    assert first.id == second.id
    assert second.external_account_id == "open_id_1"  # first write wins, matches insert_slot_if_missing convention


def test_platform_connection_unique_per_user_and_platform(store):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    store.create_platform_connection(user.id, "tiktok", None, "ACTIVE", NOW.isoformat())

    with pytest.raises(sqlite3.IntegrityError):
        store.create_platform_connection(user.id, "tiktok", None, "ACTIVE", NOW.isoformat())


# --- write-path stamping -------------------------------------------------

def test_insert_video_stamps_user_id(store):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat(), user_id=user.id)
    assert video.user_id == user.id


def test_insert_video_without_user_id_stays_legacy_unscoped(store):
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat())
    assert video.user_id is None


def test_insert_slot_and_platform_post_stamp_user_id(store):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    store.insert_slot_if_missing("2026-09-20T09:00:00", None, None, NOW.isoformat(), user_id=user.id)
    slot = store._conn.execute("SELECT * FROM content_slots").fetchone()
    assert slot["user_id"] == user.id

    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat(), user_id=user.id)
    post = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    assert post.user_id == user.id

    created = store.insert_platform_post_if_missing(
        video.id, "youtube", scheduled_at="2026-09-20T09:00:00", created_at=NOW.isoformat(), user_id=user.id
    )
    assert created is True
    assert store.get_platform_post(video.id, "youtube").user_id == user.id


# --- assign_slot ownership invariant ------------------------------------

def test_assign_slot_succeeds_for_same_owner(store):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    store.insert_slot_if_missing("2026-09-20T09:00:00", None, None, NOW.isoformat(), user_id=user.id)
    slot = store._conn.execute("SELECT id FROM content_slots").fetchone()
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat(), user_id=user.id)

    store.assign_slot(video.id, slot["id"])  # must not raise

    assert store.get_video(video.id).assigned_slot_id == slot["id"]


def test_assign_slot_raises_on_cross_user_mismatch(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    store.insert_slot_if_missing("2026-09-20T09:00:00", None, None, NOW.isoformat(), user_id=user_b.id)
    slot = store._conn.execute("SELECT id FROM content_slots").fetchone()
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat(), user_id=user_a.id)

    with pytest.raises(OwnershipMismatchError):
        store.assign_slot(video.id, slot["id"])

    # Rejected assignment must not have partially applied.
    assert store.get_video(video.id).assigned_slot_id is None


def test_assign_slot_allows_legacy_unscoped_rows(store):
    """Neither the video nor the slot has ever been stamped with a
    user_id (every pre-3.2 row, and every existing test in this
    repository) — this must keep working exactly as before 3.2."""
    store.insert_slot_if_missing("2026-09-20T09:00:00", None, None, NOW.isoformat())
    slot = store._conn.execute("SELECT id FROM content_slots").fetchone()
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", NOW.isoformat())

    store.assign_slot(video.id, slot["id"])  # must not raise

    assert store.get_video(video.id).assigned_slot_id == slot["id"]


# --- scoped selectors: cross-tenant isolation at the query layer -------

def test_get_due_platform_posts_scoped_excludes_other_user(store, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = _due_video_for_user(store, tmp_path, user_a.id, name="a")
    _due_video_for_user(store, tmp_path, user_b.id, name="b")

    scoped_to_a = due_post_selector.get_due_posts(store, "tiktok", now=NOW, user_id=user_a.id)

    assert [p.video_id for p in scoped_to_a] == [video_a.id]


def test_get_due_platform_posts_unscoped_returns_both_users(store, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    _due_video_for_user(store, tmp_path, user_a.id, name="a")
    _due_video_for_user(store, tmp_path, user_b.id, name="b")

    unscoped = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert len(unscoped) == 2  # legacy behavior, unchanged


def test_get_recoverable_platform_posts_scoped(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    video_b = store.insert_video("hb", "b.mp4", "/incoming/b.mp4", NOW.isoformat(), user_id=user_b.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)
    post_b = store.insert_platform_post(video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id)
    stale = (NOW_UTC - timedelta(hours=1)).isoformat()
    store.update_platform_post(post_a.id, updated_at=stale, status="PUBLISHING")
    store.update_platform_post(post_b.id, updated_at=stale, status="PUBLISHING")

    rows = store.get_recoverable_platform_posts("tiktok", NOW_UTC.isoformat(), user_id=user_a.id)

    assert [r.video_id for r in rows] == [video_a.id]


def test_get_reconcilable_platform_posts_scoped(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    video_b = store.insert_video("hb", "b.mp4", "/incoming/b.mp4", NOW.isoformat(), user_id=user_b.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)
    post_b = store.insert_platform_post(video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id)
    store.update_platform_post(post_a.id, updated_at=NOW_UTC.isoformat(), status="PUBLISHING", platform_post_id="pid_a")
    store.update_platform_post(post_b.id, updated_at=NOW_UTC.isoformat(), status="PUBLISHING", platform_post_id="pid_b")

    rows = store.get_reconcilable_platform_posts("tiktok", NOW_UTC.isoformat(), user_id=user_a.id)

    assert [r.video_id for r in rows] == [video_a.id]


def test_claim_platform_post_scoped_rejects_other_user(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)

    claimed_by_b = store.claim_platform_post(post_a.id, updated_at=NOW_UTC.isoformat(), user_id=user_b.id)
    assert claimed_by_b is False
    assert store.get_platform_post(video_a.id, "tiktok").status == "PENDING"

    claimed_by_a = store.claim_platform_post(post_a.id, updated_at=NOW_UTC.isoformat(), user_id=user_a.id)
    assert claimed_by_a is True


def test_update_platform_post_if_unchanged_scoped_rejects_other_user(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)

    updated_by_b = store.update_platform_post_if_unchanged(
        post_a.id, expected_updated_at=post_a.updated_at, updated_at=NOW_UTC.isoformat(),
        user_id=user_b.id, status="FAILED", failure_reason="should not apply",
    )
    assert updated_by_b is False
    assert store.get_platform_post(video_a.id, "tiktok").status == "PENDING"


# --- job-layer integration: the actual multi-tenant execution invariant -

def test_worker_scoped_to_user_never_claims_other_users_post(store, tmp_path):
    """The explicit scenario Milestone 3.2's brief asks for: user A's video
    must never be discovered or claimed by a worker pass scoped to user B."""
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = _due_video_for_user(store, tmp_path, user_a.id, name="a")
    _due_video_for_user(store, tmp_path, user_b.id, name="b")

    publisher_b = FakePublisher()
    summary_b = worker.run_due_posts_once(store, publisher_b, platform="tiktok", now=NOW, user_id=user_b.id)

    assert summary_b.discovered == 1
    assert len(publisher_b.publish_calls) == 1
    assert store.get_platform_post(video_a.id, "tiktok").status == "PENDING"  # A untouched

    publisher_a = FakePublisher()
    summary_a = worker.run_due_posts_once(store, publisher_a, platform="tiktok", now=NOW, user_id=user_a.id)

    assert summary_a.discovered == 1
    assert store.get_platform_post(video_a.id, "tiktok").status == "PUBLISHED"


def test_reconciliation_scoped_to_user_never_touches_other_users_post(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    video_b = store.insert_video("hb", "b.mp4", "/incoming/b.mp4", NOW.isoformat(), user_id=user_b.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)
    post_b = store.insert_platform_post(video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id)
    store.update_platform_post(post_a.id, updated_at=NOW_UTC.isoformat(), status="PUBLISHING", platform_post_id="pid_a")
    store.update_platform_post(post_b.id, updated_at=NOW_UTC.isoformat(), status="PUBLISHING", platform_post_id="pid_b")

    publisher_b = FakePublisher()
    summary_b = reconciliation.reconcile_pending_status_checks_once(
        store, publisher_b, platform="tiktok", now=NOW_UTC, user_id=user_b.id
    )

    assert summary_b.discovered == 1
    assert publisher_b.status_calls == ["pid_b"]
    assert store.get_platform_post(video_a.id, "tiktok").status == "PUBLISHING"  # A untouched
    assert store.get_platform_post(video_b.id, "tiktok").status == "PUBLISHED"


def test_crash_recovery_scoped_to_user_never_touches_other_users_post(store):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video_a = store.insert_video("ha", "a.mp4", "/incoming/a.mp4", NOW.isoformat(), user_id=user_a.id)
    video_b = store.insert_video("hb", "b.mp4", "/incoming/b.mp4", NOW.isoformat(), user_id=user_b.id)
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)
    post_b = store.insert_platform_post(video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id)
    stale = (NOW_UTC - timedelta(hours=1)).isoformat()
    store.update_platform_post(post_a.id, updated_at=stale, status="PUBLISHING")  # Case A: no platform_post_id
    store.update_platform_post(post_b.id, updated_at=stale, status="PUBLISHING")

    publisher_b = FakePublisher()
    summary_b = crash_recovery.recover_stale_posts_once(
        store, publisher_b, platform="tiktok", now=NOW_UTC, user_id=user_b.id
    )

    assert summary_b.discovered == 1
    assert summary_b.requeued == 1
    assert store.get_platform_post(video_a.id, "tiktok").status == "PUBLISHING"  # A untouched
    assert store.get_platform_post(video_b.id, "tiktok").status == "PENDING"


# --- full pipeline under the bootstrap user (back-compat proof) --------

def test_full_pipeline_under_bootstrap_user_behaves_like_legacy_unscoped(store, tmp_path):
    """The pre-3.2 single-user assignment path (assign_slot -> materialize)
    run entirely under the resolved bootstrap user must produce the exact
    same slot-matching/materialization outcome as the unscoped
    (user_id=None) path always has — proving 3.2 added an optional owner
    without changing existing behavior. The full media-inspection/
    transcription pipeline this feeds is already covered end-to-end by
    tests/test_fifo_process_content.py; this test is scoped to the
    ownership outcome specifically."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    user = store.get_or_create_local_user()
    store.insert_slot_if_missing("2026-09-20T09:00:00", None, None, NOW.isoformat(), user_id=user.id)

    video = store.insert_video("hash-clip", "clip.mp4", str(video_path), NOW.isoformat(), user_id=user.id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), status="TRANSCRIBED",
        caption_text="hi", caption_source="transcript_auto",
    )

    from content_automation.scheduling import platform_post_materializer, slot_matcher

    slot = slot_matcher.select_slot_fifo(store, now=NOW, user_id=user.id)
    store.assign_slot(video.id, slot.id)
    platform_post_materializer.materialize_platform_posts_for_assignment(
        store, video.id, slot.id, NOW.isoformat(), user_id=user.id
    )

    post = store.get_platform_post(video.id, "tiktok")
    assert post.user_id == user.id
    assert store.get_video(video.id).user_id == user.id
    assert store.get_slot(slot.id).user_id == user.id
