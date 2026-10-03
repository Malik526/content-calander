"""Pins the hosted worker's CURRENT overdue-post behavior (Milestone 3.14
follow-up evaluation — see docs/evaluations/productization/
milestone-3.14-hosted-e2e-validation.md "Overdue publishing policy").

Current rule (unchanged since Milestone 2.1.7, applied per slot timezone
since 3.12): a PENDING post is due once scheduled_at <= now in its slot's
timezone, with NO upper bound on lateness — so downtime is caught up when
the worker returns. These tests describe that behavior as it is; if an
overdue policy (e.g. a grace window) is adopted, change them deliberately.

SQLite + a fake publisher; no TikTok call. The rate-limit test reproduces
TikTok's documented 6-init-requests-per-minute-per-token limit (HTTP 429,
rate_limit_exceeded) to show what a large catch-up burst does today."""

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from content_automation.media.caption_editing import save_caption
from content_automation.media.media_storage import create_video_from_upload
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult
from content_automation.scheduling.hosted_due_selection import get_hosted_due_posts, lateness_seconds
from content_automation.scheduling.hosted_worker import run_hosted_cycle
from content_automation.scheduling.queue_assignment import assign_video_to_slot
from content_automation.storage.local import LocalStorage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg/ffprobe not on PATH")

NY = "America/New_York"
# Fixed instant: 2026-10-05 13:00 UTC == 09:00 New York == 06:00 Los Angeles.
NOW = datetime(2026, 10, 5, 13, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _ny_wall(delta: timedelta) -> str:
    """Naive New York wall-clock time NOW + delta (scheduled_at's convention)."""
    return (NOW.astimezone(ZoneInfo(NY)) + delta).replace(tzinfo=None).isoformat()


@pytest.fixture(scope="module")
def video_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24",
                    "-c:v", "libx264", "-loglevel", "error", str(path)], check=True)
    return path


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


@pytest.fixture
def user(store):
    u = store.create_user("creator@example.com", None, _iso(NOW))
    store.create_auth_identity(u.id, "supabase", "sub-1", "creator@example.com", _iso(NOW))
    return u


def _post(store, storage, user, video_file, tmp_path, scheduled_at, tz=NY, name="v"):
    upload = tmp_path / f"{name}.mp4"
    shutil.copy(video_file, upload)
    video = create_video_from_upload(store, storage, user.id, local_path=upload, original_filename=f"{name}.mp4",
                                     file_hash=f"h-{name}", file_size_bytes=upload.stat().st_size, created_at=_iso(NOW))
    store.insert_slot_if_missing(scheduled_at, None, None, _iso(NOW), user_id=user.id, timezone=tz)
    slot = next(s for s in store.list_content_slots_for_user(user.id, "2000-01-01T00:00:00", "2200-01-01T00:00:00")
                if s.scheduled_at == scheduled_at)
    assign_video_to_slot(store, video.id, slot.id, _iso(NOW), user_id=user.id)
    save_caption(store, store.get_video(video.id), f"caption {name}")
    return store.get_platform_post(video.id, "tiktok")


def _due_ids(store, user, now=NOW):
    return [p.id for p in get_hosted_due_posts(store, "tiktok", user.id, now)]


# --- selection: exact current semantics --------------------------------------------

@pytest.mark.parametrize("label,delta,due", [
    ("one minute in the future", timedelta(minutes=1), False),
    ("exactly due", timedelta(0), True),
    ("4 minutes late", timedelta(minutes=-4), True),
    ("6 hours late", timedelta(hours=-6), True),
    ("3 days late", timedelta(days=-3), True),
])
def test_due_has_no_upper_bound_on_lateness(store, storage, user, video_file, tmp_path, label, delta, due):
    post = _post(store, storage, user, video_file, tmp_path, _ny_wall(delta))
    assert (post.id in _due_ids(store, user)) is due, label


