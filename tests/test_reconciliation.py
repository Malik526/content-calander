"""Tests for reconciliation.py: one-pass automatic status reconciliation
for PUBLISHING platform_posts rows TikTok has already accepted (Milestone
2.1.10). A FakePublisher stands in for TikTokPublisher — no real network
access or TikTok credentials required for the pure-logic tests; a small
number of integration tests use a real TikTokPublisher with mocked HTTP to
prove composition with Milestone 2.1.8's token refresh. Uses a real
(temp-file) ContentStore, injected aware-UTC `now` throughout — no
wall-clock sleeps."""

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from content_automation.scheduling import crash_recovery as cr
from content_automation.scheduling import reconciliation as recon
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult

NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
_video_counter = itertools.count()


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@dataclass
class FakePublisher:
    status_result: PublishStatusResult | Exception = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption))
        raise AssertionError("reconciliation must never call publish()")

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        self.status_calls.append(platform_post_id)
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result


def _insert_video(store, name=None):
    name = name or f"video-{next(_video_counter)}"
    return store.insert_video(f"h-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())


def _publishing_row(
    store, *, platform_post_id="pub_1", updated_at=None, next_status_check_at=None,
    status_check_count=0, platform="tiktok",
):
    """A claimed (PUBLISHING) row, optionally already holding a
    platform_post_id and prior reconciliation scheduling state."""
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, platform, created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    store.update_platform_post(
        record.id, updated_at=updated_at or NOW.isoformat(),
        platform_post_id=platform_post_id,
        next_status_check_at=next_status_check_at,
        status_check_count=status_check_count,
    )
    return store.get_platform_post(video.id, platform)


# ---------------------------------------------------------------------------
# 1-5. selector semantics: which statuses/fields make a row reconcilable
# ---------------------------------------------------------------------------

def test_publishing_row_with_platform_post_id_is_reconcilable(store):
    row = _publishing_row(store)

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 1


def test_publishing_row_without_platform_post_id_is_not_reconciliation_work(store):
    _publishing_row(store, platform_post_id=None)

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 0


def test_pending_row_is_not_reconciliation_work(store):
    video = _insert_video(store)
    store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 0


def test_published_row_is_not_reconciliation_work(store):
    row = _publishing_row(store)
    store.update_platform_post(row.id, updated_at=NOW.isoformat(), status="PUBLISHED", published_at=NOW.isoformat())

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 0


def test_failed_row_is_not_reconciliation_work(store):
    row = _publishing_row(store)
    store.update_platform_post(row.id, updated_at=NOW.isoformat(), status="FAILED", failure_reason="terminal")

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 0


# ---------------------------------------------------------------------------
# 6-8. next_status_check_at boundary
# ---------------------------------------------------------------------------

def test_future_next_status_check_at_is_not_selected(store):
    _publishing_row(store, next_status_check_at=(NOW + timedelta(minutes=5)).isoformat())

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 0


def test_exact_next_status_check_at_boundary_is_selected(store):
    _publishing_row(store, next_status_check_at=NOW.isoformat())

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 1


def test_elapsed_next_status_check_at_is_selected(store):
    _publishing_row(store, next_status_check_at=(NOW - timedelta(minutes=5)).isoformat())

    summary = recon.reconcile_pending_status_checks_once(store, FakePublisher(), now=NOW)

    assert summary.discovered == 1


# ---------------------------------------------------------------------------
# 9-11. PROCESSING keeps the row PUBLISHING and schedules the next check,
# with increasing delay across repeated processing outcomes.
# ---------------------------------------------------------------------------

def test_processing_status_keeps_row_publishing(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert final.status == "PUBLISHING"


def test_processing_status_schedules_next_check_at_first_backoff_interval(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert summary.still_processing == 1
    assert final.status_check_count == 1
    from content_automation.config import STATUS_CHECK_BACKOFF_SECONDS
    assert final.next_status_check_at == (NOW + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[0])).isoformat()


def test_repeated_processing_increases_backoff_delay(store):
    from content_automation.config import STATUS_CHECK_BACKOFF_SECONDS

    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    # First check (count 0 -> 1), due immediately.
    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)
    after_first = store.get_platform_post(row.video_id, "tiktok")
    assert after_first.status_check_count == 1

    # Second check, run once the first interval has elapsed.
    second_now = datetime.fromisoformat(after_first.next_status_check_at)
    recon.reconcile_pending_status_checks_once(store, publisher, now=second_now)
    after_second = store.get_platform_post(row.video_id, "tiktok")

    assert after_second.status_check_count == 2
    assert after_second.next_status_check_at == (second_now + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[1])).isoformat()
    # The second interval is strictly longer than the first.
    assert STATUS_CHECK_BACKOFF_SECONDS[1] > STATUS_CHECK_BACKOFF_SECONDS[0]


