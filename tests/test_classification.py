"""Unit tests for classification.py's validation layer (no network calls).

classify() itself calls the Claude API; what's testable and important without
a live credential is the contract around it: the classifier can never emit a
pillar outside the configured set, confidence is clamped, and a malformed
response raises ClassificationError so process_content.py can map it to
CLASSIFICATION_FAILED.
"""

import pytest

from content_automation.media.classification import ClassificationError, _validate_result
from content_automation.config import AUTO_ASSIGN_THRESHOLD

# Deliberately arbitrary/generic, not imported from config.CONTENT_TYPES:
# this file tests _validate_result's generic contract (pillar restricted to
# a configured set, confidence clamped, malformed responses rejected), not
# any particular pillar strategy — it must keep passing regardless of what
# pillars are currently configured in config.py.
PILLAR_KEYS = ["pillar_a", "pillar_b", "pillar_c"]


def test_valid_high_confidence_result():
    result = _validate_result(
        {"pillar": "pillar_a", "confidence": 0.94, "reason": "Discusses tool architecture."},
        PILLAR_KEYS,
    )
    assert result.pillar == "pillar_a"
    assert result.confidence == 0.94
    assert result.confidence >= AUTO_ASSIGN_THRESHOLD  # eligible for auto-assignment


def test_low_confidence_result_is_still_valid_but_below_threshold():
    result = _validate_result(
        {"pillar": "pillar_b", "confidence": 0.43, "reason": "Ambiguous, mostly a job-search story."},
        PILLAR_KEYS,
    )
    assert result.confidence < AUTO_ASSIGN_THRESHOLD  # process_content.py routes this to NEEDS_REVIEW


def test_null_pillar_is_valid():
    result = _validate_result(
        {"pillar": None, "confidence": 0.3, "reason": "Does not fit any configured pillar."},
        PILLAR_KEYS,
    )
    assert result.pillar is None


def test_classifier_cannot_emit_an_arbitrary_pillar():
    with pytest.raises(ClassificationError):
        _validate_result(
            {"pillar": "not_a_real_pillar", "confidence": 0.9, "reason": "..."},
            PILLAR_KEYS,
        )


def test_non_numeric_confidence_raises():
    with pytest.raises(ClassificationError):
        _validate_result({"pillar": "pillar_a", "confidence": "high", "reason": "..."}, PILLAR_KEYS)


def test_confidence_is_clamped_to_0_1():
    result = _validate_result(
        {"pillar": "pillar_a", "confidence": 1.5, "reason": "..."}, PILLAR_KEYS
    )
    assert result.confidence == 1.0


def test_empty_reason_raises():
    with pytest.raises(ClassificationError):
        _validate_result({"pillar": "pillar_a", "confidence": 0.9, "reason": "   "}, PILLAR_KEYS)
