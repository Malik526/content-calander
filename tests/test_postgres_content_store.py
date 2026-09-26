"""Integration tests for PostgresContentStore (Milestone 3.3) — real
Postgres, real concurrency, real cross-tenant isolation proofs.

Skipped entirely if config.DATABASE_URL is unset (no Postgres configured in
this environment) — the rest of the suite (626 tests) never requires
network/Postgres access; this file is the deliberate exception, per
docs/decisions/0008-postgres-persistence-migration.md "Local Development
Strategy" (fast domain/unit tests stay isolated; persistence/concurrency
integration tests run against real Postgres).

Runs entirely inside config.POSTGRES_TEST_SCHEMA (default
"pickle_batch_test"), never config.POSTGRES_SCHEMA ("public", the real
application schema) — the schema is dropped and recreated once per test
session (session-scoped fixture) and every table is truncated
(RESTART IDENTITY CASCADE) before each test, so tests are deterministic and
never see another test's rows, and never touch real business data."""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from content_automation.config import DATABASE_URL, POSTGRES_TEST_SCHEMA
from content_automation.persistence.content_store import OwnershipMismatchError, SlotUnavailableError
from content_automation.persistence.postgres_content_store import PostgresContentStore
from content_automation.publishing.publisher import PublishResult, PublishStatusResult
from content_automation.scheduling import crash_recovery, platform_post_materializer, reconciliation, slot_matcher, worker

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not configured — Postgres integration tests skipped")

NOW = datetime(2026, 9, 14, 8, 0, 0)
NOW_UTC = NOW.replace(tzinfo=timezone.utc)


@pytest.fixture(scope="session", autouse=True)
def _reset_test_schema():
    if not DATABASE_URL:
        yield
        return
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute(f'DROP SCHEMA IF EXISTS "{POSTGRES_TEST_SCHEMA}" CASCADE')
    conn.close()
    yield


@pytest.fixture
def store():
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as s:
        # Truncate every table this suite touches before each test — cheap,
        # deterministic, and resets identity sequences so ids are
        # predictable across tests without relying on execution order.
        s._conn.execute(
            "TRUNCATE platform_posts, content_slots, videos, platform_connections, "
            "auth_identities, users RESTART IDENTITY CASCADE"
        )
        yield s


def _user(store, email="a@example.com"):
    return store.create_user(email, "Creator", NOW.isoformat())


def _video(store, user, *, name="v", file_hash=None):
    return store.insert_video(file_hash or f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat(), user_id=user.id)


def _slot(store, user, *, scheduled_at="2026-09-20T09:00:00"):
    store.insert_slot_if_missing(scheduled_at, None, None, NOW.isoformat(), user_id=user.id)
    return store._conn.execute("SELECT id FROM content_slots WHERE scheduled_at = %s", (scheduled_at,)).fetchone()["id"]


# --- basic CRUD roundtrip, real Postgres ---------------------------------

def test_create_user_and_video_roundtrip(store):
    user = _user(store)
    video = _video(store, user)

    assert video.user_id == user.id
    assert store.get_video(video.id) == video
    assert store.get_video_by_hash(video.file_hash) == video


def test_list_videos_for_user_scopes_strictly_by_user_newest_first(store):
    """Milestone 3.7's Library query, against real Postgres — Postgres's
    videos.user_id is NOT NULL (unlike SQLite's legacy-compat-nullable
    column), so there is no "unowned row" case to worry about here at
    all; this only has to prove per-user scoping and ordering."""
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    older = _video(store, user_a, name="older", file_hash="hash-older")
    newer = _video(store, user_a, name="newer", file_hash="hash-newer")
    _video(store, user_b, name="other", file_hash="hash-other")

    videos = store.list_videos_for_user(user_a.id)

    assert [v.id for v in videos] == [newer.id, older.id]
    assert all(v.user_id == user_a.id for v in videos)
    assert store.list_videos_for_user(999999) == []