def test_backoff_caps_at_last_interval_rather_than_growing_unbounded(store):
    from content_automation.config import STATUS_CHECK_BACKOFF_SECONDS

    row = _publishing_row(store, status_check_count=len(STATUS_CHECK_BACKOFF_SECONDS) + 3)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert final.next_status_check_at == (NOW + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[-1])).isoformat()


# ---------------------------------------------------------------------------
# 12-13. terminal outcomes
# ---------------------------------------------------------------------------

def test_publish_complete_marks_published(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert summary.published == 1
    assert final.status == "PUBLISHED"
    assert final.published_at is not None


def test_terminal_tiktok_failure_marks_failed(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="FAILED", failure_reason="content rejected"))

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert summary.failed == 1
    assert final.status == "FAILED"
    assert final.failure_reason == "content rejected"


# ---------------------------------------------------------------------------
# 14-15. platform_post_id is immutable here; publish() is never called
# ---------------------------------------------------------------------------

def test_platform_post_id_never_changes(store):
    row = _publishing_row(store, platform_post_id="pub_original")
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert final.platform_post_id == "pub_original"


def test_publish_is_never_called_during_reconciliation(store):
    _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_UPLOAD"))

    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    assert publisher.publish_calls == []  # FakePublisher.publish() would also raise if called


# ---------------------------------------------------------------------------
# 16-17. transient status-check failure: no resubmission, reschedules
# ---------------------------------------------------------------------------

def test_transient_status_check_failure_does_not_resubmit(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishError("network blip", reason_code="NETWORK_ERROR"))

    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert final.status == "PUBLISHING"
    assert publisher.publish_calls == []


def test_transient_status_check_failure_schedules_another_check(store):
    from content_automation.config import STATUS_CHECK_BACKOFF_SECONDS

    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishError("network blip", reason_code="NETWORK_ERROR"))

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert len(summary.errors) == 1
    assert final.status_check_count == 1
    assert final.next_status_check_at == (NOW + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[0])).isoformat()


# ---------------------------------------------------------------------------
# 18-19. composition with Milestone 2.1.8 token lifecycle — real
# TikTokPublisher, mocked HTTP, proving reconciliation's get_status() call
# goes through the same get_access_token() path as everything else.
# ---------------------------------------------------------------------------

