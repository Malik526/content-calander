"""
download_shofo_samples.py — Pull a small, varied, reproducible sample of real
social-video clips + reference metadata from the Shofo/shofo-talking-head-en
Hugging Face dataset, for exercising the content-automation pipeline against
real media instead of synthetic test fixtures.

What it does (and does NOT do):
  Selects a small number of rows (default 12) from the dataset's metadata,
  filtered for usability (has_audio, English, a real downloadable file
  reference, a real transcript) and stratified across short/medium/long
  duration and has_music for practical variety, then downloads only the
  underlying raw MP4 for each selected row via
  huggingface_hub.hf_hub_download() — never the full ~104GB / ~10k-row
  dataset, and never through the "video" decode feature (which would pull
  in a heavy decoder dependency like torchcodec just to get bytes back
  out). Metadata streaming explicitly disables Video-feature decoding
  (IterableDataset.decode(False), or Video(decode=False) on older
  `datasets` versions) before pulling any row — see _disable_video_decoding
  for why merely checking `ds.features` is not safe here.

  IMPORTANT — verified real schema vs. the dataset card: the dataset has NO
  "file_name" column (unlike what the card's field list suggests). The
  downloadable reference lives inside the raw (non-decoded) "video" feature
  value instead, as an hf://datasets/<repo>@<revision>/<path> URI — see
  normalize_row() / _relative_path_from_video_field() and
  evaluation/video_pipeline/README.md ("Observed Schema") for the verified
  row shape. normalize_row() is the one place Shofo-specific field names
  are translated to this project's internal names; every other function in
  this file (filtering, stratification, download, manifest) only ever sees
  the normalized shape.

  This is a test/evaluation acquisition utility, not part of the production
  pipeline. It must not import content_store, slot_matcher, classification,
  or calendar_manager — it only selects rows, downloads MP4s, and writes a
  local reference-metadata manifest. process_content.py and friends never
  import this module either.

  The dataset's transcript is treated as reference ASR output (produced by
  another model), not ground truth — evaluate_transcription.py reports
  differences, it does not assume every mismatch is faster-whisper's fault.

Dataset access:
  Shofo/shofo-talking-head-en is gated. Before this script can succeed:
    1. Visit https://huggingface.co/datasets/Shofo/shofo-talking-head-en
       while logged in and accept the dataset's access conditions.
    2. Authenticate locally: `huggingface-cli login`, or set HF_TOKEN.
  Never put a Hugging Face token in source code or commit it anywhere.

Dependencies (see requirements-eval.txt — deliberately NOT in
requirements.txt, so the production pipeline's dependency set is untouched):
  datasets, huggingface_hub (huggingface_hub is often already present
  transitively via faster-whisper/fastembed, but is declared explicitly here
  since this script depends on it directly).

Run:
  python3 download_shofo_samples.py --count 12
  python3 download_shofo_samples.py --count 12 --output evaluation/video_pipeline --seed 42

Output:
  evaluation/video_pipeline/metadata.jsonl   (one JSON record per downloaded clip)
  evaluation/video_pipeline/videos/<video_id>.mp4
  Both are gitignored — see evaluation/video_pipeline/README.md.
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from datasets import load_dataset
except ImportError:
    load_dataset = None

try:
    from datasets import Video as _HfVideo
except ImportError:
    _HfVideo = None

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    hf_hub_download = None

from content_automation.config import REPO_ROOT
from content_automation.media import inspection as media

DEFAULT_DATASET = "Shofo/shofo-talking-head-en"
DEFAULT_SPLIT = "train"
# Milestone 3.0: this script moved from repo root to tools/evaluation/ —
# REPO_ROOT keeps this pointing at the real repo-root evaluation/ dir.
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluation" / "video_pipeline"
DEFAULT_COUNT = 12
DEFAULT_SEED = 42

# Duration buckets in milliseconds. Boundaries are total (every duration
# falls in exactly one bucket), not an additional filter — the dataset card
# already bounds clips to roughly 10-300s.
DURATION_BUCKETS = ["short", "medium", "long"]
_SHORT_MEDIUM_BOUNDARY_MS = 30_000
_MEDIUM_LONG_BOUNDARY_MS = 90_000


class DatasetAccessError(Exception):
    """Raised when the dataset can't be read (gated access, missing auth,
    missing dependency, or an unexpected upstream error). Always carries an
    actionable message — never a bare stack trace."""


@dataclass
class DownloadResult:
    sample_index: int
    video_id: str
    success: bool
    local_path: Path | None
    error: str | None
    file_size_bytes: int | None


# ---------------------------------------------------------------------------
# Dataset metadata access (no video decode)
# ---------------------------------------------------------------------------

def _disable_video_decoding(ds):
    """Disable Video-feature decoding before any row is pulled, so reading
    dataset metadata never requires a video decoder backend (torchcodec).

    The "video" column is deliberately KEPT (not removed): its raw,
    non-decoded value is where the downloadable file reference actually
    lives for this dataset (see normalize_row) — there is no separate
    "file_name" column, unlike what the dataset card's field list
    suggests. With decoding disabled, that raw value is just a small dict
    (a path string and a null "bytes"), not actual video bytes, so keeping
    the column costs nothing extra over a metadata-only scan.

    Checking `ds.features` directly (the original approach here) is NOT
    safe: for a streaming dataset whose schema isn't already known, that
    property access peeks one real example to infer types, and that peek
    decodes the "video" field — raising "To support decoding videos, please
    install torchcodec." before iteration even starts, not during it. This
    function never touches `.features`.

    Uses IterableDataset.decode(False) — the documented, version-supported
    way to disable decoding for every Audio/Image/Video feature at once —
    when available; falls back to explicitly recasting the "video" column
    to Video(decode=False) on older `datasets` versions that predate
    .decode().
    """
    if hasattr(ds, "decode"):
        return ds.decode(False)

    if _HfVideo is not None:
        try:
            return ds.cast_column("video", _HfVideo(decode=False))
        except ValueError:
            pass  # no "video" column under this name/config

    return ds


# Real Shofo/shofo-talking-head-en row schema, verified empirically
# (2026-09-13) against a real streamed row with decoding disabled — the
# dataset card does not document field names directly, and does NOT have a
# "file_name" column at all (an assumption an earlier version of this file
# made without verifying, which silently filtered out every row). See
# evaluation/video_pipeline/README.md "Observed Schema" for the full
# verified shape, including the "video" feature's raw structure.
#
# Direct-passthrough fields: source column name -> our internal name.
# "file_name" is NOT in this map — it is derived, not copied (see
# _relative_path_from_video_field / normalize_row below).
_DIRECT_FIELD_MAP = {
    "video_id": "video_id",
    "tiktok_url": "tiktok_url",
    "duration_ms": "duration_ms",
    "resolution": "resolution",
    "width": "width",
    "height": "height",
    "fps": "fps",
    "codec": "codec",
    "bitrate": "bitrate",
    "has_audio": "has_audio",
    "language": "language",
    "has_music": "has_music",
    "transcript": "reference_transcript",
}

# Matches the raw "video" feature's non-decoded path value, e.g.:
#   hf://datasets/Shofo/shofo-talking-head-en@<revision>/videos/70/<id>.mp4
_HF_DATASET_PATH_RE = re.compile(r"^hf://datasets/(?P<repo_id>[^@]+)@(?P<revision>[^/]+)/(?P<path_in_repo>.+)$")


def _relative_path_from_video_field(video_field, dataset_name: str) -> str | None:
    """Extract the path-in-repo needed for hf_hub_download(filename=...)
    from the raw (non-decoded) "video" feature value. Returns None — never
    raises — for anything that isn't a usable reference (missing field,
    wrong type, empty path, or a URI naming a different repo than expected)
    so a row with no usable video is correctly filtered out downstream
    rather than crashing acquisition."""
    if not isinstance(video_field, dict):
        return None
    path = video_field.get("path")
    if not path:
        return None
    match = _HF_DATASET_PATH_RE.match(path)
    if match:
        return match.group("path_in_repo") if match.group("repo_id") == dataset_name else None
    if not path.startswith("hf://"):
        return path  # already a plain relative path
    return None


def normalize_row(raw_row: dict, dataset_name: str) -> dict:
    """Translate one raw Shofo dataset row into this project's internal
    field names/shape (see _DIRECT_FIELD_MAP and module docstring). Every
    other function in this file — filtering, stratification, download,
    manifest building — operates only on this normalized shape, so
    Shofo-specific naming never leaks past this one boundary function.

    A field the source row doesn't have comes through as None here (via
    dict.get's default), distinct from a field the source row has with a
    valid falsy value (has_audio=False, has_music=False, bitrate=0) —
    is_valid_candidate relies on that distinction rather than treating
    both the same."""
    normalized = {internal: raw_row.get(source) for source, internal in _DIRECT_FIELD_MAP.items()}
    normalized["file_name"] = _relative_path_from_video_field(raw_row.get("video"), dataset_name)
    return normalized


def iter_dataset_rows(dataset_name: str, split: str = DEFAULT_SPLIT):
    """Stream raw dataset rows as plain dicts (Shofo's own field names,
    including the still-present but non-decoded "video" feature — see
    normalize_row for the translation boundary). Video decoding is disabled
    before any row is pulled (see _disable_video_decoding) so metadata-only
    streaming never requires torchcodec/decord, and no such dependency is
    ever needed just to read metadata."""
    if load_dataset is None:
        raise DatasetAccessError(
            "The 'datasets' package is required to read Shofo dataset metadata.\n"
            "Install it with: pip install -r requirements-eval.txt"
        )

    try:
        ds = load_dataset(dataset_name, split=split, streaming=True)
        ds = _disable_video_decoding(ds)
        for row in ds:
            yield row
    except DatasetAccessError:
        raise
    except Exception as exc:
        raise DatasetAccessError(
            f"Could not read dataset '{dataset_name}' (split={split!r}): {exc}\n\n"
            "This dataset is gated on Hugging Face. Before retrying:\n"
            f"  1. Visit https://huggingface.co/datasets/{dataset_name} while logged in\n"
            "     and accept the dataset's access conditions.\n"
            "  2. Authenticate locally: `huggingface-cli login`, or set the HF_TOKEN\n"
            "     environment variable to a valid access token.\n"
            "  3. Re-run this script.\n"
            "Never put a Hugging Face token in source code."
        ) from exc


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def is_valid_candidate(row: dict) -> bool:
    """Required filters, applied to a *normalized* row (see normalize_row):
    usable audio, English, a real downloadable file reference, and a
    non-empty reference transcript to compare against later. These are
    never loosened just to produce candidates — a row failing any of them
    (whether the field is missing entirely or genuinely falsy/empty) is
    correctly excluded, not coerced into passing."""
    if row.get("has_audio") is not True:
        return False
    language = (row.get("language") or "").strip().lower()
    if language != "en":
        return False
    if not (row.get("file_name") or "").strip():
        return False
    if not (row.get("reference_transcript") or "").strip():
        return False
    return True


def bucket_for_duration(duration_ms) -> str:
    """Total function over duration: every candidate lands in exactly one of
    short/medium/long, so stratification never silently drops a row."""
    try:
        ms = float(duration_ms)
    except (TypeError, ValueError):
        ms = 0.0
    if ms < _SHORT_MEDIUM_BOUNDARY_MS:
        return "short"
    if ms < _MEDIUM_LONG_BOUNDARY_MS:
        return "medium"
    return "long"


# ---------------------------------------------------------------------------
# Stratified sampling (deterministic given seed + candidate list)
# ---------------------------------------------------------------------------

def _bucket_targets(count: int) -> dict:
    """Split `count` as evenly as possible across the three duration
    buckets, e.g. 12 -> 4/4/4, 10 -> 4/3/3 (extra goes to earlier buckets in
    a fixed order, so the result is deterministic)."""
    base, remainder = divmod(count, len(DURATION_BUCKETS))
    targets = {bucket: base for bucket in DURATION_BUCKETS}
    for bucket in DURATION_BUCKETS[:remainder]:
        targets[bucket] += 1
    return targets


def stratify_sample(candidates: list[dict], count: int, seed: int) -> list[dict]:
    """Pick up to `count` candidates with practical diversity: spread evenly
    across short/medium/long duration buckets, and within each bucket
    alternate between has_music=True/False so both are represented when
    available. Deterministic for a given (candidates, count, seed) — no
    statistically representative sampling, just enough variety to be a
    useful pipeline fixture (see module docstring)."""
    import random

    if count <= 0 or not candidates:
        return []

    buckets: dict[str, list[dict]] = {b: [] for b in DURATION_BUCKETS}
    for row in candidates:
        buckets[bucket_for_duration(row.get("duration_ms"))].append(row)

    selected: list[dict] = []
    selected_ids = set()
    targets = _bucket_targets(count)

    for bucket_name in DURATION_BUCKETS:
        rng = random.Random(f"{seed}:{bucket_name}")
        musical = [r for r in buckets[bucket_name] if r.get("has_music") is True]
        non_musical = [r for r in buckets[bucket_name] if r.get("has_music") is not True]
        rng.shuffle(musical)
        rng.shuffle(non_musical)

        pools = [non_musical, musical]  # alternate, starting with non-musical
        pool_idx = 0
        taken_this_bucket = 0
        # Round-robin across the two pools until the bucket target is met or
        # both pools are exhausted.
        while taken_this_bucket < targets[bucket_name] and (pools[0] or pools[1]):
            pool = pools[pool_idx % 2]
            pool_idx += 1
            if not pool:
                continue
            row = pool.pop(0)
            selected.append(row)
            selected_ids.add(id(row))
            taken_this_bucket += 1

    # Backfill from any unselected candidates (across all buckets) if the
    # buckets alone couldn't fill the requested count.
    if len(selected) < count:
        leftover = [r for r in candidates if id(r) not in selected_ids]
        random.Random(f"{seed}:backfill").shuffle(leftover)
        selected.extend(leftover[: count - len(selected)])

    return selected[:count]


# ---------------------------------------------------------------------------
# Download + manifest
# ---------------------------------------------------------------------------

def _video_id_for(row: dict) -> str:
    video_id = row.get("video_id")
    if video_id:
        return str(video_id)
    return Path(str(row["file_name"])).stem


def download_sample(
    row: dict,
    sample_index: int,
    dataset_name: str,
    videos_dir: Path,
    *,
    verify_media: bool,
) -> DownloadResult:
    """Download one row's raw MP4 via hf_hub_download(file_name) — never the
    decoded "video" feature — and copy it into videos_dir. Idempotent: an
    existing non-empty file at the target path is left alone rather than
    re-downloaded. Never raises; failures are reported so one bad clip
    doesn't abort the rest of the sample set."""
    video_id = _video_id_for(row)
    target = videos_dir / f"{video_id}.mp4"

    if target.exists() and target.stat().st_size > 0:
        return DownloadResult(sample_index, video_id, True, target, None, target.stat().st_size)

    if hf_hub_download is None:
        return DownloadResult(
            sample_index, video_id, False, None,
            "The 'huggingface_hub' package is required to download samples. "
            "Install it with: pip install -r requirements-eval.txt",
            None,
        )

    try:
        cached_path = Path(hf_hub_download(
            repo_id=dataset_name, repo_type="dataset", filename=row["file_name"],
        ))
    except Exception as exc:
        return DownloadResult(sample_index, video_id, False, None, f"download failed: {exc}", None)

    if not cached_path.exists() or cached_path.stat().st_size == 0:
        return DownloadResult(sample_index, video_id, False, None, "downloaded file missing or empty", None)

    videos_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(cached_path, target)
    except OSError as exc:
        return DownloadResult(sample_index, video_id, False, None, f"could not copy downloaded file: {exc}", None)

    if verify_media:
        try:
            media.inspect_media(target)
        except media.MediaError as exc:
            target.unlink(missing_ok=True)
            return DownloadResult(sample_index, video_id, False, None, f"media inspection failed: {exc}", None)

    return DownloadResult(sample_index, video_id, True, target, None, target.stat().st_size)


