"""Tests for evaluate_classifier.py: dataset loading, metrics, confusion
reporting, and threshold-sweep math — all without a real embedding/Claude
call (sweep math operates on hand-built pre-computed scores; run_classifier
is exercised with a fake classifier)."""

from pathlib import Path

import pytest

import evaluate_classifier as ec
from content_automation.media.classification import ClassificationError, ClassificationResult

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eval_sample"


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def test_load_dataset_reads_fixture():
    samples = ec.load_dataset(FIXTURE_DIR)
    assert len(samples) == 5
    by_id = {s.video_id: s for s in samples}
    assert by_id["sample_building_1"].true_pillar == "building"
    assert by_id["sample_out_of_pillar_1"].true_pillar is None
    assert "cold call" in by_id["sample_acquisition_1"].transcript.lower()


def test_load_dataset_missing_labels_csv(tmp_path):
    with pytest.raises(ec.DatasetError):
        ec.load_dataset(tmp_path)


def test_load_dataset_missing_transcript_file(tmp_path):
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "labels.csv").write_text("video_id,true_pillar\nmissing_one,building\n")
    with pytest.raises(ec.DatasetError):
        ec.load_dataset(tmp_path)


def test_load_dataset_rejects_bad_header(tmp_path):
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "labels.csv").write_text("id,pillar\n1,building\n")
    with pytest.raises(ec.DatasetError):
        ec.load_dataset(tmp_path)


# ---------------------------------------------------------------------------
# Metrics (known predictions -> expected metrics)
# ---------------------------------------------------------------------------

class FakeClassifier:
    def __init__(self, predictions: dict[str, ClassificationResult]):
        self.predictions = predictions

    def classify(self, transcript, pillars):
        return self.predictions[transcript]


def _sample(video_id, true_pillar, transcript):
    return ec.Sample(video_id=video_id, true_pillar=true_pillar, transcript=transcript)


def test_summarize_known_predictions_produce_expected_metrics():
    samples = [
        _sample("a", "building", "t_a"),   # correct auto-assignment
        _sample("b", "acquisition", "t_b"),  # wrong auto-assignment (predicted building)
        _sample("c", "mindset", "t_c"),    # abstained (review)
        _sample("d", None, "t_d"),         # correctly abstained on out-of-pillar
    ]
    predictions = {
        "t_a": ClassificationResult(pillar="building", confidence=0.9, reason="x", classifier="fake"),
        "t_b": ClassificationResult(pillar="building", confidence=0.6, reason="x", classifier="fake"),
        "t_c": ClassificationResult(pillar=None, confidence=0.4, reason="x", classifier="fake"),
        "t_d": ClassificationResult(pillar=None, confidence=0.3, reason="x", classifier="fake"),
    }
    results = ec.run_classifier(FakeClassifier(predictions), samples)
    metrics = ec.summarize(results)

    assert metrics.total == 4
    assert metrics.auto_assigned == 2
    assert metrics.auto_assigned_correct == 1
    assert metrics.wrong_auto_assignments == 1
    assert metrics.review == 2
    assert metrics.errors == 0
    assert metrics.per_pillar["building"] == {"total": 1, "correct": 1}
    assert metrics.per_pillar["acquisition"] == {"total": 1, "correct": 0}


def test_summarize_counts_classification_errors_separately():
    samples = [_sample("a", "building", "t_a")]

    class RaisingClassifier:
        def classify(self, transcript, pillars):
            raise ClassificationError("boom")

    results = ec.run_classifier(RaisingClassifier(), samples)
    metrics = ec.summarize(results)

    assert metrics.errors == 1
    assert metrics.total == 1
    assert metrics.auto_assigned == 0
    assert metrics.review == 0  # an error is not the same as an abstention


def test_confusion_pairs_counts_mismatches_only():
    samples = [
        _sample("a", "building", "t_a"),
        _sample("b", "building", "t_b"),
        _sample("c", "acquisition", "t_c"),
    ]
    predictions = {
        "t_a": ClassificationResult(pillar="acquisition", confidence=0.9, reason="x", classifier="fake"),
        "t_b": ClassificationResult(pillar="acquisition", confidence=0.9, reason="x", classifier="fake"),
        "t_c": ClassificationResult(pillar="acquisition", confidence=0.9, reason="x", classifier="fake"),  # correct
    }
    results = ec.run_classifier(FakeClassifier(predictions), samples)

    pairs = ec.confusion_pairs(results)

    assert pairs[("building", "acquisition")] == 2
    assert ("acquisition", "acquisition") not in pairs


# ---------------------------------------------------------------------------
# Threshold sweep (pure math over pre-computed scores)
# ---------------------------------------------------------------------------

def test_sweep_from_scores_is_deterministic():
    scored_samples = [
        (_sample("a", "building", "t"), [("building", 0.8), ("acquisition", 0.5)]),
        (_sample("b", "acquisition", "t"), [("acquisition", 0.6), ("building", 0.55)]),
    ]
    rows1 = ec.sweep_from_scores(scored_samples, [0.5, 0.7], [0.02, 0.1])
    rows2 = ec.sweep_from_scores(scored_samples, [0.5, 0.7], [0.02, 0.1])
    assert rows1 == rows2


def test_sweep_from_scores_higher_margin_reduces_wrong_assignments():
    # "b" is ambiguous (margin only 0.05); a stricter margin should push it to review.
    scored_samples = [
        (_sample("a", "building", "t"), [("building", 0.8), ("acquisition", 0.3)]),
        (_sample("b", "mindset", "t"), [("building", 0.6), ("mindset", 0.55)]),  # wrong at loose margin
    ]
    loose = ec.sweep_from_scores(scored_samples, [0.5], [0.02])[0]
    strict = ec.sweep_from_scores(scored_samples, [0.5], [0.08])[0]

    assert loose.auto_assigned == 2
    assert loose.wrong_auto_assignments == 1
    assert strict.auto_assigned == 1  # "b" now abstains
    assert strict.wrong_auto_assignments == 0
    assert strict.review == 1


def test_sweep_from_scores_treats_empty_score_list_as_review():
    scored_samples = [(_sample("a", "building", "t"), None)]
    rows = ec.sweep_from_scores(scored_samples, [0.5], [0.05])
    assert rows[0].review == 1
    assert rows[0].auto_assigned == 0


# ---------------------------------------------------------------------------
# No production DB mutation
# ---------------------------------------------------------------------------

def test_evaluate_classifier_never_imports_content_store():
    """content_store may be *mentioned* in comments/docstrings explaining why
    it's avoided; it must never actually be imported or used."""
    source = Path(ec.__file__).read_text(encoding="utf-8")
    assert "import content_store" not in source
    assert "from content_store" not in source
    assert not hasattr(ec, "ContentStore")
