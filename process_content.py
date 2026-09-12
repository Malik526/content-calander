"""
process_content.py — Ingest, transcribe, (optionally classify), and schedule
incoming videos.

What it does:
  Discovers videos in content/incoming/, inspects them with ffprobe, extracts
  a small audio derivative and transcribes it locally, derives a caption
  candidate, and routes each video to an open content_slot. Routing mode
  (config.ROUTING_MODE) governs matching:

    - "fifo" (default): no classifier is constructed or called at all. Every
      valid, inspected video is eligible for the earliest OPEN future slot
      regardless of pillar — see slot_matcher.select_slot_fifo. Transcription
      is enrichment, not a scheduling gate: a transcription failure records
      transcription_status=FAILED / status=TRANSCRIPTION_FAILED and the
      video still proceeds to slot matching in the same run (see
      docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md for
      why transcription is decoupled here but media inspection is not).
    - "pillar": preserves the original classify-then-match strategy —
      transcript classified against config.CONTENT_TYPES (local embeddings
      by default; Claude optionally via config.CLASSIFIER — see
      classification.py), routed to the earliest open content_slot for that
      pillar. This module depends only on classification.ContentClassifier —
      the active classifier already applies its own auto-assign policy
      before returning (pillar=None means "abstain"), so this file never
      re-applies a classifier-specific confidence threshold itself.
      Transcription failure is still a hard blocker here (classification
      needs the transcript).

  See docs/decisions/0001-video-ingestion-pipeline.md,
  docs/decisions/0003-local-embedding-classification.md, and
  docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md.

Run command:
  python3 process_content.py
  python3 process_content.py --dry-run
  python3 process_content.py --verbose

Ordering rule:
  Videos are processed oldest-first, so a batch always assigns slots in a
  stable, deterministic order. A path already known to the store (i.e.
  still waiting in incoming/ from a prior run) sorts by its immutable
  videos.created_at; a genuinely new path sorts by filesystem mtime
  (tie-broken by filename). This keeps a still-waiting video's FIFO
  position stable across a touch/copy between runs — it does not protect
  against a rename, which is treated as a new video (documented limitation,
  see ADR-0005).

Idempotency:
  Videos are identified by sha256 content hash. Re-running this command does
  not retranscribe, reclassify, or reassign a video that already reached a
  terminal state (ASSIGNED or FAILED); a video interrupted mid-pipeline
  resumes from its last completed stage.

Dependencies:
  media.py, transcription.py, classification.py, caption.py,
  content_store.py, slot_matcher.py, config.py
"""