def build_manifest_record(row: dict, sample_index: int, local_path: Path, output_dir: Path, dataset_name: str) -> dict:
    """`row` must already be normalize_row()'s output — this only adds
    acquisition-run bookkeeping (sample_index, local_path, dataset); it does
    not translate any more Shofo-specific fields."""
    video_id = _video_id_for(row)
    return {
        "sample_index": sample_index,
        "video_id": video_id,
        "file_name": row.get("file_name"),
        "local_path": str(local_path.relative_to(output_dir)),
        "tiktok_url": row.get("tiktok_url"),
        "duration_ms": row.get("duration_ms"),
        "resolution": row.get("resolution"),
        "width": row.get("width"),
        "height": row.get("height"),
        "fps": row.get("fps"),
        "codec": row.get("codec"),
        "bitrate": row.get("bitrate"),
        "has_audio": row.get("has_audio"),
        "language": row.get("language"),
        "has_music": row.get("has_music"),
        "reference_transcript": row.get("reference_transcript"),
        "dataset": dataset_name,
    }


def write_manifest(records: list[dict], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download a small, varied, reproducible sample of raw MP4s + reference "
            "metadata from the Shofo/shofo-talking-head-en Hugging Face dataset. "
            "Never downloads the full dataset or decodes video during selection."
        )
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help=f"Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Reproducible sampling seed (default 42).")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print the discovered raw source schema (column names) before filtering — useful if the dataset's field names ever change.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir: Path = args.output
    videos_dir = output_dir / "videos"
    manifest_path = output_dir / "metadata.jsonl"

    try:
        raw_rows = list(iter_dataset_rows(args.dataset, args.split))
    except DatasetAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        if raw_rows:
            print(f"Discovered source keys (first row): {sorted(raw_rows[0].keys())}")
        else:
            print("Discovered source keys: <no rows returned by the dataset>")

    candidates = [row for row in (normalize_row(r, args.dataset) for r in raw_rows) if is_valid_candidate(row)]
    if not candidates:
        print(
            "ERROR: no candidate rows passed filtering "
            "(has_audio=True, language=en, non-empty file_name, non-empty transcript).",
            file=sys.stderr,
        )
        sys.exit(1)

    selected = stratify_sample(candidates, args.count, args.seed)
    if len(selected) < args.count:
        print(f"NOTE: only {len(selected)} candidate(s) available after filtering (requested {args.count}).")

    verify_media = True
    try:
        media.check_ffmpeg_available()
    except media.FfmpegNotFoundError:
        verify_media = False
        print("NOTE: ffmpeg/ffprobe not found on PATH — skipping post-download media verification.")

    records = []
    downloaded = failed = 0
    total_bytes = 0
    for index, row in enumerate(selected, start=1):
        result = download_sample(row, index, args.dataset, videos_dir, verify_media=verify_media)
        if result.success:
            downloaded += 1
            total_bytes += result.file_size_bytes or 0
            records.append(build_manifest_record(row, index, result.local_path, output_dir, args.dataset))
        else:
            failed += 1
            print(f"FAILED sample {index} ({result.video_id}): {result.error}", file=sys.stderr)

    write_manifest(records, manifest_path)

    print(f"\nDownloaded: {downloaded}")
    print(f"Failed: {failed}")
    print(f"Total disk usage: {total_bytes / (1024 * 1024):.1f} MB")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
