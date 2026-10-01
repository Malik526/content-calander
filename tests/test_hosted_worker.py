"""Tests for the hosted scheduler worker (Milestone 3.12):
scheduling/hosted_worker.py, scheduling/hosted_due_selection.py,
publishing/tiktok/hosted_publisher.py, and the hosted-specific changes in
scheduling/publish_tiktok.py (materialization failures, inspection of
never-inspected hosted uploads).

State is built through the real hosted path — create_video_from_upload into
a LocalStorage bucket, queue assignment, caption save — so the worker sees
exactly what production sees. Behavior tests run against SQLite and real
Postgres (throwaway schema; Postgres skipped without DATABASE_URL);
concurrency tests are Postgres-only. No real TikTok call is ever made: the
publisher is a fake, or the real TikTokPublisher failing before any network
call. Times are relative to the real clock, because claims stamp
updated_at with it."""

import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from content_automation.config import DATABASE_URL, POSTGRES_SCHEMA, POSTGRES_TEST_SCHEMA, RETRY_BACKOFF_MINUTES
from content_automation.media.caption_editing import save_caption
from content_automation.media.media_storage import create_video_from_upload
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult
from content_automation.publishing.tiktok import credential_store
from content_automation.publishing.tiktok.hosted_publisher import (
    build_hosted_tiktok_publisher,
    hosted_access_token_provider,
)
from content_automation.publishing.tiktok.auth import TikTokAuthError
from content_automation.scheduling.hosted_due_selection import get_hosted_due_posts
from content_automation.scheduling.hosted_worker import run_hosted_cycle, run_worker_loop
from content_automation.scheduling.queue_assignment import assign_video_to_slot
from content_automation.storage.local import LocalStorage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg/ffprobe not on PATH")

PG_SCHEMA = f"{POSTGRES_TEST_SCHEMA}_worker"
assert PG_SCHEMA != POSTGRES_SCHEMA
NEEDS_PG = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not configured — Postgres integration tests skipped")
NY = ZoneInfo("America/New_York")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local(tz: ZoneInfo, minutes_from_now: int) -> str:
    """A naive wall-clock scheduled_at in `tz`, minute precision."""
    moment = datetime.now(timezone.utc).astimezone(tz) + timedelta(minutes=minutes_from_now)
    return moment.replace(second=0, microsecond=0, tzinfo=None).isoformat()


# --- fixtures ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def video_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac",
         "-shortest", "-loglevel", "error", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def _pg_schema():
    if not DATABASE_URL:
        yield None
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA IF EXISTS "{PG_SCHEMA}" CASCADE')
        admin.execute(f'CREATE SCHEMA "{PG_SCHEMA}"')
    yield PG_SCHEMA
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA IF EXISTS "{PG_SCHEMA}" CASCADE')


def _pg_store():
    from content_automation.persistence.postgres_content_store import PostgresContentStore

    return PostgresContentStore(dsn=DATABASE_URL, schema=PG_SCHEMA)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=NEEDS_PG)])
def backend(request, tmp_path, _pg_schema):
    """(store_factory, store) for one backend, starting empty."""
    if request.param == "sqlite":
        factory = lambda: ContentStore(db_path=tmp_path / "test.db")  # noqa: E731
    else:
        factory = _pg_store
        with factory() as s:
            s._conn.execute("TRUNCATE users, videos, content_slots, platform_posts RESTART IDENTITY CASCADE")
    with factory() as store:
        yield factory, store


@pytest.fixture
def store(backend):
    return backend[1]


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


@dataclass
class FakePublisher:
    publish_result: PublishResult | Exception = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult | Exception = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        with self._lock:
            # Record whether the materialized file really existed at publish time.
            self.publish_calls.append((Path(video_path), Path(video_path).exists(), caption))
        if isinstance(self.publish_result, Exception):
            raise self.publish_result
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        self.status_calls.append(platform_post_id)
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result


def _hosted_user(store, email="creator@example.com", with_login=True):
    user = store.create_user(email, None, _iso_now())
    if with_login:
        store.create_auth_identity(user.id, "supabase", f"sub-{email}", email, _iso_now())
    return user


