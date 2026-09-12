"""
evaluate_classifier.py — Benchmark a ContentClassifier against a local,
manually labeled golden dataset of real transcripts.

What it does:
  Loads labeled transcripts, classifies each with the chosen classifier, and
  reports accuracy/review/wrong-auto-assignment metrics plus a confusion
  breakdown — so EmbeddingClassifier and ClaudeClassifier can be compared on
  the exact same real data, and so EMBEDDING_MIN_SIMILARITY/MIN_MARGIN can be
  chosen from evidence instead of guessed. See
  docs/decisions/0003-local-embedding-classification.md.

  Never imports content_store — this script cannot mutate videos or
  content_slots, claim a slot, or otherwise touch production state.

Dataset format (default directory: evaluation/, gitignored — see
evaluation/README.md for the private, real-transcript corpus; a small
synthetic fixture for testing this script lives in tests/fixtures/eval_sample/):

    <dataset>/
        labels.csv              # header: video_id,true_pillar
        transcripts/
            <video_id>.txt

  Leave true_pillar blank (or write "none") for a transcript that should not
  match any configured pillar.

Run:
  python3 evaluate_classifier.py --classifier embeddings
  python3 evaluate_classifier.py --classifier claude
  python3 evaluate_classifier.py --classifier embeddings --sweep
  python3 evaluate_classifier.py --classifier embeddings --sweep --similarity 0.4,0.5,0.6 --margin 0.02,0.05,0.08
  python3 evaluate_classifier.py --dataset tests/fixtures/eval_sample --classifier embeddings

Dependencies:
  classification.py, config.py. No content_store import, by design.
"""

import argparse
import csv
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import classification
from config import CONTENT_TYPES

DEFAULT_DATASET_DIR = Path(__file__).with_name("evaluation")
DEFAULT_SIMILARITY_GRID = [0.3, 0.4, 0.5, 0.6, 0.7]
DEFAULT_MARGIN_GRID = [0.0, 0.02, 0.05, 0.08, 0.1]


class DatasetError(Exception):
    pass


@dataclass
class Sample:
    video_id: str
    true_pillar: str | None  # None = expected to not match any configured pillar
    transcript: str


@dataclass
class SampleResult:
    sample: Sample
    result: classification.ClassificationResult | None  # None if classification raised
    error: str | None
    latency_seconds: float


@dataclass
class Metrics:
    total: int
    correct: int
    incorrect: int
    review: int
    auto_assigned: int
    auto_assigned_correct: int
    wrong_auto_assignments: int
    per_pillar: dict[str, dict[str, int]]
    avg_latency_seconds: float
    errors: int


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def load_dataset(dataset_dir: Path) -> list[Sample]:
    labels_path = dataset_dir / "labels.csv"
    if not labels_path.exists():
        raise DatasetError(f"No labels.csv found at {labels_path}")

    samples: list[Sample] = []
    with labels_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or {"video_id", "true_pillar"} - set(reader.fieldnames):
            raise DatasetError(f"{labels_path} must have columns: video_id,true_pillar")
        for row in reader:
            video_id = (row.get("video_id") or "").strip()
            if not video_id:
                continue
            true_pillar_raw = (row.get("true_pillar") or "").strip().lower()
            true_pillar = None if true_pillar_raw in ("", "none") else true_pillar_raw
            transcript_path = dataset_dir / "transcripts" / f"{video_id}.txt"
            if not transcript_path.exists():
                raise DatasetError(f"Missing transcript for '{video_id}': {transcript_path}")
            samples.append(Sample(video_id=video_id, true_pillar=true_pillar, transcript=transcript_path.read_text(encoding="utf-8")))

    if not samples:
        raise DatasetError(f"{labels_path} has no rows")
    return samples


# ---------------------------------------------------------------------------
# Running a classifier over the dataset
# ---------------------------------------------------------------------------

def run_classifier(classifier: classification.ContentClassifier, samples: list[Sample]) -> list[SampleResult]:
    results = []
    for sample in samples:
        t0 = time.perf_counter()
        try:
            result = classifier.classify(sample.transcript, CONTENT_TYPES)
            error = None
        except classification.ClassificationError as exc:
            result = None
            error = str(exc)
        results.append(SampleResult(sample=sample, result=result, error=error, latency_seconds=time.perf_counter() - t0))
    return results