def test_lateness_is_measured_in_the_slots_own_timezone(store, storage, user, video_file, tmp_path):
    # 07:00 is 3h overdue in New York but 1h in the future in Los Angeles (06:00 now).
    post = _post(store, storage, user, video_file, tmp_path, "2026-10-05T07:00:00", tz="America/Los_Angeles")
    assert _due_ids(store, user) == []
    assert _due_ids(store, user, NOW + timedelta(hours=1)) == [post.id]


@pytest.mark.parametrize("status", ["PUBLISHING", "PUBLISHED", "FAILED", "UNKNOWN"])
def test_only_pending_posts_are_caught_up(store, storage, user, video_file, tmp_path, status):
    post = _post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(days=-1)))
    store.update_platform_post(post.id, updated_at=_iso(NOW), status=status)
    assert _due_ids(store, user) == []


def test_overdue_posts_are_selected_oldest_first(store, storage, user, video_file, tmp_path):
    late = [_post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(hours=-h)), name=f"p{h}")
            for h in (1, 30, 6)]
    assert _due_ids(store, user) == [late[1].id, late[2].id, late[0].id]  # 30h, 6h, 1h overdue


# --- burst: what one recovery cycle does today ------------------------------------

class RateLimitedPublisher:
    """Accepts `allowed` submissions, then answers like TikTok's init
    endpoint does past 6 requests/minute/token (HTTP 429)."""

    reports_platform_post_id_before_media_transfer = True

    def __init__(self, allowed: int):
        self.allowed, self.calls = allowed, []

    def publish(self, video_path, caption, on_platform_post_id=None):
        self.calls.append(caption)
        if len(self.calls) > self.allowed:
            raise PublishError("TikTok API error rate_limit_exceeded", reason_code="rate_limit_exceeded", http_status=429)
        publish_id = f"pub_{len(self.calls)}"
        on_platform_post_id(publish_id)
        return PublishResult(platform_post_id=publish_id, status="PROCESSING_UPLOAD")

    def get_status(self, platform_post_id):
        return PublishStatusResult(status="PUBLISH_COMPLETE")


def test_whole_backlog_publishes_back_to_back_in_one_cycle(store, storage, user, video_file, tmp_path):
    posts = [_post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(hours=-6 * (i + 1))), name=f"b{i}")
             for i in range(5)]
    publisher = RateLimitedPublisher(allowed=100)

    summary = run_hosted_cycle(store, storage, now_utc=NOW, publisher_factory=lambda *_: publisher)

    assert summary.published == 5
    assert publisher.calls == [f"caption b{i}" for i in reversed(range(5))]  # oldest first, no spacing
    assert all(store.get_platform_post(p.video_id, "tiktok").status == "PUBLISHED" for p in posts)


def test_backlog_beyond_tiktoks_rate_limit_ends_failed_not_retried(store, storage, user, video_file, tmp_path):
    """Current classification treats rate_limit_exceeded (HTTP 429) as
    terminal, so a burst larger than TikTok's per-minute init limit fails
    the remainder instead of spreading them out. Evaluation finding."""
    posts = [_post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(minutes=-(i + 1))), name=f"r{i}")
             for i in range(8)]
    publisher = RateLimitedPublisher(allowed=6)

    summary = run_hosted_cycle(store, storage, now_utc=NOW, publisher_factory=lambda *_: publisher)

    statuses = [store.get_platform_post(p.video_id, "tiktok") for p in posts]
    assert (summary.published, summary.failed, summary.retry_scheduled) == (6, 2, 0)
    assert sorted(r.failure_code for r in statuses if r.status == "FAILED") == ["rate_limit_exceeded"] * 2


# --- overdue telemetry (Milestone 3.14 follow-up) ---------------------------------

def test_lateness_is_measured_in_the_slots_timezone(store, storage, user, video_file, tmp_path):
    ny = _post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(hours=-6)), name="ny")
    la = _post(store, storage, user, video_file, tmp_path, "2026-10-05T05:00:00", tz="America/Los_Angeles", name="la")
    exact = _post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(0)), name="exact")

    assert lateness_seconds(store, ny, NOW) == 6 * 3600
    assert lateness_seconds(store, la, NOW) == 3600  # 06:00 now in Los Angeles
    assert lateness_seconds(store, exact, NOW) == 0