def _scheduled_post(store, storage, user, video_file, tmp_path, *, scheduled_at=None, tz="America/New_York",
                    caption="Built today.\n\n#coding #saas", name="clip"):
    """Upload -> slot -> assign -> caption, exactly like the hosted product."""
    upload = tmp_path / f"upload-{name}-{user.id}.mp4"
    shutil.copy(video_file, upload)
    video = create_video_from_upload(
        store, storage, user.id, local_path=upload, original_filename=f"{name}.mp4",
        file_hash=f"hash-{name}", file_size_bytes=upload.stat().st_size, created_at=_iso_now(),
    )
    scheduled_at = scheduled_at or _local(ZoneInfo(tz), -5)
    store.insert_slot_if_missing(scheduled_at, None, None, _iso_now(), user_id=user.id, timezone=tz)
    slot = next(s for s in store.list_content_slots_for_user(user.id, "2000-01-01T00:00:00", "2200-01-01T00:00:00")
                if s.scheduled_at == scheduled_at)
    assign_video_to_slot(store, video.id, slot.id, _iso_now(), user_id=user.id)
    save_caption(store, store.get_video(video.id), caption)
    return store.get_platform_post(video.id, "tiktok")


def _cycle(store, storage, publishers, **kwargs):
    return run_hosted_cycle(store, storage, publisher_factory=lambda _s, uid: publishers[uid], **kwargs)


# --- execution through the existing pipeline -----------------------------------------

def test_due_post_is_claimed_materialized_and_published(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = FakePublisher()

    summary = _cycle(store, storage, {user.id: publisher})

    assert summary.claimed == 1 and summary.published == 1
    [(media_path, existed, caption)] = publisher.publish_calls
    assert existed  # materialized from storage before publishing
    assert not media_path.exists()  # temp copy cleaned up afterwards
    assert caption == "Built today.\n\n#coding #saas"  # exact canonical caption
    final = store.get_platform_post(post.video_id, "tiktok")
    assert final.status == "PUBLISHED"
    assert final.platform_post_id == "pub_1"
    assert final.published_at is not None
    # Hosted upload was never inspected; the worker validated it without writing metadata.
    assert store.get_video(post.video_id).container is None


def test_future_post_is_not_executed(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path, scheduled_at=_local(NY, 30))
    publisher = FakePublisher()

    summary = _cycle(store, storage, {user.id: publisher})

    assert summary.claimed == 0
    assert publisher.publish_calls == []
    assert store.get_platform_post(post.video_id, "tiktok").status == "PENDING"


def test_due_time_uses_the_slots_own_timezone(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    la = ZoneInfo("America/Los_Angeles")
    # Due in Los Angeles in 30 minutes — already "past" as a New York wall-clock time.
    post = _scheduled_post(store, storage, user, video_file, tmp_path, scheduled_at=_local(la, 30), tz="America/Los_Angeles")

    now = datetime.now(timezone.utc)
    assert get_hosted_due_posts(store, "tiktok", user.id, now) == []
    assert [p.id for p in get_hosted_due_posts(store, "tiktok", user.id, now + timedelta(minutes=31))] == [post.id]


def test_retryable_failure_uses_existing_backoff(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = FakePublisher(publish_result=PublishError("connection reset", reason_code="NETWORK_ERROR"))

    summary = _cycle(store, storage, {user.id: publisher})

    assert summary.retry_scheduled == 1
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.retry_count, row.failure_code) == ("PENDING", 1, "NETWORK_ERROR")
    assert row.next_retry_at is not None

    # Not retried before next_retry_at...
    _cycle(store, storage, {user.id: publisher})
    assert len(publisher.publish_calls) == 1
    # ...retried once it arrives.
    publisher.publish_result = PublishResult(platform_post_id="pub_2", status="PROCESSING_UPLOAD")
    later = datetime.now(timezone.utc) + timedelta(minutes=RETRY_BACKOFF_MINUTES[0] + 1)
    _cycle(store, storage, {user.id: publisher}, now_utc=later)
    assert len(publisher.publish_calls) == 2
    assert store.get_platform_post(post.video_id, "tiktok").status == "PUBLISHED"


def test_terminal_failure_persists_status_and_failure_code(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = FakePublisher(publish_result=PublishError("too long", reason_code="CAPTION_TOO_LONG"))

    summary = _cycle(store, storage, {user.id: publisher})

    assert summary.failed == 1
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.failure_code, row.platform_post_id) == ("FAILED", "CAPTION_TOO_LONG", None)


def test_accepted_post_hands_off_to_reconciliation_without_resubmitting(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    _cycle(store, storage, {user.id: publisher})
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.platform_post_id) == ("PUBLISHING", "pub_1")
    assert row.next_status_check_at is not None

    publisher.status_result = PublishStatusResult(status="PUBLISH_COMPLETE")
    _cycle(store, storage, {user.id: publisher}, now_utc=datetime.now(timezone.utc) + timedelta(minutes=2))

    assert len(publisher.publish_calls) == 1  # status checked, never resubmitted
    assert store.get_platform_post(post.video_id, "tiktok").status == "PUBLISHED"


def test_stale_unsubmitted_claim_is_recovered_and_published(store, storage, video_file, tmp_path):
    """Worker died after claiming, before reaching TikTok."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.claim_platform_post(post.id, updated_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), user_id=user.id)
    publisher = FakePublisher()

    _cycle(store, storage, {user.id: publisher})

    assert len(publisher.publish_calls) == 1
    assert store.get_platform_post(post.video_id, "tiktok").status == "PUBLISHED"


def test_stale_submitted_claim_is_polled_never_resubmitted(store, storage, video_file, tmp_path):
    """Worker died after TikTok accepted, before the outcome was recorded."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.claim_platform_post(post.id, updated_at=stale, user_id=user.id)
    store.update_platform_post(post.id, updated_at=stale, platform_post_id="pub_existing")
    publisher = FakePublisher()

    _cycle(store, storage, {user.id: publisher})

    assert publisher.publish_calls == []
    assert publisher.status_calls == ["pub_existing"]
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.platform_post_id) == ("PUBLISHED", "pub_existing")


def test_missing_storage_object_fails_cleanly_instead_of_sticking(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    storage.delete(store.get_video(post.video_id).storage_key)
    publisher = FakePublisher()

    _cycle(store, storage, {user.id: publisher})

    assert publisher.publish_calls == []
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.failure_code) == ("FAILED", "STORAGE_OBJECT_MISSING")


def test_undecodable_hosted_upload_fails_inspection_cleanly(store, storage, tmp_path):
    user = _hosted_user(store)
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    post = _scheduled_post(store, storage, user, junk, tmp_path)
    publisher = FakePublisher()

    _cycle(store, storage, {user.id: publisher})

    assert publisher.publish_calls == []
    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.failure_code) == ("FAILED", "CORRUPT_MEDIA")


