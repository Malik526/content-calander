"""
cli/process_content.py — Thin CLI entry point for the content ingestion
pipeline (Milestone 3.0's package refactor thinned this from the original
standalone process_content.py).

All actual logic (discover_videos, process_one, and the progress/summary
printers) lives in content_automation.media.processing — this file only
parses arguments, validates config, constructs the transcriber/classifier,
and drives the per-video loop.

Run:
  python3 cli/process_content.py
  python3 cli/process_content.py --dry-run
  python3 cli/process_content.py --verbose
"""

import argparse
import sys

from content_automation.calendar import cadence
from content_automation.config import CAPTION_MODE, INCOMING_DIR, ROUTING_MODE
from content_automation.media import caption, classification
from content_automation.media import inspection as media
from content_automation.media.processing import (
    Outcome,
    _print_progress,
    _print_summary,
    discover_videos,
    process_one,
)
from content_automation.media.transcription import FasterWhisperTranscriber
from content_automation.persistence.content_store import ContentStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest, transcribe, classify, and schedule incoming videos."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Transcribe and classify normally (results are cached), but never "
            "claim a content_slot, mark a video ASSIGNED, or move files."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print media inspection, transcript, and classification detail per video.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        media.check_ffmpeg_available()
    except media.FfmpegNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        cadence.validate_routing_mode(ROUTING_MODE)
        caption.validate_caption_mode(CAPTION_MODE)
    except (cadence.ScheduleConfigError, caption.CaptionConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Only construct a classifier in pillar mode — FIFO mode never invokes
    # one, so an invalid/unset CONTENT_CALENDAR_CLASSIFIER can't block it.
    classifier: classification.ContentClassifier | None = None
    if ROUTING_MODE == "pillar":
        try:
            classifier = classification.build_classifier()
        except classification.ClassificationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    transcriber = FasterWhisperTranscriber()
    outcomes: list[Outcome] = []

    with ContentStore() as store:
        # Milestone 3.2 (ownership): see cli/worker.py's matching comment —
        # every video this run creates is stamped with the local user's id.
        user = store.get_or_create_local_user()

        videos = discover_videos(store, INCOMING_DIR)
        if not videos:
            print(f"No videos found in {INCOMING_DIR}")
            return

        print(f"Discovered {len(videos)} video(s) in {INCOMING_DIR} ({ROUTING_MODE} mode)")
        if args.dry_run:
            print("Dry run: transcription/classification will run and cache, but no slots will be claimed.\n")
        else:
            print()

        for path in videos:
            outcome = process_one(
                store, transcriber, classifier, path, dry_run=args.dry_run, routing_mode=ROUTING_MODE,
                user_id=user.id,
            )
            outcomes.append(outcome)
            _print_progress(outcome, verbose=args.verbose)

    _print_summary(outcomes, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
