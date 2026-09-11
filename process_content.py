"""
process_content.py — Ingest, transcribe, classify, and schedule incoming videos.

What it does:
  Discovers videos in content/incoming/, inspects them with ffprobe, extracts
  a small audio derivative and transcribes it locally, classifies the
  transcript against the pillars in config.CONTENT_TYPES via Claude, and
  routes sufficiently confident classifications to the earliest open
  content_slot for that pillar (written by generate_calendar.py). See
  docs/decisions/0001-video-ingestion-pipeline.md for the architecture.

Run command:
  python3 process_content.py
  python3 process_content.py --dry-run
  python3 process_content.py --verbose

Ordering rule:
  Videos are processed oldest-file-first, by filesystem mtime (tie-broken by
  filename), so a batch always assigns slots in a stable, deterministic order.

Idempotency:
  Videos are identified by sha256 content hash. Re-running this command does
  not retranscribe, reclassify, or reassign a video that already reached a
  terminal state (ASSIGNED or FAILED); a video interrupted mid-pipeline
  resumes from its last completed stage.

Dependencies:
  media.py, transcription.py, classification.py, content_store.py,
  slot_matcher.py, config.py
"""

import argparse
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import classification
import media
import slot_matcher
import transcription
from classification import ClaudeClassifier
from config import (
    AUTO_ASSIGN_THRESHOLD,
    CONTENT_TYPES,
    FAILED_DIR,
    INCOMING_DIR,
    PROCESSED_DIR,
    SUPPORTED_VIDEO_EXTENSIONS,
)
from content_store import ContentStore, SlotRecord, VideoRecord
from generate_calendar import get_content_label
from transcription import FasterWhisperTranscriber


@dataclass
class Outcome:
    path: Path
    video: VideoRecord
    kind: str  # ASSIGNED, WOULD_ASSIGN, WAITING_FOR_SLOT, NEEDS_REVIEW, FAILED, ALREADY_ASSIGNED, ALREADY_FAILED
    slot: SlotRecord | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_videos(incoming_dir: Path) -> list[Path]:
    """Oldest-file-first (filesystem mtime, tie-broken by filename)."""
    if not incoming_dir.exists():
        return []
    files = [
        p for p in incoming_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    ]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


# ---------------------------------------------------------------------------
# Per-video pipeline
# ---------------------------------------------------------------------------

def process_one(
    store: ContentStore,
    transcriber: transcription.Transcriber,
    classifier: classification.ContentClassifier,
    path: Path,
    *,
    dry_run: bool,
) -> Outcome:
    now_iso = datetime.now(timezone.utc).isoformat()
    file_hash = media.file_hash(path)

    video = store.get_video_by_hash(file_hash)
    if video is None:
        video = store.insert_video(file_hash, path.name, str(path), now_iso)

    if video.status == "ASSIGNED":
        return Outcome(path, video, "ALREADY_ASSIGNED")
    if video.status == "FAILED":
        return Outcome(path, video, "ALREADY_FAILED")
    if video.status == "NEEDS_REVIEW":
        return Outcome(path, video, "NEEDS_REVIEW")

    # --- Inspect (skip if already validated) ---
    if video.container is None:
        try:
            info = media.inspect_media(path)
        except media.MediaError as exc:
            store.update_video(video.id, status="FAILED", failure_reason=exc.reason_code, processed_at=now_iso)
            _move_file(path, FAILED_DIR)
            return Outcome(path, store.get_video_by_hash(file_hash), "FAILED")

        store.update_video(
            video.id,
            canonical_media_path=str(info.path),
            container=info.container,
            video_codec=info.video_codec,
            audio_codec=info.audio_codec,
            width=info.width,
            height=info.height,
            fps=info.fps,
            duration_seconds=info.duration_seconds,
            file_size_bytes=info.file_size_bytes,
            status="VALIDATED",
        )
        video = store.get_video_by_hash(file_hash)
    else:
        info = media.MediaInfo(
            path=path,
            container=video.container,
            video_codec=video.video_codec,
            audio_codec=video.audio_codec,
            width=video.width,
            height=video.height,
            fps=video.fps,
            duration_seconds=video.duration_seconds,
            file_size_bytes=video.file_size_bytes,
        )

    # --- Transcribe (skip if already transcribed) ---
    if video.transcript is None:
        audio_path = None
        try:
            audio_path = media.extract_audio(info)
            result = transcriber.transcribe(audio_path)
        except (media.MediaError, transcription.TranscriptionError) as exc:
            reason = getattr(exc, "reason_code", "TRANSCRIPTION_FAILED")
            store.update_video(video.id, status="FAILED", failure_reason=reason, processed_at=now_iso)
            _move_file(path, FAILED_DIR)
            return Outcome(path, store.get_video_by_hash(file_hash), "FAILED")
        finally:
            if audio_path is not None:
                media.cleanup_audio(audio_path)

        store.update_video(
            video.id,
            transcript=result.text,
            transcript_language=result.language,
            transcription_status="COMPLETE",
            status="TRANSCRIBED",
        )
        video = store.get_video_by_hash(file_hash)

    # --- Classify (skip if already classified) ---
    if video.classified_pillar is None and video.status != "NEEDS_REVIEW":
        try:
            result = classifier.classify(video.transcript, CONTENT_TYPES)
        except classification.ClassificationError:
            store.update_video(video.id, status="FAILED", failure_reason="CLASSIFICATION_FAILED", processed_at=now_iso)
            _move_file(path, FAILED_DIR)
            return Outcome(path, store.get_video_by_hash(file_hash), "FAILED")

        eligible = result.pillar is not None and result.confidence >= AUTO_ASSIGN_THRESHOLD
        store.update_video(
            video.id,
            classified_pillar=result.pillar,
            classification_confidence=result.confidence,
            classification_reason=result.reason,
            status="CLASSIFIED" if eligible else "NEEDS_REVIEW",
        )
        video = store.get_video_by_hash(file_hash)

    if video.status == "NEEDS_REVIEW":
        return Outcome(path, video, "NEEDS_REVIEW")

    # --- Deterministic slot match (video.status == "CLASSIFIED" here) ---
    slot = slot_matcher.select_slot(store, video.classified_pillar)
    if slot is None:
        return Outcome(path, video, "WAITING_FOR_SLOT")

    if dry_run:
        return Outcome(path, video, "WOULD_ASSIGN", slot=slot)

    store.assign_slot(video.id, slot.id)
    store.update_video(video.id, status="ASSIGNED", processed_at=now_iso)
    _move_file(path, PROCESSED_DIR)
    return Outcome(path, store.get_video_by_hash(file_hash), "ASSIGNED", slot=slot)


