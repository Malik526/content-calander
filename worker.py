"""
worker.py — One-pass worker execution: discover due platform_posts,
atomically claim each one, and execute the existing TikTok publish flow
(Milestone 2.1.4).

What it does:
  Connects the pieces already proven in isolation:

    due_post_selector.get_due_posts()          (2.1.1, corrected in 2.1.2)
      -> content_store.ContentStore.claim_platform_post()  (2.1.3)
      -> publish_tiktok.execute_claimed_platform_post()    (2.0)

  For every due PENDING platform_posts row, attempt to claim it. A
  successful claim executes the exact same proven publish path the manual
  CLI uses (execute_claimed_platform_post) — no duplicated TikTok
  publishing logic. A failed claim (another process won first, e.g. a
  concurrently-running worker or a manual publish_tiktok.py invocation) is
  skipped, not retried or treated as an error — that is the correct,
  expected outcome of losing a race, not a failure.

  One pass only: discovers whatever is due right now, attempts each once,
  and returns. No loop, no cron, no daemon, no retry/backoff, no
  stale-PUBLISHING recovery — see
  docs/evaluations/scheduling/milestone-2.1.4-worker-execution.md for why
  that is this milestone's deliberate boundary. If the process crashes
  after a successful claim but before completion, that row is left
  PUBLISHING; recovering it is explicitly deferred to a future
  crash-recovery milestone.

Run:
  python3 worker.py
  python3 worker.py --platform tiktok

Dependencies:
  content_store.py, due_post_selector.py, publish_tiktok.py, publisher.py,
  tiktok_publisher.py, config.py.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

import due_post_selector
from content_store import ContentStore
from publish_tiktok import PublishTikTokError, execute_claimed_platform_post
from publisher import Publisher


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerRunSummary:
    """One one-pass run's outcome. published/failed reflect the row's
    status immediately after execution — a row that ends this run still
    PUBLISHING (TikTok reported an in-progress, non-terminal status) is
    counted in neither; claimed - published - failed - len(errors) is
    exactly that count."""
    discovered: int = 0
    claimed: int = 0
    skipped: int = 0
    published: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def run_due_posts_once(
    store: ContentStore, publisher: Publisher, *, platform: str = "tiktok", now: datetime | None = None
) -> WorkerRunSummary:
    """Discover due PENDING platform_posts rows for `platform`, attempt to
    atomically claim each one, and execute the proven publish flow for
    every successful claim. One pass — never loops, never waits, never
    retries a failed claim. `now` is forwarded to
    due_post_selector.get_due_posts() for deterministic testing (see that
    module's docstring for the naive-local-time convention this repository
    uses for scheduling).
    """
    summary = WorkerRunSummary()

    due_posts = due_post_selector.get_due_posts(store, platform, now=now)
    summary.discovered = len(due_posts)

    for post in due_posts:
        claimed = store.claim_platform_post(post.id, updated_at=_now_iso())
        if not claimed:
            summary.skipped += 1
            continue
        summary.claimed += 1

        try:
            execute_claimed_platform_post(store, post.video_id, post.platform, publisher)
        except PublishTikTokError as exc:
            summary.errors.append(str(exc))

        final = store.get_platform_post(post.video_id, post.platform)
        if final is not None and final.status == "PUBLISHED":
            summary.published += 1
        elif final is not None and final.status == "FAILED":
            summary.failed += 1

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one pass: discover due platform_posts, atomically claim each, and execute the "
        "existing publish flow. Not a loop/daemon — exits after this one pass."
    )
    parser.add_argument("--platform", default="tiktok", help="Platform to process (default: tiktok).")
    return parser.parse_args()


def main() -> None:
    from tiktok_publisher import TikTokPublisher

    args = parse_args()
    publisher = TikTokPublisher()

    with ContentStore() as store:
        summary = run_due_posts_once(store, publisher, platform=args.platform)

    print(
        f"discovered={summary.discovered} claimed={summary.claimed} skipped={summary.skipped} "
        f"published={summary.published} failed={summary.failed}"
    )
    for err in summary.errors:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
