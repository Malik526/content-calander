"""Tests for Milestone 3.13 (Reconciliation + Recovery) on the hosted worker:
the submission checkpoint (scheduling/publish_tiktok.py), crash recovery's
checkpoint-aware cases (scheduling/crash_recovery.py), UNKNOWN parking
(reconciliation.py), manual recovery (scheduling/manual_recovery.py),
per-connection token-refresh locking (publishing/tiktok/credential_store.py),
persisted publish-time media metadata, and silent-video support.

Same approach as test_hosted_worker.py: state is built through the real
hosted path (upload -> slot -> assign -> caption), behavior tests run on
SQLite and real Postgres (throwaway schema; skipped without DATABASE_URL),
concurrency tests are Postgres-only. No real TikTok call is ever made — the
real TikTokPublisher is exercised with requests mocked. A "crash" is
simulated with a BaseException, which no handler in the worker catches —
the same effect as the process dying at that line."""

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
import requests

from content_automation.config import DATABASE_URL, POSTGRES_SCHEMA, POSTGRES_TEST_SCHEMA, RETRY_BACKOFF_MINUTES
from content_automation.media import inspection
from content_automation.media.caption_editing import save_caption
from content_automation.media.media_storage import create_video_from_upload
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult
from content_automation.publishing.tiktok import auth as tiktok_auth
from content_automation.publishing.tiktok import credential_store
from content_automation.publishing.tiktok import publisher as tp
from content_automation.scheduling import publish_tiktok
from content_automation.scheduling.hosted_worker import run_hosted_cycle
from content_automation.scheduling.manual_recovery import RetryRejectedError, retry_platform_post
from content_automation.scheduling.queue_assignment import assign_video_to_slot
from content_automation.storage.local import LocalStorage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg/ffprobe not on PATH")

PG_SCHEMA = f"{POSTGRES_TEST_SCHEMA}_recovery"
assert PG_SCHEMA != POSTGRES_SCHEMA
NEEDS_PG = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not configured — Postgres integration tests skipped")
NY = ZoneInfo("America/New_York")