def test_upload_batch_and_attempt_persistence_is_isolated_per_tenant(store):
    """Milestone 3.7 follow-up's upload-performance instrumentation,
    against real Postgres."""
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")

    batch_a = store.create_upload_batch(user_a.id, started_at=NOW.isoformat(), file_count=2)
    attempt_a = store.create_upload_attempt(batch_a.id, user_a.id, "clip.mp4", started_at=NOW.isoformat())
    store.update_upload_attempt(attempt_a.id, completed_at=NOW.isoformat(), duration_ms=42, status="SUCCESS")
    store.update_upload_batch(
        batch_a.id, completed_at=NOW.isoformat(), total_bytes=100, total_duration_ms=50, status="COMPLETED",
    )

    batch_b = store.create_upload_batch(user_b.id, started_at=NOW.isoformat(), file_count=1)

    assert [b.id for b in store.list_upload_batches_for_user(user_a.id)] == [batch_a.id]
    assert [b.id for b in store.list_upload_batches_for_user(user_b.id)] == [batch_b.id]

    refreshed = store.list_upload_batches_for_user(user_a.id)[0]
    assert refreshed.status == "COMPLETED"
    assert refreshed.total_bytes == 100

    attempts = store.get_upload_attempts_for_batch(batch_a.id)
    assert len(attempts) == 1
    assert attempts[0].status == "SUCCESS"
    assert attempts[0].duration_ms == 42
    assert store.get_upload_attempts_for_batch(batch_b.id) == []