import argparse
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import caption
import classification
import media
import scheduling
import slot_matcher
import transcription
from config import (
    CAPTION_MODE,
    CONTENT_TYPES,
    FAILED_DIR,
    INCOMING_DIR,
    PROCESSED_DIR,
    ROUTING_MODE,
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

def discover_videos(store: ContentStore, incoming_dir: Path) -> list[Path]:
    """Deterministic FIFO discovery order.

    A path already known to the store (still sitting in incoming/ from a
    prior run — e.g. WAITING_FOR_SLOT) sorts by its immutable
    videos.created_at, so its position can't unexpectedly change because the
    file was later touched or copied. A path not yet known (genuinely new
    this run) sorts after all known videos, by filesystem mtime (tie-broken
    by filename) — the best available signal for relative order among files
    the system has never seen. This does not protect a still-waiting video
    against being renamed between runs (a renamed file has no original_path
    match and is treated as newly discovered) — see
    docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md.
    """
    if not incoming_dir.exists():
        return []
    files = [
        p for p in incoming_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    ]

    def sort_key(path: Path):
        known = store.get_video_by_path(str(path))
        if known is not None:
            return (0, known.created_at, path.name)
        return (1, path.stat().st_mtime, path.name)

    return sorted(files, key=sort_key)


# ---------------------------------------------------------------------------
# Per-video pipeline
# ---------------------------------------------------------------------------

def process_one(
    store: ContentStore,
    transcriber: transcription.Transcriber,
    classifier: classification.ContentClassifier | None,
    path: Path,
    *,
    dry_run: bool,
    routing_mode: str = ROUTING_MODE,
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

    # --- Transcribe (skip if already transcribed, or already permanently
    #     failed under FIFO's non-blocking policy — see below) ---
    if video.transcript is None and video.status != "TRANSCRIPTION_FAILED":
        audio_path = None
        try:
            audio_path = media.extract_audio(info)
            result = transcriber.transcribe(audio_path)
        except (media.MediaError, transcription.TranscriptionError) as exc:
            reason = getattr(exc, "reason_code", "TRANSCRIPTION_FAILED")
            if routing_mode == "fifo":
                # Transcription is enrichment in FIFO mode, not a scheduling
                # gate: record the failure and fall through to caption/slot
                # matching in this same call rather than aborting. See
                # docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md
                # for why this stays before slot assignment rather than after.
                store.update_video(
                    video.id, transcription_status="FAILED", failure_reason=reason, status="TRANSCRIPTION_FAILED"
                )
                video = store.get_video_by_hash(file_hash)
            else:
                store.update_video(video.id, status="FAILED", failure_reason=reason, processed_at=now_iso)
                _move_file(path, FAILED_DIR)
                return Outcome(path, store.get_video_by_hash(file_hash), "FAILED")
        else:
            store.update_video(
                video.id,
                transcript=result.text,
                transcript_language=result.language,
                transcription_status="COMPLETE",
                status="TRANSCRIBED",
            )
            video = store.get_video_by_hash(file_hash)
        finally:
            if audio_path is not None:
                media.cleanup_audio(audio_path)

    # --- Caption (skip if already prepared; both modes) ---
    if video.caption_source is None:
        if CAPTION_MODE == "transcript_auto":
            if video.transcript is not None:
                text = caption.build_caption_from_transcript(video.transcript)
                store.update_video(video.id, caption_text=text, caption_source="transcript_auto")
                video = store.get_video_by_hash(file_hash)
            elif video.status == "TRANSCRIPTION_FAILED":
                # Documented fallback: transcript permanently unavailable
                # this run, so there is nothing to derive a caption from.
                store.update_video(video.id, caption_source="none")
                video = store.get_video_by_hash(file_hash)
        elif CAPTION_MODE == "manual":
            # caption_text is left alone (NULL until a future editing UI
            # sets it) — recording caption_source here only makes this
            # stage idempotent, it never overwrites a manual caption.
            store.update_video(video.id, caption_source="manual")
            video = store.get_video_by_hash(file_hash)
        elif CAPTION_MODE == "none":
            store.update_video(video.id, caption_source="none")
            video = store.get_video_by_hash(file_hash)

    if routing_mode == "fifo":
        slot = slot_matcher.select_slot_fifo(store)
    elif routing_mode == "pillar":
        # --- Classify (skip if already classified) ---
        if video.classified_pillar is None and video.status != "NEEDS_REVIEW":
            try:
                result = classifier.classify(video.transcript, CONTENT_TYPES)
            except classification.ClassificationError:
                store.update_video(video.id, status="FAILED", failure_reason="CLASSIFICATION_FAILED", processed_at=now_iso)
                _move_file(path, FAILED_DIR)
                return Outcome(path, store.get_video_by_hash(file_hash), "FAILED")

            # The classifier already applied its own auto-assign policy: pillar
            # is None whenever it decided to abstain (low confidence, low
            # similarity/margin, empty transcript, out-of-pillar content, ...).
            eligible = result.pillar is not None
            store.update_video(
                video.id,
                classified_pillar=result.pillar,
                classification_confidence=result.confidence,
                classification_reason=result.reason,
                classification_second_score=result.second_score,
                classification_margin=result.margin,
                classifier=result.classifier,
                status="CLASSIFIED" if eligible else "NEEDS_REVIEW",
            )
            video = store.get_video_by_hash(file_hash)

        if video.status == "NEEDS_REVIEW":
            return Outcome(path, video, "NEEDS_REVIEW")

        slot = slot_matcher.select_slot(store, video.classified_pillar)
    else:
        raise ValueError(f"Unsupported routing_mode: {routing_mode!r}")

    # --- Deterministic slot match ---
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
            extra = ""
            if v.classification_margin is not None:
                extra = f" second={v.classification_second_score} margin={v.classification_margin}"
            print(f"      classification[{v.classifier}]: pillar={v.classified_pillar} "
                  f"score={v.classification_confidence}{extra} reason={v.classification_reason!r}")
        if v.transcription_status == "FAILED":
            print(f"      transcription: FAILED ({v.failure_reason}) — still eligible for FIFO scheduling")
        if v.caption_source is not None:
            snippet = (v.caption_text[:120] + "…") if v.caption_text and len(v.caption_text) > 120 else v.caption_text
            print(f"      caption[{v.caption_source}]: {snippet!r}")
        if outcome.slot is not None:
            print(f"      slot: {outcome.slot.scheduled_at}")
        if v.failure_reason and v.transcription_status != "FAILED":
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
    transcription_failed = sum(1 for o in outcomes if o.video.transcription_status == "FAILED")
    if transcription_failed:
        # FIFO-only outcome (pillar mode still hard-fails on a transcription
        # error) — surfaced here so it's never silently discarded.
        print(f"Transcription failed (still scheduled): {transcription_failed}")

    assignments = [o for o in outcomes if o.kind == assigned_kind]
    if assignments:
        print(f"\n{assigned_label}s\n")
        for o in assignments:
            when = datetime.fromisoformat(o.slot.scheduled_at).strftime("%b %d, %I:%M %p").replace(" 0", " ")
            if o.video.classified_pillar is not None:
                label = get_content_label(o.video.classified_pillar)
                pct = round(o.video.classification_confidence * 100)
                # "Confidence" only for Claude, whose score is a genuine
                # self-reported confidence; embeddings report raw cosine
                # similarity, which is not a calibrated probability — see
                # docs/decisions/0003-local-embedding-classification.md.
                score_label = "Confidence" if o.video.classifier == "claude" else "Similarity score"
                print(f"{o.path.name}\n{label}\n{when}\n{score_label}: {pct}%\n")
            else:
                caption_note = f"\nCaption: {o.video.caption_text!r}" if o.video.caption_text else ""
                print(f"{o.path.name}\n{when}{caption_note}\n")

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

    try:
        scheduling.validate_routing_mode(ROUTING_MODE)
        caption.validate_caption_mode(CAPTION_MODE)
    except (scheduling.ScheduleConfigError, caption.CaptionConfigError) as exc:
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
            outcome = process_one(store, transcriber, classifier, path, dry_run=args.dry_run, routing_mode=ROUTING_MODE)
            outcomes.append(outcome)
            _print_progress(outcome, verbose=args.verbose)

    _print_summary(outcomes, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