# --- tenancy, credentials, isolation ------------------------------------------------

def test_each_user_is_published_with_their_own_publisher_only(store, storage, video_file, tmp_path):
    alice = _hosted_user(store, "alice@example.com")
    bob = _hosted_user(store, "bob@example.com")
    alice_post = _scheduled_post(store, storage, alice, video_file, tmp_path, caption="alice", name="a")
    bob_post = _scheduled_post(store, storage, bob, video_file, tmp_path, caption="bob", name="b")
    publishers = {alice.id: FakePublisher(), bob.id: FakePublisher()}

    _cycle(store, storage, publishers)

    assert [c[2] for c in publishers[alice.id].publish_calls] == ["alice"]
    assert [c[2] for c in publishers[bob.id].publish_calls] == ["bob"]
    assert store.get_platform_post(alice_post.video_id, "tiktok").status == "PUBLISHED"
    assert store.get_platform_post(bob_post.video_id, "tiktok").status == "PUBLISHED"


def test_users_without_a_hosted_login_are_left_to_the_local_cli(store, storage, video_file, tmp_path):
    legacy = _hosted_user(store, "local@pickle-batch.local", with_login=False)
    post = _scheduled_post(store, storage, legacy, video_file, tmp_path)

    summary = run_hosted_cycle(store, storage, publisher_factory=lambda *_: pytest.fail("must not build a publisher"))

    assert summary.users == 0
    assert store.get_platform_post(post.video_id, "tiktok").status == "PENDING"


def test_one_users_failure_does_not_stop_others(store, storage, video_file, tmp_path):
    alice = _hosted_user(store, "alice@example.com")
    bob = _hosted_user(store, "bob@example.com")
    _scheduled_post(store, storage, alice, video_file, tmp_path, name="a")
    bob_post = _scheduled_post(store, storage, bob, video_file, tmp_path, name="b")
    bob_publisher = FakePublisher()

    def factory(_store, user_id):
        if user_id == alice.id:
            raise RuntimeError("boom")
        return bob_publisher

    summary = run_hosted_cycle(store, storage, publisher_factory=factory)

    assert summary.user_errors == [alice.id]
    assert store.get_platform_post(bob_post.video_id, "tiktok").status == "PUBLISHED"