def test_token_refresh_can_happen_during_reconciliation(monkeypatch, store, tmp_path):
    from content_automation.publishing.tiktok import auth as ta
    from content_automation.publishing.tiktok import publisher as tp

    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tok.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(ta, "TIKTOK_REFRESH_LOCK_PATH", tmp_path / "lock")
    ta.save_token({
        "access_token": "stale_access", "refresh_token": "refresh_xyz",
        "access_token_expires_at": (NOW - timedelta(minutes=10)).isoformat(),
        "refresh_token_expires_at": (NOW + timedelta(days=300)).isoformat(),
        "open_id": "u", "scope": "s",
    })

    bearer_tokens_seen = []

    class _FakeResponse:
        def __init__(self, body, status_code=200):
            self._body, self.status_code, self.text = body, status_code, str(body)

        def json(self):
            return self._body

    def _fake_post(url, headers=None, json=None, data=None, **kwargs):
        if headers and "Authorization" in headers:
            bearer_tokens_seen.append(headers["Authorization"])
        if url == ta.TOKEN_URL:
            return _FakeResponse({
                "access_token": "refreshed_access", "refresh_token": "refreshed_refresh",
                "expires_in": 86400, "refresh_expires_in": 31536000,
            })
        if url == tp.STATUS_URL:
            return _FakeResponse({
                "data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok", "message": "OK", "log_id": "x"},
            })
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr(tp.requests, "post", _fake_post)

    row = _publishing_row(store)
    publisher = tp.TikTokPublisher()

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    assert summary.published == 1
    assert "Bearer refreshed_access" in bearer_tokens_seen
    assert "Bearer stale_access" not in bearer_tokens_seen


def test_reauthorization_required_marks_failed_and_stops_polling(monkeypatch, store, tmp_path):
    from content_automation.publishing.tiktok import auth as ta
    from content_automation.publishing.tiktok import publisher as tp

    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tok.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(ta, "TIKTOK_REFRESH_LOCK_PATH", tmp_path / "lock")
    ta.save_token({
        "access_token": "stale_access", "refresh_token": "revoked_refresh",
        "access_token_expires_at": (NOW - timedelta(minutes=10)).isoformat(),
        "refresh_token_expires_at": (NOW + timedelta(days=300)).isoformat(),
        "open_id": "u", "scope": "s",
    })

    class _FakeResponse:
        def __init__(self, body, status_code):
            self._body, self.status_code, self.text = body, status_code, str(body)

        def json(self):
            return self._body

    monkeypatch.setattr(
        tp.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=401)
    )

    row = _publishing_row(store)
    publisher = tp.TikTokPublisher()

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert len(summary.errors) == 1
    assert summary.failed == 1
    # Terminal — a human must reconnect TikTok; must not sit PUBLISHING and
    # get silently re-polled forever. Never resubmitted either way.
    assert final.status == "FAILED"
    assert "tiktok_auth.py --authorize" in final.failure_reason
    assert final.platform_post_id == row.platform_post_id


def test_reauthorization_required_does_not_schedule_another_check(monkeypatch, store, tmp_path):
    """A row marked FAILED for this reason must never be selected by
    reconciliation again — it leaves PUBLISHING entirely, so
    get_reconcilable_platform_posts excludes it structurally regardless of
    next_status_check_at, exactly like any other terminal outcome."""
    from content_automation.publishing.tiktok import auth as ta
    from content_automation.publishing.tiktok import publisher as tp

    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tok.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(ta, "TIKTOK_REFRESH_LOCK_PATH", tmp_path / "lock")
    ta.save_token({
        "access_token": "stale_access", "refresh_token": "revoked_refresh",
        "access_token_expires_at": (NOW - timedelta(minutes=10)).isoformat(),
        "refresh_token_expires_at": (NOW + timedelta(days=300)).isoformat(),
        "open_id": "u", "scope": "s",
    })

    class _FakeResponse:
        def __init__(self, body, status_code):
            self._body, self.status_code, self.text = body, status_code, str(body)

        def json(self):
            return self._body

    monkeypatch.setattr(
        tp.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=401)
    )

    _publishing_row(store)
    publisher = tp.TikTokPublisher()
    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW + timedelta(days=1))

    assert summary.discovered == 0