def summarize(sample_results: list[SampleResult]) -> Metrics:
    total = len(sample_results)
    correct = incorrect = review = auto_assigned = auto_assigned_correct = wrong_auto = errors = 0
    per_pillar: dict[str, dict[str, int]] = {}
    latencies = []

    for sr in sample_results:
        latencies.append(sr.latency_seconds)
        if sr.result is None:
            errors += 1
            continue

        true_pillar = sr.sample.true_pillar
        predicted = sr.result.pillar

        if true_pillar:
            bucket = per_pillar.setdefault(true_pillar, {"total": 0, "correct": 0})
            bucket["total"] += 1

        if predicted is None:
            review += 1
            continue

        auto_assigned += 1
        if predicted == true_pillar:
            correct += 1
            auto_assigned_correct += 1
            if true_pillar:
                per_pillar[true_pillar]["correct"] += 1
        else:
            incorrect += 1
            wrong_auto += 1

    return Metrics(
        total=total, correct=correct, incorrect=incorrect, review=review,
        auto_assigned=auto_assigned, auto_assigned_correct=auto_assigned_correct,
        wrong_auto_assignments=wrong_auto, per_pillar=per_pillar,
        avg_latency_seconds=(sum(latencies) / len(latencies)) if latencies else 0.0,
        errors=errors,
    )


def confusion_pairs(sample_results: list[SampleResult]) -> Counter:
    counter: Counter = Counter()
    for sr in sample_results:
        if sr.result is None or sr.result.pillar is None:
            continue
        if sr.result.pillar != sr.sample.true_pillar:
            counter[(sr.sample.true_pillar or "(none)", sr.result.pillar)] += 1
    return counter


def print_report(sample_results: list[SampleResult], metrics: Metrics, classifier_name: str) -> None:
    print(f"\nEvaluation — classifier={classifier_name}")
    print("=" * 60)
    print(f"Total samples: {metrics.total}")
    if metrics.errors:
        print(f"Classification errors (excluded from metrics below): {metrics.errors}")
    print(f"Auto-assigned: {metrics.auto_assigned}")
    auto_acc = metrics.auto_assigned_correct / metrics.auto_assigned if metrics.auto_assigned else 0.0
    print(f"  Auto-assigned accuracy: {auto_acc:.1%}")
    print(f"  WRONG AUTO-ASSIGNMENTS: {metrics.wrong_auto_assignments}")
    review_rate = metrics.review / metrics.total if metrics.total else 0.0
    print(f"Needs review / abstained: {metrics.review} ({review_rate:.1%} review rate)")
    print(f"Average latency: {metrics.avg_latency_seconds * 1000:.1f} ms/sample")

    if metrics.per_pillar:
        print("\nPer-pillar accuracy (of samples truly labeled that pillar, how many were correctly auto-assigned):")
        for pillar, bucket in sorted(metrics.per_pillar.items()):
            acc = bucket["correct"] / bucket["total"] if bucket["total"] else 0.0
            print(f"  {pillar:<15} {bucket['correct']}/{bucket['total']} ({acc:.1%})")

    pairs = confusion_pairs(sample_results)
    if pairs:
        print("\nConfusion:")
        for (true_pillar, predicted), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
            print(f"  Expected {true_pillar} / Predicted {predicted}: {count}")

    mistakes = [sr for sr in sample_results if sr.result and sr.result.pillar and sr.result.pillar != sr.sample.true_pillar]
    if mistakes:
        print("\nWrong auto-assignments (detail):")
        for sr in mistakes:
            r = sr.result
            preview = " ".join(sr.sample.transcript.split())[:100]
            print(
                f"  {sr.sample.video_id}: expected={sr.sample.true_pillar or '(none)'} predicted={r.pillar} "
                f"top={r.confidence:.3f} second={r.second_score} margin={r.margin} — {preview!r}"
            )


# ---------------------------------------------------------------------------
# Threshold sweep (embeddings only — MIN_SIMILARITY/MIN_MARGIN are an
# embeddings-specific concept; ClaudeClassifier has a single confidence gate)
# ---------------------------------------------------------------------------

def score_samples_with_embeddings(samples: list[Sample]) -> list[tuple[Sample, list[tuple[str, float]] | None]]:
    """Score every sample once against a shared EmbeddingClassifier instance
    (thresholds irrelevant here — score() ignores them) so a sweep never
    re-embeds per threshold combination."""
    scorer = classification.EmbeddingClassifier(min_similarity=-1.0, min_margin=0.0)
    scored = []
    for sample in samples:
        try:
            scored.append((sample, scorer.score(sample.transcript, CONTENT_TYPES)))
        except classification.ClassificationError:
            scored.append((sample, None))
    return scored