def test_dry_run_claims_and_writes_nothing(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    before = store.get_platform_post(post.video_id, "tiktok")

    summary = run_hosted_cycle(store, storage, dry_run=True, publisher_factory=lambda *_: pytest.fail("no publisher"))

    assert (summary.due, summary.claimed) == (1, 0)
    assert store.get_platform_post(post.video_id, "tiktok") == before


def test_disconnected_tiktok_fails_with_reconnect_code_before_any_network_call(store, storage, video_file, tmp_path):
    """The real hosted publisher: no connection -> terminal REAUTHORIZATION_REQUIRED."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)

    run_hosted_cycle(store, storage, publisher_factory=build_hosted_tiktok_publisher)

    row = store.get_platform_post(post.video_id, "tiktok")
    assert (row.status, row.failure_code) == ("FAILED", "REAUTHORIZATION_REQUIRED")


def test_hosted_token_provider_reads_that_users_stored_credential(store, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(credential_store, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    user = _hosted_user(store)
    other = _hosted_user(store, "other@example.com")
    connection = store.get_or_create_platform_connection(user.id, "tiktok")
    far = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    credential_store.save_hosted_tiktok_token(store, connection.id, {
        "access_token": "user-access-token", "refresh_token": "r", "access_token_expires_at": far,
        "refresh_token_expires_at": far, "open_id": "o", "scope": "video.publish",
    })

    assert hosted_access_token_provider(store, user.id)() == "user-access-token"
    with pytest.raises(TikTokAuthError) as other_error:
        hosted_access_token_provider(store, other.id)()
    assert other_error.value.reason_code == "REAUTHORIZATION_REQUIRED"

    monkeypatch.setattr(credential_store, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())  # rotated key
    with pytest.raises(TikTokAuthError) as rotated:
        hosted_access_token_provider(store, user.id)()
    assert rotated.value.reason_code == "CREDENTIAL_UNAVAILABLE"


def test_worker_loop_runs_one_cycle_and_stops(backend, storage, video_file, tmp_path):
    factory, store = backend
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = FakePublisher()

    run_worker_loop(store_factory=factory, storage=storage, poll_interval_seconds=0, stop=threading.Event(),
                    once=True, publisher_factory=lambda *_: publisher)

    assert store.get_platform_post(post.video_id, "tiktok").status == "PUBLISHED"


# --- Postgres-only: concurrency -------------------------------------------------------

@NEEDS_PG
def test_two_concurrent_workers_publish_a_post_exactly_once(_pg_schema, storage, video_file, tmp_path):
    with _pg_store() as setup:
        setup._conn.execute("TRUNCATE users, videos, content_slots, platform_posts RESTART IDENTITY CASCADE")
        user = _hosted_user(setup)
        post = _scheduled_post(setup, storage, user, video_file, tmp_path)
    publisher = FakePublisher()
    barrier = threading.Barrier(4)
    errors = []

    def worker():
        try:
            with _pg_store() as store:  # each worker has its own connection
                barrier.wait()
                run_hosted_cycle(store, storage, publisher_factory=lambda *_: publisher)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert len(publisher.publish_calls) == 1
    with _pg_store() as check:
        assert check.get_platform_post(post.video_id, "tiktok").status == "PUBLISHED"


@NEEDS_PG
def test_concurrent_web_and_worker_startup_is_migration_safe(_pg_schema):
    """API requests and worker cycles all construct PostgresContentStore,
    which applies migrations; against a schema missing them, simultaneous
    construction must not race (postgres_migrate's advisory lock)."""
    from content_automation.persistence.postgres_content_store import PostgresContentStore

    fresh = f"{PG_SCHEMA}_startup"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA IF EXISTS "{fresh}" CASCADE')
        admin.execute(f'CREATE SCHEMA "{fresh}"')  # like "public", the schema itself already exists
    barrier, errors = threading.Barrier(6), []

    def start():
        try:
            barrier.wait()
            with PostgresContentStore(dsn=DATABASE_URL, schema=fresh) as store:
                store.list_hosted_user_ids_with_platform_work("tiktok")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    try:
        assert errors == []
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{fresh}" CASCADE')
