"""
cli/run_worker.py — Thin entry point for the hosted scheduler worker
(Milestone 3.12). A separate long-running process from the API
(cli/run_api.py); on Railway it runs as its own service
(railway.worker.json).

All logic lives in content_automation.scheduling.hosted_worker — this file
only parses arguments, configures logging, checks prerequisites, wires
signals, and builds the configured store/storage.

Run:
  python3 cli/run_worker.py                 # loop until SIGTERM/SIGINT
  python3 cli/run_worker.py --dry-run       # log due work, claim/publish nothing
  python3 cli/run_worker.py --once          # one cycle, then exit

Uses only existing configuration (DATABASE_URL, STORAGE_BACKEND, SUPABASE_URL,
SERVICE_ROLE_KEY, TIKTOK_CLIENT_KEY/SECRET, CREDENTIAL_ENCRYPTION_KEY,
CONTENT_CALENDAR_TIMEZONE), plus the optional
CONTENT_CALENDAR_WORKER_POLL_INTERVAL_SECONDS (default 60).
"""

import argparse
import logging
import sys
import threading

from content_automation.config import WORKER_POLL_INTERVAL_SECONDS
from content_automation.persistence.store_factory import build_content_store
from content_automation.scheduling.hosted_worker import (
    install_stop_signal_handlers,
    run_worker_loop,
    worker_prerequisite_problems,
)
from content_automation.scheduling.worker import log_event
from content_automation.storage.factory import build_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the hosted scheduler worker.")
    parser.add_argument("--dry-run", action="store_true", help="Log due work only; never claim or publish.")
    parser.add_argument("--once", action="store_true", help="Run a single cycle, then exit.")
    parser.add_argument("--poll-interval", type=float, default=WORKER_POLL_INTERVAL_SECONDS,
                        help=f"Seconds between cycles (default {WORKER_POLL_INTERVAL_SECONDS:g}).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    problems = worker_prerequisite_problems()
    for problem in problems:
        log_event("worker_prerequisite_missing", detail=f'"{problem}"')
    if problems and not args.dry_run:
        sys.exit(1)

    stop = threading.Event()
    install_stop_signal_handlers(stop)

    run_worker_loop(
        store_factory=build_content_store, storage=build_storage(),
        poll_interval_seconds=args.poll_interval, stop=stop, dry_run=args.dry_run, once=args.once,
    )


if __name__ == "__main__":
    main()
