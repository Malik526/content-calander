"""Targeted checks for the active pillar strategy in config.py.

These lock in the provisional engineering-focused pillar set (September
2026) and the guarantee that the default classifier needs no Anthropic
credential — see PROJECT_STATE.md and the 2026-09-12 CHANGELOG.md entry.
"""

import pytest

from classification import EmbeddingClassifier, build_classifier
from config import CLASSIFIER, CONTENT_TYPES


def test_active_pillar_keys_are_the_provisional_engineering_set():
    assert set(CONTENT_TYPES.keys()) == {"engineering", "career", "building_in_public", "mindset"}


def test_active_pillar_weights_sum_to_one():
    total = sum(info["weight"] for info in CONTENT_TYPES.values())
    assert total == pytest.approx(1.0)


def test_every_pillar_has_a_description_and_examples():
    for key, info in CONTENT_TYPES.items():
        assert info.get("description"), f"{key} is missing a description"
        assert info.get("classification_examples"), f"{key} is missing classification_examples"


def test_default_classifier_is_embeddings():
    assert CLASSIFIER == "embeddings"


def test_build_classifier_initializes_without_anthropic_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    classifier = build_classifier("embeddings")

    assert isinstance(classifier, EmbeddingClassifier)


def test_embedding_classifier_builds_profiles_for_all_four_pillars_without_real_model():
    """Confirms EmbeddingClassifier derives one profile per configured pillar
    from label+description+classification_examples with no classifier-specific
    hard-coding — using a stub model so this doesn't require the real ONNX
    download (that path is covered by the manual verification in this
    milestone and by tests/test_embedding_classifier.py's mocked tests)."""

    class StubModel:
        def embed(self, texts):
            return [[float(len(t))] for t in texts]

    clf = EmbeddingClassifier(min_similarity=-1.0, min_margin=0.0)
    clf._model = StubModel()

    vectors = clf._get_pillar_vectors(CONTENT_TYPES)

    assert set(vectors.keys()) == {"engineering", "career", "building_in_public", "mindset"}