def test_transient_auth_network_failure_stays_publishing_and_reschedules(monkeypatch, store, tmp_path):
    """The retryable counterpart to the reauthorization-required case
    above: a transient failure to even reach TikTok's token endpoint while
    refreshing must NOT be treated as reauthorization-required — the row
    stays PUBLISHING and another check is scheduled, exactly like any
    other transient status-check failure."""
    from content_automation.publishing.tiktok import auth as ta
    from content_automation.publishing.tiktok import publisher as tp
    from content_automation.config import STATUS_CHECK_BACKOFF_SECONDS

    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tok.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(ta, "TIKTOK_REFRESH_LOCK_PATH", tmp_path / "lock")
    ta.save_token({
        "access_token": "stale_access", "refresh_token": "refresh_xyz",
        "access_token_expires_at": (NOW - timedelta(minutes=10)).isoformat(),
        "refresh_token_expires_at": (NOW + timedelta(days=300)).isoformat(),
        "open_id": "u", "scope": "s",
    })

    import requests as real_requests

    def _raise(*a, **k):
        raise real_requests.ConnectionError("temporary DNS failure")

    monkeypatch.setattr(tp.requests, "post", _raise)

    row = _publishing_row(store)
    publisher = tp.TikTokPublisher()

    summary = recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    final = store.get_platform_post(row.video_id, "tiktok")
    assert len(summary.errors) == 1
    assert summary.failed == 0
    assert final.status == "PUBLISHING"
    assert final.status_check_count == 1
    assert final.next_status_check_at == (NOW + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[0])).isoformat()


# ---------------------------------------------------------------------------
# 20. two concurrent reconciliation passes cannot corrupt the same row
# ---------------------------------------------------------------------------

def test_two_concurrent_reconciliation_passes_do_not_corrupt_row(tmp_path):
    db_path = tmp_path / "test.db"
    with ContentStore(db_path=db_path) as setup_store:
        row = _publishing_row(setup_store)
        video_id = row.video_id

    barrier = threading.Barrier(2)
    results = []
    results_lock = threading.Lock()

    def _run():
        with ContentStore(db_path=db_path) as thread_store:
            publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))
            barrier.wait()
            summary = recon.reconcile_pending_status_checks_once(thread_store, publisher, now=NOW)
            with results_lock:
                results.append(summary)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_published = sum(s.published for s in results)
    assert total_published == 1  # exactly one thread's write actually applied

    with ContentStore(db_path=db_path) as check_store:
        final = check_store.get_platform_post(video_id, "tiktok")
    assert final.status == "PUBLISHED"


# ---------------------------------------------------------------------------
# 21. crash recovery and reconciliation share one safe write primitive and
# cannot fight over the same row.
# ---------------------------------------------------------------------------

def test_crash_recovery_and_reconciliation_do_not_fight_over_same_row(store):
    row = _publishing_row(store, updated_at=(NOW - timedelta(hours=1)).isoformat())

    # Reconciliation "observes" the row (captures its updated_at) before
    # crash recovery actually resolves it first.
    observed = store.get_platform_post(row.video_id, "tiktok")

    cr_publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))
    cr_summary = cr.recover_stale_posts_once(store, cr_publisher, platform="tiktok", now=NOW)
    assert cr_summary.published == 1

    # Reconciliation now attempts to apply its own write from the stale
    # view it captured earlier — must lose the race, not overwrite.
    applied = store.update_platform_post_if_unchanged(
        observed.id, expected_updated_at=observed.updated_at, updated_at=NOW.isoformat(),
        next_status_check_at=(NOW + timedelta(seconds=30)).isoformat(), status_check_count=1,
    )

    assert applied is False
    final = store.get_platform_post(row.video_id, "tiktok")
    assert final.status == "PUBLISHED"  # crash recovery's terminal result stands, untouched


# ---------------------------------------------------------------------------
# 22. once PUBLISHED, a row is never selected again
# ---------------------------------------------------------------------------

def test_once_published_row_is_never_selected_again(store):
    row = _publishing_row(store)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))
    recon.reconcile_pending_status_checks_once(store, publisher, now=NOW)

    summary = recon.reconcile_pending_status_checks_once(
        store, FakePublisher(), now=NOW + timedelta(days=30)
    )

    assert summary.discovered == 0