class SimulatedCrash(BaseException):
    """The process dying at this point: not an Exception, so nothing in the
    worker handles it and no further write happens."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(hours: float = 2) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _local(minutes_from_now: int) -> str:
    moment = datetime.now(timezone.utc).astimezone(NY) + timedelta(minutes=minutes_from_now)
    return moment.replace(second=0, microsecond=0, tzinfo=None).isoformat()


def _after_backoff() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=RETRY_BACKOFF_MINUTES[0] + 1)


# --- fixtures ----------------------------------------------------------------------

def _make_video(path: Path, *, audio: bool) -> Path:
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-shortest"]
    subprocess.run([*args, "-c:v", "libx264", "-loglevel", "error", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def video_file(tmp_path_factory):
    return _make_video(tmp_path_factory.mktemp("media") / "clip.mp4", audio=True)


@pytest.fixture(scope="module")
def silent_video_file(tmp_path_factory):
    return _make_video(tmp_path_factory.mktemp("media") / "silent.mp4", audio=False)


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


def _truncate(store):
    store._conn.execute(
        "TRUNCATE users, videos, content_slots, platform_posts, platform_connections, platform_credentials "
        "RESTART IDENTITY CASCADE"
    )


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=NEEDS_PG)])
def store(request, tmp_path, _pg_schema):
    if request.param == "sqlite":
        with ContentStore(db_path=tmp_path / "test.db") as s:
            yield s
        return
    with _pg_store() as s:
        _truncate(s)
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


@dataclass
class FakePublisher:
    """No submission checkpoint (like any publisher before 3.13)."""
    publish_result: PublishResult | BaseException = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult | BaseException = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)

    def publish(self, video_path, caption, on_platform_post_id=None):
        self.publish_calls.append(caption)
        if isinstance(self.publish_result, BaseException):
            raise self.publish_result
        return self.publish_result

    def get_status(self, platform_post_id):
        self.status_calls.append(platform_post_id)
        if isinstance(self.status_result, BaseException):
            raise self.status_result
        return self.status_result


@dataclass
class CheckpointPublisher(FakePublisher):
    """Honors the 3.13 contract like TikTokPublisher: init -> report id ->
    media transfer. crash_at / upload_error inject the failure point."""
    reports_platform_post_id_before_media_transfer = True
    crash_at: str | None = None  # "after_init" | "during_upload"
    upload_error: PublishError | None = None
    uploads: int = 0
    next_id: int = 1

    def publish(self, video_path, caption, on_platform_post_id=None):
        self.publish_calls.append(caption)
        publish_id = f"pub_{self.next_id}"
        self.next_id += 1
        # TikTok has now accepted init and issued publish_id...
        if self.crash_at == "after_init":
            raise SimulatedCrash()  # ...and the worker dies before persisting it.
        on_platform_post_id(publish_id)
        if self.crash_at == "during_upload":
            raise SimulatedCrash()
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads += 1
        return PublishResult(platform_post_id=publish_id, status="PROCESSING_UPLOAD")


def _hosted_user(store, email="creator@example.com"):
    user = store.create_user(email, None, _iso_now())
    store.create_auth_identity(user.id, "supabase", f"sub-{email}", email, _iso_now())
    return user


def _scheduled_post(store, storage, user, video_file, tmp_path, *, name="clip", minutes_from_now=-5):
    upload = tmp_path / f"upload-{name}-{user.id}.mp4"
    shutil.copy(video_file, upload)
    video = create_video_from_upload(
        store, storage, user.id, local_path=upload, original_filename=f"{name}.mp4",
        file_hash=f"hash-{name}", file_size_bytes=upload.stat().st_size, created_at=_iso_now(),
    )
    scheduled_at = _local(minutes_from_now)
    store.insert_slot_if_missing(scheduled_at, None, None, _iso_now(), user_id=user.id, timezone="America/New_York")
    slot = next(s for s in store.list_content_slots_for_user(user.id, "2000-01-01T00:00:00", "2200-01-01T00:00:00")
                if s.scheduled_at == scheduled_at)
    assign_video_to_slot(store, video.id, slot.id, _iso_now(), user_id=user.id)
    save_caption(store, store.get_video(video.id), f"caption {name}")
    return store.get_platform_post(video.id, "tiktok")


def _cycle(store, storage, publisher, **kwargs):
    return run_hosted_cycle(store, storage, publisher_factory=lambda *_: publisher, **kwargs)


def _row(store, post):
    return store.get_platform_post(post.video_id, "tiktok")


def _make_stale(store, post):
    """The worker that owned this row died long enough ago for recovery."""
    store.update_platform_post(post.id, updated_at=_hours_ago())


# --- 1. crash during the submission request ------------------------------------------

def test_crash_after_platform_accepted_init_but_before_id_persisted_is_retried_without_duplicate(
    store, storage, video_file, tmp_path,
):
    """TikTok issued a publish_id, the worker died before persisting it. No
    media was sent (the id is persisted before the upload), so nothing can
    have been posted: recovery schedules a bounded retry, and the eventual
    publish is the only one that ever transferred media."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    crashing = CheckpointPublisher(crash_at="after_init")

    with pytest.raises(SimulatedCrash):
        _cycle(store, storage, crashing)
    row = _row(store, post)
    assert (row.status, row.platform_post_id, row.submission_state) == ("PUBLISHING", None, "AWAITING_PLATFORM_ID")
    assert row.submission_started_at is not None
    assert crashing.uploads == 0

    _make_stale(store, post)
    restarted = CheckpointPublisher()
    summary = _cycle(store, storage, restarted)

    row = _row(store, post)
    assert summary.recovered == 1
    assert (row.status, row.retry_count, row.failure_code, row.submission_state) == (
        "PENDING", 1, "SUBMISSION_INTERRUPTED", None)
    assert restarted.publish_calls == []  # waits for the retry backoff, like any retry

    _cycle(store, storage, restarted, now_utc=_after_backoff())
    assert restarted.uploads == 1
    assert crashing.uploads + restarted.uploads == 1
    assert _row(store, post).status == "PUBLISHED"