def _move_file(path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}.{uuid.uuid4().hex[:8]}{path.suffix}"
    shutil.move(str(path), str(dest))


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def _print_progress(outcome: Outcome, verbose: bool) -> None:
    labels = {
        "ASSIGNED": "ASSIGNED",
        "WOULD_ASSIGN": "WOULD ASSIGN (dry run)",
        "WAITING_FOR_SLOT": "WAITING FOR SLOT",
        "NEEDS_REVIEW": "NEEDS REVIEW",
        "FAILED": "FAILED",
        "ALREADY_ASSIGNED": "already assigned (skipped)",
        "ALREADY_FAILED": "already failed (skipped)",
    }
    print(f"  {outcome.path.name}: {labels[outcome.kind]}")
    if verbose:
        v = outcome.video
        if v.container:
            print(f"      container={v.container} video={v.video_codec} audio={v.audio_codec} "
                  f"{v.width}x{v.height}@{v.fps} {v.duration_seconds:.1f}s")
        if v.transcript is not None:
            snippet = v.transcript[:160] + ("…" if len(v.transcript) > 160 else "")
            print(f"      transcript ({v.transcript_language}): {snippet!r}")
        if v.classified_pillar is not None or v.classification_confidence is not None:
            print(f"      classification: pillar={v.classified_pillar} "
                  f"confidence={v.classification_confidence} reason={v.classification_reason!r}")
        if outcome.slot is not None:
            print(f"      slot: {outcome.slot.scheduled_at}")
        if v.failure_reason:
            print(f"      failure_reason={v.failure_reason}")


def _print_summary(outcomes: list[Outcome], dry_run: bool) -> None:
    counts = {kind: 0 for kind in (
        "ASSIGNED", "WOULD_ASSIGN", "WAITING_FOR_SLOT", "NEEDS_REVIEW",
        "FAILED", "ALREADY_ASSIGNED", "ALREADY_FAILED",
    )}
    for outcome in outcomes:
        counts[outcome.kind] += 1

    assigned_label = "Would assign" if dry_run else "Assigned"
    assigned_count = counts["WOULD_ASSIGN"] if dry_run else counts["ASSIGNED"]
    assigned_kind = "WOULD_ASSIGN" if dry_run else "ASSIGNED"

    print("\nContent Processing Complete\n")
    print(f"Processed: {len(outcomes)}")
    print(f"{assigned_label}: {assigned_count}")
    print(f"Needs review: {counts['NEEDS_REVIEW']}")
    print(f"Waiting for slot: {counts['WAITING_FOR_SLOT']}")
    print(f"Failed: {counts['FAILED']}")
    skipped = counts["ALREADY_ASSIGNED"] + counts["ALREADY_FAILED"]
    if skipped:
        print(f"Skipped (already processed in a prior run): {skipped}")

    assignments = [o for o in outcomes if o.kind == assigned_kind]
    if assignments:
        print(f"\n{assigned_label}s\n")
        for o in assignments:
            label = get_content_label(o.video.classified_pillar)
            when = datetime.fromisoformat(o.slot.scheduled_at).strftime("%b %d, %I:%M %p").replace(" 0", " ")
            pct = round(o.video.classification_confidence * 100)
            print(f"{o.path.name}\n{label}\n{when}\nConfidence: {pct}%\n")

    if dry_run:
        print("Dry run: no content_slots were mutated and no files were moved.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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

    videos = discover_videos(INCOMING_DIR)
    if not videos:
        print(f"No videos found in {INCOMING_DIR}")
        return

    print(f"Discovered {len(videos)} video(s) in {INCOMING_DIR}")
    if args.dry_run:
        print("Dry run: transcription/classification will run and cache, but no slots will be claimed.\n")
    else:
        print()

    transcriber = FasterWhisperTranscriber()
    classifier = ClaudeClassifier()
    outcomes: list[Outcome] = []

    with ContentStore() as store:
        for path in videos:
            outcome = process_one(store, transcriber, classifier, path, dry_run=args.dry_run)
            outcomes.append(outcome)
            _print_progress(outcome, verbose=args.verbose)

    _print_summary(outcomes, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
