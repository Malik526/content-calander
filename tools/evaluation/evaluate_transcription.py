"""
evaluate_transcription.py — Benchmark FasterWhisperTranscriber against the
Shofo real-video evaluation corpus (download_shofo_samples.py's output).

What it does:
  For each downloaded clip in evaluation/video_pipeline/metadata.jsonl, runs
  the real, production transcription.FasterWhisperTranscriber (no second
  Whisper implementation) against the local MP4, compares its output to the
  dataset-supplied WEBVTT reference transcript after normalizing both, and
  reports word/character error rate plus runtime (realtime_factor =
  transcription time / video duration).

  The dataset transcript is a reference ASR output (produced by another
  model, NVIDIA Canary-Qwen-2.5B), not human ground truth — this script
  reports differences, it does not assume every mismatch means
  faster-whisper is wrong. This is an ASR accuracy/runtime benchmark, not a
  pillar-classification benchmark (see evaluation/video_pipeline/README.md
  for why pillar accuracy is explicitly out of scope for this corpus).

  Deliberately separate from evaluate_classifier.py (different subject) and
  from process_content.py (this is benchmark tooling, not the pipeline
  itself) — imports only media.py, transcription.py, transcript_metrics.py.
  Never imports content_store, slot_matcher, classification, or
  calendar_manager.

Run:
  python3 evaluate_transcription.py
  python3 evaluate_transcription.py --manifest evaluation/video_pipeline/metadata.jsonl
  python3 evaluate_transcription.py --limit 3
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import transcript_metrics
from content_automation.config import REPO_ROOT
from content_automation.media import inspection as media
from content_automation.media import transcription

# Milestone 3.0: this script moved from repo root to tools/evaluation/ —
# REPO_ROOT keeps this pointing at the real repo-root evaluation/ dir.
DEFAULT_MANIFEST = REPO_ROOT / "evaluation" / "video_pipeline" / "metadata.jsonl"
DEFAULT_RESULTS = REPO_ROOT / "evaluation" / "video_pipeline" / "results.jsonl"
SNIPPET_LENGTH = 120


class ManifestError(Exception):
    pass


@dataclass
class ManifestRecord:
    sample_index: int
    video_id: str
    local_path: str
    reference_transcript: str
    has_music: bool | None
    duration_ms: float | None


@dataclass
class TranscriptionOutcome:
    video_id: str
    reference_transcript: str
    predicted_transcript: str
    wer: float | None
    cer: float | None
    transcription_seconds: float
    video_duration_seconds: float | None
    realtime_factor: float | None
    has_music: bool | None
    error: str | None


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: Path) -> list[ManifestRecord]:
    if not manifest_path.exists():
        raise ManifestError(
            f"No manifest found at {manifest_path}. Run download_shofo_samples.py first."
        )

    records = []
    with manifest_path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"{manifest_path}:{line_number}: invalid JSON: {exc}") from exc
            records.append(ManifestRecord(
                sample_index=row.get("sample_index", line_number),
                video_id=row["video_id"],
                local_path=row["local_path"],
                reference_transcript=row.get("reference_transcript") or "",
                has_music=row.get("has_music"),
                duration_ms=row.get("duration_ms"),
            ))

    if not records:
        raise ManifestError(f"{manifest_path} has no rows")
    return records


# ---------------------------------------------------------------------------
# Per-clip evaluation
# ---------------------------------------------------------------------------

def evaluate_one(
    record: ManifestRecord,
    transcriber: transcription.Transcriber,
    videos_dir: Path,
) -> TranscriptionOutcome:
    video_path = videos_dir.parent / record.local_path
    reference_plain = transcript_metrics.strip_webvtt(record.reference_transcript)
    fallback_duration = (record.duration_ms / 1000.0) if record.duration_ms else None

    try:
        info = media.inspect_media(video_path)
    except media.MediaError as exc:
        return TranscriptionOutcome(
            video_id=record.video_id, reference_transcript=reference_plain, predicted_transcript="",
            wer=None, cer=None, transcription_seconds=0.0, video_duration_seconds=fallback_duration,
            realtime_factor=None, has_music=record.has_music, error=f"media inspection failed: {exc}",
        )

    video_duration = info.duration_seconds or fallback_duration
    audio_path = None
    predicted = ""
    error = None
    t0 = time.perf_counter()
    try:
        audio_path = media.extract_audio(info)
        result = transcriber.transcribe(audio_path)
        predicted = result.text
    except (media.MediaError, transcription.TranscriptionError) as exc:
        error = str(exc)
    finally:
        elapsed = time.perf_counter() - t0
        if audio_path is not None:
            media.cleanup_audio(audio_path)

    wer = transcript_metrics.word_error_rate(reference_plain, predicted) if error is None else None
    cer = transcript_metrics.character_error_rate(reference_plain, predicted) if error is None else None
    realtime_factor = (elapsed / video_duration) if (error is None and video_duration) else None

    return TranscriptionOutcome(
        video_id=record.video_id, reference_transcript=reference_plain, predicted_transcript=predicted,
        wer=wer, cer=cer, transcription_seconds=elapsed, video_duration_seconds=video_duration,
        realtime_factor=realtime_factor, has_music=record.has_music, error=error,
    )


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

@dataclass
class AggregateMetrics:
    total: int
    scored: int
    errors: int
    mean_wer: float | None
    median_wer: float | None
    best: TranscriptionOutcome | None
    worst: TranscriptionOutcome | None
    mean_wer_with_music: float | None
    mean_wer_without_music: float | None


def summarize(outcomes: list[TranscriptionOutcome]) -> AggregateMetrics:
    scoreable = [o for o in outcomes if o.wer is not None]
    wers = [o.wer for o in scoreable]

    def _mean_wer_for(music_flag: bool) -> float | None:
        subset = [o.wer for o in scoreable if o.has_music is music_flag]
        return statistics.mean(subset) if subset else None

    best = min(scoreable, key=lambda o: o.wer) if scoreable else None
    worst = max(scoreable, key=lambda o: o.wer) if scoreable else None

    return AggregateMetrics(
        total=len(outcomes),
        scored=len(scoreable),
        errors=sum(1 for o in outcomes if o.error is not None),
        mean_wer=statistics.mean(wers) if wers else None,
        median_wer=statistics.median(wers) if wers else None,
        best=best,
        worst=worst,
        mean_wer_with_music=_mean_wer_for(True),
        mean_wer_without_music=_mean_wer_for(False),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text[:SNIPPET_LENGTH] + ("…" if len(text) > SNIPPET_LENGTH else "")


def print_report(outcomes: list[TranscriptionOutcome], metrics: AggregateMetrics) -> None:
    print("\nTranscription Evaluation — Shofo real-video corpus")
    print("=" * 60)
    for o in outcomes:
        music = "yes" if o.has_music else ("no" if o.has_music is False else "unknown")
        duration = f"{o.video_duration_seconds:.1f}s" if o.video_duration_seconds else "unknown"
        if o.error:
            print(f"\n{o.video_id}  duration={duration}  music={music}  ERROR: {o.error}")
            continue
        wer_str = f"{o.wer:.1%}" if o.wer is not None else "N/A (empty reference)"
        print(f"\n{o.video_id}  duration={duration}  music={music}  WER={wer_str}")
        print(f"  reference: {_snippet(o.reference_transcript)!r}")
        print(f"  predicted: {_snippet(o.predicted_transcript)!r}")

    print("\n" + "-" * 60)
    print(f"Total: {metrics.total}  Scored: {metrics.scored}  Errors: {metrics.errors}")
    if metrics.mean_wer is not None:
        print(f"Mean WER: {metrics.mean_wer:.1%}  Median WER: {metrics.median_wer:.1%}")
    if metrics.best is not None:
        print(f"Best:  {metrics.best.video_id} (WER {metrics.best.wer:.1%})")
    if metrics.worst is not None:
        print(f"Worst: {metrics.worst.video_id} (WER {metrics.worst.wer:.1%})")
    if metrics.mean_wer_with_music is not None:
        print(f"Mean WER (has_music=True):  {metrics.mean_wer_with_music:.1%}")
    if metrics.mean_wer_without_music is not None:
        print(f"Mean WER (has_music=False): {metrics.mean_wer_without_music:.1%}")


def write_results(outcomes: list[TranscriptionOutcome], results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as f:
        for outcome in outcomes:
            f.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark FasterWhisperTranscriber against the Shofo real-video evaluation corpus."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help=f"Default: {DEFAULT_MANIFEST}")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS, help=f"Default: {DEFAULT_RESULTS}")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N clips.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        media.check_ffmpeg_available()
    except media.FfmpegNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        records = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.limit is not None:
        records = records[: args.limit]

    transcriber = transcription.FasterWhisperTranscriber()
    videos_dir = args.manifest.parent / "videos"

    outcomes = [evaluate_one(record, transcriber, videos_dir) for record in records]
    metrics = summarize(outcomes)
    print_report(outcomes, metrics)
    write_results(outcomes, args.output)
    print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
