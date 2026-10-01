"""
hosted_worker.py — the hosted scheduler loop (Milestone 3.12: Hosted
Scheduler + Worker Execution).

What it does:
  run_hosted_cycle() is one pass over every hosted user with publishing
  work, running the three existing, already-validated one-pass jobs for
  that user, in this order, with that user's own TikTok credential
  (publishing/tiktok/hosted_publisher.py):

    1. crash_recovery.recover_stale_posts_once   (2.1.5 — requeue a stale
       claim that never reached TikTok; poll, never resubmit, one that did)
    2. reconciliation.reconcile_pending_status_checks_once   (2.1.10 —
       status checks for accepted-but-unfinished posts)
    3. worker.run_due_posts_once with hosted_due_selection's timezone-exact
       due rows   (2.1.4 — atomic claim, then publish_tiktok's one publish
       path; retry/backoff 2.1.6 unchanged)

  No new state machine: every status transition is made by those
  functions exactly as they already do for the local CLI. Each is scoped
  to user_id, so one user's rows are never touched with another user's
  credential. A failure for one user is logged and never stops the others.

  run_worker_loop() repeats run_hosted_cycle() every poll_interval_seconds
  until `stop` is set (SIGTERM/SIGINT — see cli/run_worker.py). Shutdown
  stops taking new work between cycles; an in-flight publish is allowed
  to finish. If the process is killed mid-publish anyway, the existing
  crash-recovery semantics apply on the next start (see the Milestone 3.12
  evaluation record's crash table).

  Each cycle opens a fresh store and closes it afterwards, so a dropped
  Postgres connection costs one cycle, not the process. Opening a store
  applies pending migrations; that is concurrency-safe across web and
  worker processes (postgres_migrate's advisory lock).

  dry_run: discovery only — logs what would be claimed and claims/writes
  nothing (used to verify a deploy without publishing).

Dependencies:
  scheduling.{worker,crash_recovery,reconciliation,hosted_due_selection},
  publishing.tiktok.hosted_publisher, persistence.store_factory.
"""

import logging
import shutil
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from content_automation.config import CREDENTIAL_ENCRYPTION_KEY, DATABASE_URL, STORAGE_BACKEND
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.publisher import Publisher
from content_automation.publishing.tiktok.hosted_publisher import build_hosted_tiktok_publisher
from content_automation.scheduling.crash_recovery import recover_stale_posts_once
from content_automation.scheduling.hosted_due_selection import get_hosted_due_posts
from content_automation.scheduling.reconciliation import reconcile_pending_status_checks_once
from content_automation.scheduling.worker import log_event, run_due_posts_once
from content_automation.storage.protocol import StorageProtocol

logger = logging.getLogger(__name__)

PLATFORM = "tiktok"


@dataclass
class HostedCycleSummary:
    users: int = 0
    due: int = 0
    claimed: int = 0
    published: int = 0
    failed: int = 0
    retry_scheduled: int = 0
    recovered: int = 0
    reconciled: int = 0
    user_errors: list[int] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return any((self.claimed, self.recovered, self.reconciled, self.user_errors))