@dataclass
class SweepRow:
    min_similarity: float
    min_margin: float
    auto_assigned: int
    wrong_auto_assignments: int
    review: int
    auto_assigned_accuracy: float


def sweep_from_scores(
    scored_samples: list[tuple[Sample, list[tuple[str, float]] | None]],
    similarity_grid: list[float],
    margin_grid: list[float],
) -> list[SweepRow]:
    """Pure grid evaluation over pre-computed scores — no model calls, so
    this is cheap to unit test and cheap to run for a large grid."""
    rows = []
    for min_similarity in similarity_grid:
        for min_margin in margin_grid:
            auto = wrong = review = correct = 0
            for sample, scored in scored_samples:
                if not scored:
                    review += 1
                    continue
                top_key, top_score = scored[0]
                second_score = scored[1][1] if len(scored) > 1 else -1.0
                # The reason string _gate_decision would build isn't used here
                # (sweep only needs the routing decision), so pass top_key
                # itself rather than looking up a real CONTENT_TYPES label —
                # sweep_from_scores works on caller-supplied scored_samples,
                # which may use pillar keys that aren't in the live config
                # (e.g. a saved/replayed sweep from a previous pillar strategy).
                pillar, _ = classification._gate_decision(top_key, top_score, second_score, min_similarity, min_margin, top_key)
                if pillar is None:
                    review += 1
                else:
                    auto += 1
                    if pillar == sample.true_pillar:
                        correct += 1
                    else:
                        wrong += 1
            rows.append(SweepRow(
                min_similarity=min_similarity, min_margin=min_margin,
                auto_assigned=auto, wrong_auto_assignments=wrong, review=review,
                auto_assigned_accuracy=(correct / auto) if auto else 0.0,
            ))
    return rows


def print_sweep(rows: list[SweepRow]) -> None:
    print("\nThreshold sweep (embeddings) — prioritize low WRONG AUTO-ASSIGNMENTS over raw accuracy:")
    print(f"{'similarity':>10} {'margin':>8} {'auto':>6} {'wrong':>6} {'review':>7} {'auto_acc':>9}")
    for row in rows:
        print(
            f"{row.min_similarity:>10.2f} {row.min_margin:>8.2f} {row.auto_assigned:>6} "
            f"{row.wrong_auto_assignments:>6} {row.review:>7} {row.auto_assigned_accuracy:>8.1%}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark a ContentClassifier against a local labeled transcript dataset. Never touches the production database."
    )
    parser.add_argument("--classifier", choices=["embeddings", "claude"], default="embeddings")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR, help=f"Default: {DEFAULT_DATASET_DIR}")
    parser.add_argument("--sweep", action="store_true", help="Sweep MIN_SIMILARITY/MIN_MARGIN candidates (embeddings only).")
    parser.add_argument("--similarity", default=None, help="Comma-separated similarity candidates, e.g. 0.4,0.5,0.6")
    parser.add_argument("--margin", default=None, help="Comma-separated margin candidates, e.g. 0.02,0.05,0.08")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        samples = load_dataset(args.dataset)
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(samples)} labeled sample(s) from {args.dataset}")
    pillar_counts = Counter(sample.true_pillar or "(none)" for sample in samples)
    print("Sample counts per true pillar:", dict(sorted(pillar_counts.items())))

    if args.sweep:
        if args.classifier != "embeddings":
            print("ERROR: --sweep is only supported with --classifier embeddings", file=sys.stderr)
            sys.exit(1)
        similarity_grid = [float(x) for x in args.similarity.split(",")] if args.similarity else DEFAULT_SIMILARITY_GRID
        margin_grid = [float(x) for x in args.margin.split(",")] if args.margin else DEFAULT_MARGIN_GRID
        scored_samples = score_samples_with_embeddings(samples)
        print_sweep(sweep_from_scores(scored_samples, similarity_grid, margin_grid))
        return

    try:
        classifier = classification.build_classifier(args.classifier)
    except classification.ClassificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    sample_results = run_classifier(classifier, samples)
    print_report(sample_results, summarize(sample_results), args.classifier)


if __name__ == "__main__":
    main()