def test_timestamps_round_trip_as_aware_utc_strings(store):
    user = _user(store)
    fetched = store.get_user(user.id)
    # created_at/updated_at are TIMESTAMPTZ in Postgres — must come back as
    # real ISO strings with a UTC offset, matching the SQLite backend's
    # TEXT-stored convention exactly (see postgres_content_store.py's
    # _normalize_row docstring).
    assert isinstance(fetched.created_at, str)
    parsed = datetime.fromisoformat(fetched.created_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_get_or_create_local_user_is_idempotent(store):
    first = store.get_or_create_local_user()
    second = store.get_or_create_local_user()
    assert first.id == second.id
    count = store._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert count == 1


# --- Phase 8: ownership is NOT NULL, enforced by the schema itself -------

def test_video_insert_without_ownership_is_rejected_by_schema():
    """insert_video requires user_id as a real Python parameter (TypeError
    if omitted) — but the deeper guarantee this phase asks for is that the
    database itself refuses a NULL user_id even if a caller bypassed the
    Python method entirely and wrote raw SQL, which this test proves
    directly."""
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as store:
        store._conn.execute(
            "TRUNCATE platform_posts, content_slots, videos, platform_connections, "
            "auth_identities, users RESTART IDENTITY CASCADE"
        )
        with pytest.raises(psycopg.errors.NotNullViolation):
            store._conn.execute(
                "INSERT INTO videos (file_hash, original_filename, original_path, status, created_at, user_id) "
                "VALUES ('h', 'v.mp4', '/incoming/v.mp4', 'DISCOVERED', %s, NULL)",
                (NOW.isoformat(),),
            )


def test_insert_video_requires_user_id_parameter(store):
    with pytest.raises(TypeError):
        store.insert_video("h", "v.mp4", "/incoming/v.mp4", NOW.isoformat())  # missing user_id


# --- Phase 9: ownership consistency invariant, unconditional under PG ---

def test_assign_slot_raises_on_cross_user_mismatch(store):
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    slot_id = _slot(store, user_b)
    video = _video(store, user_a)

    with pytest.raises(OwnershipMismatchError):
        store.assign_slot(video.id, slot_id)

    assert store.get_video(video.id).assigned_slot_id is None


def test_assign_slot_succeeds_for_same_owner(store):
    user = _user(store)
    slot_id = _slot(store, user)
    video = _video(store, user)

    store.assign_slot(video.id, slot_id)

    assert store.get_video(video.id).assigned_slot_id == slot_id
    assert store.get_slot(slot_id).status == "ASSIGNED"


def test_assign_slot_raises_slot_unavailable_when_not_open(store):
    user = _user(store)
    slot_id = _slot(store, user)
    video1 = _video(store, user, name="v1")
    video2 = _video(store, user, name="v2")
    store.assign_slot(video1.id, slot_id)

    with pytest.raises(SlotUnavailableError):
        store.assign_slot(video2.id, slot_id)


# --- Phase 13: atomic claiming under real Postgres concurrency ----------

def test_two_real_connections_racing_to_claim_exactly_one_wins():
    """The brief's own headline test: two independent real Postgres
    connections (real threads, real network round-trips, not mocks) race
    to claim the same PENDING platform_post. Exactly one must win."""
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as setup_store:
        setup_store._conn.execute(
            "TRUNCATE platform_posts, content_slots, videos, platform_connections, "
            "auth_identities, users RESTART IDENTITY CASCADE"
        )
        user = setup_store.create_user("racer@example.com", "Racer", NOW.isoformat())
        video = setup_store.insert_video("hash-race", "race.mp4", "/incoming/race.mp4", NOW.isoformat(), user_id=user.id)
        post = setup_store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
        post_id, user_id = post.id, user.id

    results = []
    barrier = threading.Barrier(5)

    def try_claim():
        with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as claimer_store:
            barrier.wait()  # maximize real overlap between the five claim attempts
            won = claimer_store.claim_platform_post(post_id, updated_at=NOW_UTC.isoformat(), user_id=user_id)
            results.append(won)

    threads = [threading.Thread(target=try_claim) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 4

    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as verify_store:
        assert verify_store.get_platform_post(video.id, "tiktok").status == "PUBLISHING"


# --- Phase 14: CAS / reconciliation concurrency under real Postgres -----

def test_stale_cas_update_cannot_overwrite_newer_state():
    """Actor A reads a row, then genuinely updates it (new updated_at).
    Actor B, holding the stale updated_at it read before A's write,
    attempts a compare-and-swap update — it must fail, and A's write must
    survive untouched."""
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as setup_store:
        setup_store._conn.execute(
            "TRUNCATE platform_posts, content_slots, videos, platform_connections, "
            "auth_identities, users RESTART IDENTITY CASCADE"
        )
        user = setup_store.create_user("cas@example.com", "CAS", NOW.isoformat())
        video = setup_store.insert_video("hash-cas", "cas.mp4", "/incoming/cas.mp4", NOW.isoformat(), user_id=user.id)
        post = setup_store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
        stale_updated_at = post.updated_at  # B will hold onto this, already-stale, value
        post_id, user_id, video_id = post.id, user.id, video.id

    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as store_a:
        a_new_updated_at = (NOW_UTC + timedelta(seconds=1)).isoformat()
        a_won = store_a.update_platform_post_if_unchanged(
            post_id, expected_updated_at=stale_updated_at, updated_at=a_new_updated_at,
            user_id=user_id, status="PUBLISHING", platform_post_id="real_pid",
        )
    assert a_won is True

    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as store_b:
        b_won = store_b.update_platform_post_if_unchanged(
            post_id, expected_updated_at=stale_updated_at,  # B never saw A's write
            updated_at=(NOW_UTC + timedelta(seconds=2)).isoformat(),
            user_id=user_id, status="FAILED", failure_reason="B should never apply this",
        )
    assert b_won is False

    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as verify_store:
        final = verify_store.get_platform_post(video_id, "tiktok")
        assert final.status == "PUBLISHING"  # A's write survives
        assert final.platform_post_id == "real_pid"


# --- Phase 15: cross-tenant isolation under real Postgres ---------------

def test_due_selector_scoped_to_user_b_never_sees_user_a(store):
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    video_a = _video(store, user_a, name="a")
    video_b = _video(store, user_b, name="b")
    store.insert_platform_post(
        video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id,
        scheduled_at=(NOW - timedelta(hours=1)).isoformat(),
    )
    store.insert_platform_post(
        video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id,
        scheduled_at=(NOW - timedelta(hours=1)).isoformat(),
    )

    due_for_b = store.get_due_platform_posts("tiktok", NOW.isoformat(), ["PENDING"], user_id=user_b.id)

    assert [p.video_id for p in due_for_b] == [video_b.id]


def test_claim_scoped_to_user_b_cannot_claim_user_a_post(store):
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    video_a = _video(store, user_a, name="a")
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)

    claimed_by_b = store.claim_platform_post(post_a.id, updated_at=NOW_UTC.isoformat(), user_id=user_b.id)

    assert claimed_by_b is False
    assert store.get_platform_post(video_a.id, "tiktok").status == "PENDING"


def test_recoverable_and_reconcilable_selectors_scoped_to_user(store):
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    video_a = _video(store, user_a, name="a")
    video_b = _video(store, user_b, name="b")
    post_a = store.insert_platform_post(video_a.id, "tiktok", created_at=NOW.isoformat(), user_id=user_a.id)
    post_b = store.insert_platform_post(video_b.id, "tiktok", created_at=NOW.isoformat(), user_id=user_b.id)
    stale = (NOW_UTC - timedelta(hours=1)).isoformat()
    store.update_platform_post(post_a.id, updated_at=stale, status="PUBLISHING", platform_post_id="pid_a")
    store.update_platform_post(post_b.id, updated_at=stale, status="PUBLISHING", platform_post_id="pid_b")

    recoverable_b = store.get_recoverable_platform_posts("tiktok", NOW_UTC.isoformat(), user_id=user_b.id)
    reconcilable_b = store.get_reconcilable_platform_posts("tiktok", NOW_UTC.isoformat(), user_id=user_b.id)

    assert [p.video_id for p in recoverable_b] == [video_b.id]
    assert [p.video_id for p in reconcilable_b] == [video_b.id]


def test_find_earliest_open_slot_fifo_scoped_to_user(store):
    user_a = _user(store, "a@example.com")
    user_b = _user(store, "b@example.com")
    _slot(store, user_a, scheduled_at="2026-09-20T09:00:00")
    slot_b_id = _slot(store, user_b, scheduled_at="2026-09-21T09:00:00")

    found = store.find_earliest_open_slot_fifo(NOW.isoformat(), user_id=user_b.id)

    assert found.id == slot_b_id


# --- migration runner idempotency ----------------------------------------

# --- Phase 29: end-to-end simulation, real scheduling/domain modules ----

@dataclass
class FakePublisher:
    publish_result: PublishResult = field(
        default_factory=lambda: PublishResult(platform_post_id="pg_pub_1", status="PROCESSING_UPLOAD")
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


def test_end_to_end_upload_to_published_against_real_postgres(store, tmp_path):
    """The brief's own Phase 29 scenario, run against real Postgres using
    the *actual* scheduling/domain modules (slot_matcher,
    platform_post_materializer, worker) — not reimplemented test logic —
    proving those modules work transparently against either
    ContentStoreProtocol-satisfying backend, not just SQLite."""
    user = store.get_or_create_local_user()
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    past_scheduled_at = (NOW - timedelta(hours=1)).isoformat()
    store.insert_slot_if_missing(past_scheduled_at, None, None, NOW.isoformat(), user_id=user.id)
    video = store.insert_video("hash-e2e", "clip.mp4", str(video_path), NOW.isoformat(), user_id=user.id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264",
        audio_codec="aac", width=576, height=1024, fps=30.0, duration_seconds=20.0,
        file_size_bytes=video_path.stat().st_size, caption_text="hello world",
        caption_source="transcript_auto", status="ASSIGNED",
    )

    # select_slot_fifo matches the earliest OPEN slot at/after `now` — pass
    # a `now` before the slot's own scheduled_at so the match succeeds, then
    # separately run the worker at a *later* `now` so the resulting
    # platform_posts row (scheduled_at copied from the slot) is actually due.
    slot = slot_matcher.select_slot_fifo(store, now=NOW - timedelta(hours=2), user_id=user.id)
    store.assign_slot(video.id, slot.id)
    platform_post_materializer.materialize_platform_posts_for_assignment(
        store, video.id, slot.id, NOW.isoformat(), user_id=user.id
    )

    publisher = FakePublisher()
    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW, user_id=user.id)

    assert summary.published == 1
    assert len(publisher.publish_calls) == 1
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_end_to_end_async_reconciliation_against_real_postgres(store, tmp_path):
    """Same scenario's async-status branch: a submission that's still
    PROCESSING right after the worker's inline poll resolves to PUBLISHED
    via reconciliation.reconcile_pending_status_checks_once — the real
    module, against real Postgres."""
    video_path = tmp_path / "async.mp4"
    video_path.write_bytes(b"fake mp4 bytes")
    user = store.get_or_create_local_user()
    video = store.insert_video("hash-async", "async.mp4", str(video_path), NOW.isoformat(), user_id=user.id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264",
        audio_codec="aac", width=576, height=1024, fps=30.0, duration_seconds=20.0,
        file_size_bytes=video_path.stat().st_size,
        caption_text="hello", caption_source="transcript_auto", status="ASSIGNED",
    )
    store.insert_platform_post(
        video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id,
        scheduled_at=(NOW - timedelta(hours=1)).isoformat(),
    )

    still_processing = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))
    worker.run_due_posts_once(store, still_processing, platform="tiktok", now=NOW, user_id=user.id)
    after_submit = store.get_platform_post(video.id, "tiktok")
    assert after_submit.status == "PUBLISHING"

    # publish_tiktok._poll_and_update stamps next_status_check_at from real
    # wall-clock time (datetime.now(timezone.utc)), not this test's fixed
    # NOW — so reconciliation must be run at a `now` genuinely after that
    # real stamp, not after the test's own fictional NOW.
    reconcile_at = datetime.fromisoformat(after_submit.next_status_check_at) + timedelta(seconds=1)
    resolves_now = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))
    summary = reconciliation.reconcile_pending_status_checks_once(
        store, resolves_now, platform="tiktok", now=reconcile_at, user_id=user.id
    )

    assert summary.published == 1
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_end_to_end_crash_recovery_against_real_postgres(store):
    """A worker claim that crashed before submission (Case A: no
    platform_post_id) — crash_recovery.recover_stale_posts_once, the real
    module, against real Postgres, requeues it to PENDING."""
    user = store.get_or_create_local_user()
    video = store.insert_video("hash-crash", "crash.mp4", "/incoming/crash.mp4", NOW.isoformat(), user_id=user.id)
    post = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    stale = (NOW_UTC - timedelta(hours=1)).isoformat()
    store.update_platform_post(post.id, updated_at=stale, status="PUBLISHING")

    summary = crash_recovery.recover_stale_posts_once(
        store, FakePublisher(), platform="tiktok", now=NOW_UTC, user_id=user.id
    )

    assert summary.requeued == 1
    assert store.get_platform_post(video.id, "tiktok").status == "PENDING"


def test_migrations_are_idempotent_on_reopen():
    """Opening a second PostgresContentStore against an already-migrated
    schema must not fail or re-apply anything — proves apply_migrations()'s
    idempotency against a real database, not just its own logic in
    isolation. Compares against the real migrations directory rather than
    a hardcoded count, so this doesn't need editing every time a new
    migration file is added (Milestone 3.4 added a second one,
    0002_add_media_storage_columns.sql — this test's own count was
    hardcoded to 1 and had to be fixed here for exactly that reason)."""
    from content_automation.persistence.postgres_migrate import MIGRATIONS_DIR

    expected = len(list(MIGRATIONS_DIR.glob("*.sql")))

    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA):
        pass
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as store:
        count = store._conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        assert count == expected