def run_hosted_cycle(
    store: ContentStoreProtocol,
    storage: StorageProtocol,
    *,
    now_utc: datetime | None = None,
    publisher_factory: Callable[[ContentStoreProtocol, int], Publisher] = build_hosted_tiktok_publisher,
    dry_run: bool = False,
) -> HostedCycleSummary:
    now_utc = now_utc or datetime.now(timezone.utc)
    summary = HostedCycleSummary()

    for user_id in store.list_hosted_user_ids_with_platform_work(PLATFORM):
        summary.users += 1
        try:
            due_posts = get_hosted_due_posts(store, PLATFORM, user_id, now_utc)
            summary.due += len(due_posts)
            if dry_run:
                for post in due_posts:
                    log_event("dry_run_would_claim", platform_post_row_id=post.id, video_id=post.video_id,
                              platform=post.platform, scheduled_at=post.scheduled_at, user_id=user_id)
                continue

            publisher = publisher_factory(store, user_id)

            recovery = recover_stale_posts_once(store, publisher, platform=PLATFORM, now=now_utc, user_id=user_id)
            summary.recovered += recovery.requeued + recovery.published + recovery.failed

            reconciliation = reconcile_pending_status_checks_once(
                store, publisher, platform=PLATFORM, now=now_utc, user_id=user_id,
            )
            summary.reconciled += reconciliation.published + reconciliation.failed

            # Recovery may have requeued a stale claim; re-select so it can
            # run this cycle rather than waiting for the next one.
            if recovery.requeued:
                due_posts = get_hosted_due_posts(store, PLATFORM, user_id, now_utc)

            run = run_due_posts_once(
                store, publisher, platform=PLATFORM, user_id=user_id, storage=storage, due_posts=due_posts,
            )
            summary.claimed += run.claimed
            summary.published += run.published
            summary.failed += run.failed
            summary.retry_scheduled += run.retry_scheduled
        except Exception as exc:  # noqa: BLE001 — isolate users; the loop must keep running
            summary.user_errors.append(user_id)
            # Type only: exception text can embed platform/storage response bodies.
            log_event("user_cycle_error", user_id=user_id, error_type=type(exc).__name__)
            logger.debug("user cycle error detail", exc_info=True)

    return summary


def run_worker_loop(
    *,
    store_factory: Callable[[], ContentStoreProtocol],
    storage: StorageProtocol,
    poll_interval_seconds: float,
    stop: threading.Event,
    dry_run: bool = False,
    once: bool = False,
    publisher_factory: Callable[[ContentStoreProtocol, int], Publisher] = build_hosted_tiktok_publisher,
) -> None:
    """once=True runs exactly one cycle and returns (cli/run_worker.py --once)."""
    log_event("worker_started", poll_interval_seconds=poll_interval_seconds, dry_run=dry_run, platform=PLATFORM)
    while not stop.is_set():
        try:
            with store_factory() as store:
                summary = run_hosted_cycle(store, storage, publisher_factory=publisher_factory, dry_run=dry_run)
            # Quiet when idle: one line per cycle only when something happened
            # (always in dry-run, where the summary is the whole point).
            if summary.did_work or dry_run:
                log_event(
                    "poll_cycle", users=summary.users, due=summary.due, claimed=summary.claimed,
                    published=summary.published, failed=summary.failed, retry_scheduled=summary.retry_scheduled,
                    recovered=summary.recovered, reconciled=summary.reconciled, user_errors=len(summary.user_errors),
                )
        except Exception as exc:  # noqa: BLE001 — e.g. database unreachable; retry next cycle
            log_event("poll_cycle_error", error_type=type(exc).__name__)
            logger.debug("poll cycle error detail", exc_info=True)
        if once:
            break
        stop.wait(poll_interval_seconds)
    log_event("worker_stopped")


def worker_prerequisite_problems() -> list[str]:
    """Configuration the hosted worker cannot publish without. Names only —
    never values. ffprobe is required because TikTokPublisher inspects the
    media (duration) before every submission, and hosted uploads are only
    inspected at publish time (publish_tiktok._validate_ready_to_publish)."""
    problems = []
    if not DATABASE_URL:
        problems.append("DATABASE_URL is not set (the hosted worker must use Postgres)")
    if STORAGE_BACKEND != "supabase":
        problems.append(f"STORAGE_BACKEND is {STORAGE_BACKEND!r}, expected 'supabase'")
    if not CREDENTIAL_ENCRYPTION_KEY:
        problems.append("CREDENTIAL_ENCRYPTION_KEY is not set (hosted TikTok credentials can't be decrypted)")
    if shutil.which("ffprobe") is None:
        problems.append("ffprobe is not on PATH (install ffmpeg in the worker image)")
    return problems


def install_stop_signal_handlers(stop: threading.Event) -> None:
    """SIGTERM (Railway redeploy/stop) and SIGINT set `stop`: the loop
    finishes the current cycle — including an in-flight publish — then
    exits instead of taking new work."""

    def handle(signum, _frame):
        log_event("worker_stopping", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