def test_lateness_across_a_dst_change_is_real_elapsed_time(store, storage, user, video_file, tmp_path):
    # 2026-11-01 00:30 EDT (04:30Z); clocks fall back at 02:00. At 12:00Z the
    # wall-clock difference is 6.5h, but 7.5h have actually elapsed.
    post = _post(store, storage, user, video_file, tmp_path, "2026-11-01T00:30:00")
    assert lateness_seconds(store, post, datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)) == 27000


def _real_ny_wall(delta: timedelta) -> str:
    moment = datetime.now(timezone.utc).astimezone(ZoneInfo(NY)) + delta
    return moment.replace(second=0, microsecond=0, tzinfo=None).isoformat()


def _events(caplog, name):
    return [dict(part.split("=", 1) for part in r.getMessage().split() if "=" in part)
            for r in caplog.records if r.getMessage().startswith(f"event={name} ")]


def test_cycle_logs_backlog_and_lateness_at_claim_and_outcome(store, storage, user, video_file, tmp_path, caplog):
    """Real clock: claims are stamped with real time, so schedule relative to it."""
    late = _post(store, storage, user, video_file, tmp_path, _real_ny_wall(timedelta(hours=-2)), name="late")
    on_time = _post(store, storage, user, video_file, tmp_path, _real_ny_wall(timedelta(0)), name="on_time")
    store.update_platform_post(late.id, updated_at=_iso(NOW), failure_code="NETWORK_ERROR", retry_count=1)
    publisher = RateLimitedPublisher(allowed=100)

    with caplog.at_level("INFO"):
        run_hosted_cycle(store, storage, publisher_factory=lambda *_: publisher)

    [backlog] = _events(caplog, "due_backlog")
    assert (backlog["user_id"], backlog["due"], backlog["overdue"]) == (str(user.id), "2", "1")
    assert 7200 <= int(backlog["max_lateness_seconds"]) < 7200 + 180

    claims = {int(e["platform_post_row_id"]): e for e in _events(caplog, "post_claimed")}
    assert 7200 <= int(claims[late.id]["lateness_seconds"]) < 7200 + 180
    assert 0 <= int(claims[on_time.id]["lateness_seconds"]) < 180
    assert claims[late.id]["previous_failure_code"] == "NETWORK_ERROR"
    assert claims[on_time.id]["previous_failure_code"] == "None"
    assert all("claimed_at" in e for e in claims.values())

    outcomes = {int(e["platform_post_row_id"]): e for e in _events(caplog, "publish_succeeded")}
    assert set(outcomes) == {late.id, on_time.id}
    assert all(int(e["lateness_seconds"]) >= int(claims[i]["lateness_seconds"]) for i, e in outcomes.items())


def test_no_backlog_event_when_nothing_is_due(store, storage, user, video_file, tmp_path, caplog):
    _post(store, storage, user, video_file, tmp_path, _real_ny_wall(timedelta(hours=1)))
    with caplog.at_level("INFO"):
        run_hosted_cycle(store, storage, publisher_factory=lambda *_: RateLimitedPublisher(allowed=1))
    assert _events(caplog, "due_backlog") == []


def test_dry_run_reports_lateness_without_claiming(store, storage, user, video_file, tmp_path, caplog):
    post = _post(store, storage, user, video_file, tmp_path, _ny_wall(timedelta(hours=-3)))
    with caplog.at_level("INFO"):
        run_hosted_cycle(store, storage, now_utc=NOW, dry_run=True, publisher_factory=lambda *_: pytest.fail("no"))
    [event] = _events(caplog, "dry_run_would_claim")
    assert event["lateness_seconds"] == str(3 * 3600)
    assert store.get_platform_post(post.video_id, "tiktok").status == "PENDING"