def test_crash_after_id_persisted_is_polled_never_resubmitted(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)

    with pytest.raises(SimulatedCrash):
        _cycle(store, storage, CheckpointPublisher(crash_at="during_upload"))
    assert _row(store, post).platform_post_id == "pub_1"

    _make_stale(store, post)
    restarted = CheckpointPublisher()
    _cycle(store, storage, restarted)

    assert restarted.publish_calls == []
    assert restarted.status_calls == ["pub_1"]
    assert (_row(store, post).status, _row(store, post).platform_post_id) == ("PUBLISHED", "pub_1")


def test_ambiguous_upload_failure_is_reconciled_not_resubmitted(store, storage, video_file, tmp_path):
    """Before 3.13 an upload that timed out after TikTok had the bytes was a
    retryable pre-submission failure — i.e. a duplicate post. Now the id is
    already persisted, so it goes to status checks only."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = CheckpointPublisher(
        upload_error=PublishError("read timed out", reason_code="UPLOAD_NETWORK_ERROR"),
        status_result=PublishStatusResult(status="PROCESSING_UPLOAD"),
    )

    summary = _cycle(store, storage, publisher)
    row = _row(store, post)
    assert summary.retry_scheduled == 0
    assert (row.status, row.platform_post_id, row.retry_count) == ("PUBLISHING", "pub_1", 0)

    publisher.status_result = PublishStatusResult(status="PUBLISH_COMPLETE")
    for _ in range(3):
        _cycle(store, storage, publisher, now_utc=_after_backoff())

    assert len(publisher.publish_calls) == 1
    assert publisher.status_calls and set(publisher.status_calls) == {"pub_1"}
    assert _row(store, post).status == "PUBLISHED"


def test_real_tiktok_publisher_persists_publish_id_before_uploading(monkeypatch, store, storage, video_file, tmp_path):
    """The real TikTokPublisher + submission checkpoint, HTTP mocked: when
    the upload PUT starts, the row already carries the publish_id."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    seen_at_upload = []

    def fake_post(url, **_kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _Response({"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600})
        if url == tp.INIT_URL:
            return _Response({"publish_id": "v_pub_real", "upload_url": "https://upload.example/x"})
        return _Response({"status": "PUBLISH_COMPLETE"})

    def fake_put(url, **_kwargs):
        seen_at_upload.append(_row(store, post).platform_post_id)
        raise requests.ConnectionError("connection reset during upload")

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.requests, "put", fake_put)
    publisher = tp.TikTokPublisher(access_token_provider=lambda: "token")

    _cycle(store, storage, publisher)

    assert seen_at_upload == ["v_pub_real"]
    row = _row(store, post)
    assert (row.status, row.platform_post_id, row.retry_count) == ("PUBLISHING", "v_pub_real", 0)
    _cycle(store, storage, publisher, now_utc=_after_backoff())
    assert _row(store, post).status == "PUBLISHED"  # by status check, not a second upload
    assert seen_at_upload == ["v_pub_real"]


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": self._data, "error": {"code": "ok"}}


def test_unknowable_submission_is_parked_unknown_and_not_retried(store, storage, video_file, tmp_path):
    """A publisher without the checkpoint died mid-request: it may have been
    accepted and there is no id to check. Never resubmitted automatically."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    with pytest.raises(SimulatedCrash):
        _cycle(store, storage, FakePublisher(publish_result=SimulatedCrash()))
    assert _row(store, post).submission_state == "SUBMITTING"

    _make_stale(store, post)
    restarted = FakePublisher()
    summary = _cycle(store, storage, restarted)
    assert summary.unknown == 1
    row = _row(store, post)
    assert (row.status, row.failure_code) == ("UNKNOWN", "SUBMISSION_OUTCOME_UNKNOWN")

    for _ in range(2):
        summary = _cycle(store, storage, restarted, now_utc=_after_backoff())
    assert restarted.publish_calls == [] and restarted.status_calls == []
    assert summary.users == 0  # UNKNOWN is not automatic work
    assert _row(store, post).status == "UNKNOWN"


# --- 3/6. manual recovery and unknown-state transitions ----------------------------------

def test_unknown_without_id_needs_confirmation_then_resubmits(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at=_iso_now(), status="UNKNOWN",
                               failure_code="SUBMISSION_OUTCOME_UNKNOWN")

    with pytest.raises(RetryRejectedError) as rejected:
        retry_platform_post(store, _row(store, post), user_id=user.id)
    assert rejected.value.code == "CONFIRMATION_REQUIRED"
    assert _row(store, post).status == "UNKNOWN"

    retried = retry_platform_post(store, _row(store, post), user_id=user.id, confirm_not_published=True)
    assert (retried.status, retried.retry_count, retried.failure_code) == ("PENDING", 0, "SUBMISSION_OUTCOME_UNKNOWN")

    publisher = CheckpointPublisher()
    _cycle(store, storage, publisher, now_utc=_after_backoff())
    assert _row(store, post).status == "PUBLISHED"


def test_unknown_with_id_rechecks_status_and_never_resubmits(store, storage, video_file, tmp_path):
    """E.g. TikTok was disconnected while checking (reconciliation parked it);
    after reconnecting, retry re-checks the existing submission."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at=_iso_now(), status="PUBLISHING", platform_post_id="pub_old")
    reauth = PublishError("refresh token revoked", reason_code="REAUTHORIZATION_REQUIRED")
    publisher = CheckpointPublisher(status_result=reauth)

    summary = _cycle(store, storage, publisher)
    assert summary.unknown == 1
    row = _row(store, post)
    assert (row.status, row.failure_code, row.platform_post_id) == ("UNKNOWN", "REAUTHORIZATION_REQUIRED", "pub_old")

    retried = retry_platform_post(store, row, user_id=user.id)
    assert (retried.status, retried.platform_post_id, retried.status_check_count) == ("PUBLISHING", "pub_old", 0)

    publisher.status_result = PublishStatusResult(status="PUBLISH_COMPLETE")
    _cycle(store, storage, publisher)
    assert publisher.publish_calls == []
    assert _row(store, post).status == "PUBLISHED"


def test_status_that_never_resolves_is_parked_unknown(monkeypatch, store, storage, video_file, tmp_path):
    from content_automation.scheduling import reconciliation

    monkeypatch.setattr(reconciliation, "STATUS_CHECK_MAX_ATTEMPTS", 3)
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    publisher = CheckpointPublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    _cycle(store, storage, publisher)  # submit + inline poll (check 1)
    later = datetime.now(timezone.utc)
    for _ in range(4):
        later += timedelta(hours=1)
        _cycle(store, storage, publisher, now_utc=later)

    row = _row(store, post)
    assert (row.status, row.failure_code, row.platform_post_id) == ("UNKNOWN", "STATUS_UNRESOLVED", "pub_1")
    assert len(publisher.publish_calls) == 1


def test_retry_failed_without_id_requeues_with_fresh_budget_and_keeps_history(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at=_iso_now(), status="FAILED", retry_count=4,
                               failure_code="NETWORK_ERROR", failure_reason="connection reset")

    retried = retry_platform_post(store, _row(store, post), user_id=user.id)

    assert (retried.status, retried.retry_count) == ("PENDING", 0)
    assert retried.next_retry_at is not None
    assert (retried.failure_code, retried.failure_reason) == ("NETWORK_ERROR", "connection reset")
    publisher = CheckpointPublisher()
    _cycle(store, storage, publisher)
    assert publisher.publish_calls == []  # not before next_retry_at
    _cycle(store, storage, publisher, now_utc=_after_backoff())
    assert _row(store, post).status == "PUBLISHED"


def test_retry_platform_reported_failure_resubmits_as_a_new_submission(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at=_iso_now(), status="FAILED", platform_post_id="pub_rejected",
                               failure_code="internal", status_check_count=2)

    retried = retry_platform_post(store, _row(store, post), user_id=user.id)
    assert (retried.status, retried.platform_post_id, retried.status_check_count) == ("PENDING", None, 0)


@pytest.mark.parametrize("status", ["PENDING", "PUBLISHING", "PUBLISHED"])
def test_retry_rejects_posts_that_are_not_failed_or_unknown(store, storage, video_file, tmp_path, status):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at=_iso_now(), status=status,
                               platform_post_id="pub_1" if status != "PENDING" else None)
    before = _row(store, post)

    with pytest.raises(RetryRejectedError) as rejected:
        retry_platform_post(store, before, user_id=user.id, confirm_not_published=True)

    assert rejected.value.code == "NOT_RETRYABLE"
    assert _row(store, post) == before


def test_retry_with_stale_snapshot_loses_cleanly(store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    store.update_platform_post(post.id, updated_at="2026-01-01T00:00:00+00:00", status="FAILED")
    snapshot = _row(store, post)
    store.update_platform_post(post.id, updated_at=_iso_now(), status="FAILED", failure_code="X")  # someone else

    with pytest.raises(RetryRejectedError) as rejected:
        retry_platform_post(store, snapshot, user_id=user.id)
    assert rejected.value.code == "CONCURRENT_UPDATE"
    assert _row(store, post).status == "FAILED"


# --- 4/5. media metadata and silent video ----------------------------------------------

def test_publish_time_inspection_is_persisted_and_reused_by_retries(monkeypatch, store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    probes = []
    real_inspect = inspection.inspect_media

    def counting_inspect(path, **kwargs):
        probes.append(path)
        return real_inspect(path, **kwargs)

    monkeypatch.setattr(publish_tiktok.media, "inspect_media", counting_inspect)
    publisher = CheckpointPublisher()
    # First attempt fails before submission (retryable) — after inspection.
    failing = FakePublisher(publish_result=PublishError("connection reset", reason_code="NETWORK_ERROR"))
    _cycle(store, storage, failing)
    assert len(probes) == 1

    video = store.get_video(post.video_id)
    assert (video.container, video.video_codec, video.audio_codec) == ("mov", "h264", "aac")
    assert (video.width, video.height) == (320, 240)
    assert video.fps == pytest.approx(24.0)
    assert video.duration_seconds == pytest.approx(1.0, abs=0.2)

    _cycle(store, storage, publisher, now_utc=_after_backoff())
    assert len(probes) == 1  # the retry used the stored metadata
    assert _row(store, post).status == "PUBLISHED"


def test_metadata_persist_failure_does_not_block_publishing(monkeypatch, store, storage, video_file, tmp_path):
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    real_update = store.update_video

    def failing_update(video_id, **fields):
        if "container" in fields:
            raise RuntimeError("db hiccup")
        return real_update(video_id, **fields)

    monkeypatch.setattr(store, "update_video", failing_update)
    _cycle(store, storage, CheckpointPublisher())
    assert _row(store, post).status == "PUBLISHED"
    assert store.get_video(post.video_id).container is None


def test_silent_video_publishes(store, storage, silent_video_file, tmp_path):
    """TikTok's documented media requirements include no audio track."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, silent_video_file, tmp_path)

    _cycle(store, storage, CheckpointPublisher())

    assert _row(store, post).status == "PUBLISHED"
    video = store.get_video(post.video_id)
    assert (video.video_codec, video.audio_codec) == ("h264", None)


def test_real_tiktok_publisher_accepts_silent_video(monkeypatch, silent_video_file):
    monkeypatch.setattr(tp.requests, "post", lambda url, **_k: _Response(
        {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600}
        if url == tp.CREATOR_INFO_URL else {"publish_id": "p", "upload_url": "https://u"}))
    monkeypatch.setattr(tp.requests, "put", lambda *_a, **_k: type("R", (), {"status_code": 201, "text": ""})())

    result = tp.TikTokPublisher(access_token_provider=lambda: "t").publish(silent_video_file, "caption")
    assert result.platform_post_id == "p"


def test_local_ingestion_still_requires_audio(silent_video_file):
    with pytest.raises(inspection.NoAudioStreamError):
        inspection.inspect_media(silent_video_file)


# --- isolation and convergence ---------------------------------------------------------

def test_one_users_recovery_failure_does_not_stop_other_users(store, storage, video_file, tmp_path):
    alice = _hosted_user(store, "alice@example.com")
    bob = _hosted_user(store, "bob@example.com")
    alice_post = _scheduled_post(store, storage, alice, video_file, tmp_path, name="a")
    bob_post = _scheduled_post(store, storage, bob, video_file, tmp_path, name="b")
    # Alice has a stale submitted row whose recovery check blows up unexpectedly.
    store.claim_platform_post(alice_post.id, updated_at=_hours_ago(), user_id=alice.id)
    store.update_platform_post(alice_post.id, updated_at=_hours_ago(), platform_post_id="pub_alice")
    publishers = {alice.id: FakePublisher(status_result=RuntimeError("unexpected")), bob.id: CheckpointPublisher()}

    summary = run_hosted_cycle(store, storage, publisher_factory=lambda _s, uid: publishers[uid])

    assert summary.user_errors == [alice.id]
    assert _row(store, alice_post).status == "PUBLISHING"  # untouched, retried next cycle
    assert _row(store, bob_post).status == "PUBLISHED"


def test_worker_restart_converges_every_state_safely(store, storage, video_file, tmp_path):
    """One restart over every interrupted/terminal state, then more cycles:
    each row reaches a safe state, nothing is resubmitted that may have been
    accepted, and further cycles change nothing."""
    user = _hosted_user(store)
    names = ("never_submitted", "awaiting_id", "unknowable", "accepted", "published", "failed", "unknown")
    posts = {name: _scheduled_post(store, storage, user, video_file, tmp_path, name=name, minutes_from_now=-5 - i)
             for i, name in enumerate(names)}
    stale = _hours_ago()
    states = {
        "never_submitted": {"status": "PUBLISHING"},
        "awaiting_id": {"status": "PUBLISHING", "submission_state": "AWAITING_PLATFORM_ID"},
        "unknowable": {"status": "PUBLISHING", "submission_state": "SUBMITTING"},
        "accepted": {"status": "PUBLISHING", "platform_post_id": "pub_accepted"},
        "published": {"status": "PUBLISHED", "platform_post_id": "pub_done"},
        "failed": {"status": "FAILED", "failure_code": "CAPTION_TOO_LONG"},
        "unknown": {"status": "UNKNOWN", "failure_code": "SUBMISSION_OUTCOME_UNKNOWN"},
    }
    for name, fields in states.items():
        store.update_platform_post(posts[name].id, updated_at=stale, **fields)
    publisher = CheckpointPublisher(next_id=100)

    _cycle(store, storage, publisher)
    later = _after_backoff()
    _cycle(store, storage, publisher, now_utc=later)
    final = {name: _row(store, post) for name, post in posts.items()}

    assert final["never_submitted"].status == "PUBLISHED"
    assert final["awaiting_id"].status == "PUBLISHED" and final["awaiting_id"].retry_count == 1
    assert final["unknowable"].status == "UNKNOWN"
    assert (final["accepted"].status, final["accepted"].platform_post_id) == ("PUBLISHED", "pub_accepted")
    assert final["published"].status == "PUBLISHED" and final["published"].updated_at == stale
    assert final["failed"].status == "FAILED" and final["failed"].updated_at == stale
    assert final["unknown"].status == "UNKNOWN" and final["unknown"].updated_at == stale
    assert sorted(publisher.publish_calls) == ["caption awaiting_id", "caption never_submitted"]

    _cycle(store, storage, publisher, now_utc=later + timedelta(hours=1))
    assert {name: _row(store, post) for name, post in posts.items()} == final


# --- 2. token refresh locking (Postgres) -------------------------------------------------

def _expiring_token():
    now = datetime.now(timezone.utc)
    return {
        "access_token": "old-access", "refresh_token": "refresh-1",
        "access_token_expires_at": (now - timedelta(minutes=1)).isoformat(),
        "refresh_token_expires_at": (now + timedelta(days=300)).isoformat(), "open_id": "o", "scope": "video.publish",
    }


def _fresh_token(n):
    far = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return {"access_token": f"new-access-{n}", "refresh_token": f"refresh-{n + 1}", "access_token_expires_at": far,
            "refresh_token_expires_at": far, "open_id": "o", "scope": "video.publish"}


@pytest.fixture
def encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(credential_store, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _connections(n_users):
    """n users, each with one connection holding an expiring token."""
    with _pg_store() as setup:
        _truncate(setup)
        ids = []
        for i in range(n_users):
            user = _hosted_user(setup, f"u{i}@example.com")
            connection = setup.get_or_create_platform_connection(user.id, "tiktok")
            credential_store.save_hosted_tiktok_token(setup, connection.id, _expiring_token())
            ids.append(connection.id)
    return ids


def _run_threads(n, target):
    barrier, results, errors = threading.Barrier(n), [], []

    def run():
        try:
            with _pg_store() as store:
                barrier.wait()
                results.append(target(store))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, errors


@NEEDS_PG
def test_concurrent_token_refresh_refreshes_once_and_everyone_reuses_it(monkeypatch, _pg_schema, encryption_key):
    [connection_id] = _connections(1)
    calls = []

    def slow_refresh(refresh_token):
        calls.append(refresh_token)
        time.sleep(0.3)  # long enough for every other worker to be waiting
        return _fresh_token(len(calls))

    monkeypatch.setattr(tiktok_auth, "refresh_access_token", slow_refresh)

    results, errors = _run_threads(6, lambda s: credential_store.get_hosted_tiktok_access_token(s, connection_id))

    assert errors == []
    assert calls == ["refresh-1"]
    assert results == ["new-access-1"] * 6


@NEEDS_PG
def test_refresh_lock_is_per_connection(monkeypatch, _pg_schema, encryption_key):
    first, second = _connections(2)
    started = threading.Event()
    release = threading.Event()

    def refresh(refresh_token):
        if not started.is_set():
            started.set()
            assert release.wait(10)  # hold `first`'s lock until the other connection has refreshed
        return _fresh_token(1)

    monkeypatch.setattr(tiktok_auth, "refresh_access_token", refresh)
    holder = threading.Thread(target=lambda: _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, first)))
    holder.start()
    assert started.wait(10)
    try:
        assert _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, second)) == "new-access-1"
    finally:
        release.set()
        holder.join(10)


def _with_store(fn):
    with _pg_store() as store:
        return fn(store)


@NEEDS_PG
def test_failed_refresh_releases_the_lock(monkeypatch, _pg_schema, encryption_key):
    [connection_id] = _connections(1)
    outcomes = iter([tiktok_auth.TikTokAuthError("token endpoint down", reason_code="NETWORK_ERROR"), _fresh_token(2)])

    def refresh(_refresh_token):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(tiktok_auth, "refresh_access_token", refresh)
    with pytest.raises(tiktok_auth.TikTokAuthError):
        _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, connection_id))
    # The lock was released by the rollback: the next caller gets it at once.
    started = time.monotonic()
    assert _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, connection_id)) == "new-access-2"
    assert time.monotonic() - started < 5


@NEEDS_PG
def test_lock_wait_is_bounded_and_retryable(monkeypatch, _pg_schema, encryption_key):
    from content_automation.persistence import postgres_content_store
    from content_automation.scheduling import retry_classification

    [connection_id] = _connections(1)
    monkeypatch.setattr(postgres_content_store, "CREDENTIAL_REFRESH_LOCK_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(tiktok_auth, "refresh_access_token", lambda _t: pytest.fail("must not refresh"))
    with _pg_store() as holder, holder.credential_refresh_lock(connection_id):
        with pytest.raises(tiktok_auth.TikTokAuthError) as busy:
            _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, connection_id))
    assert busy.value.reason_code == "CREDENTIAL_REFRESH_BUSY"
    assert retry_classification.is_retryable("CREDENTIAL_REFRESH_BUSY")


@NEEDS_PG
def test_refresh_logs_ids_never_tokens(monkeypatch, caplog, _pg_schema, encryption_key):
    [connection_id] = _connections(1)
    monkeypatch.setattr(tiktok_auth, "refresh_access_token", lambda _t: _fresh_token(7))
    with caplog.at_level("INFO"):
        _with_store(lambda s: credential_store.get_hosted_tiktok_access_token(s, connection_id))
    assert f"event=credential_refreshed platform_connection_id={connection_id}" in caplog.text
    for secret in ("new-access-7", "refresh-1", "refresh-8", "old-access"):
        assert secret not in caplog.text


# --- concurrency on manual retry (Postgres) ----------------------------------------------

@NEEDS_PG
def test_concurrent_manual_retries_apply_exactly_once(_pg_schema, storage, video_file, tmp_path):
    with _pg_store() as setup:
        _truncate(setup)
        user = _hosted_user(setup)
        post = _scheduled_post(setup, storage, user, video_file, tmp_path)
        setup.update_platform_post(post.id, updated_at=_iso_now(), status="FAILED", failure_code="NETWORK_ERROR")
        snapshot = _row(setup, post)

    def retry(store):
        try:
            retry_platform_post(store, snapshot, user_id=user.id)
            return "ok"
        except RetryRejectedError as exc:
            return exc.code

    results, errors = _run_threads(5, retry)
    assert errors == []
    assert sorted(results) == ["CONCURRENT_UPDATE"] * 4 + ["ok"]
    with _pg_store() as check:
        assert _row(check, post).status == "PENDING"


# --- Milestone 3.14 follow-up: optional caption ---------------------------------

def test_captionless_post_publishes_and_old_caption_missing_failure_recovers_with_retry(
    store, storage, video_file, tmp_path,
):
    """A post that FAILED with CAPTION_MISSING before captions became optional
    goes through the normal Retry -> PENDING -> claim path and publishes
    without a caption. Retry/idempotency rules are unchanged."""
    user = _hosted_user(store)
    post = _scheduled_post(store, storage, user, video_file, tmp_path)
    save_caption(store, store.get_video(post.video_id), "")  # user cleared it
    store.update_platform_post(post.id, updated_at=_iso_now(), status="FAILED", failure_code="CAPTION_MISSING",
                               failure_reason="no stored caption_text")

    retried = retry_platform_post(store, _row(store, post), user_id=user.id)
    assert retried.status == "PENDING"

    publisher = CheckpointPublisher()
    _cycle(store, storage, publisher, now_utc=_after_backoff())

    assert publisher.publish_calls == [None]  # submitted, with no caption
    row = _row(store, post)
    assert (row.status, row.platform_post_id) == ("PUBLISHED", "pub_1")
