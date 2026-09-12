"""Unit tests for classification.EmbeddingClassifier, with the embedding
model mocked so tests don't require downloading a real ONNX model.

A separate real-model smoke test lives in
test_embedding_classifier_real_model.py (skipped by default — see that file).
"""

import pytest

from classification import ClassificationError, EmbeddingClassifier, UnsupportedClassifierError, build_classifier

PILLARS = {
    "building": {"label": "Building Systems & Tools", "description": "Building tools and automation.", "classification_examples": ["Explaining an API integration."]},
    "acquisition": {"label": "Customer Acquisition in Action", "description": "Cold outreach and sales.", "classification_examples": ["Discussing a cold call."]},
    "mindset": {"label": "Mindset & Discipline", "description": "Personal discipline and reflection.", "classification_examples": ["Reflecting on a setback."]},
}

# Fixed unit vectors in a 3-dim toy space so cosine similarity is exact and
# easy to reason about: transcript vector is compared against each pillar's
# "vector" by _pillar_profile_text -> a fake embed() that maps text to one of
# these based on which pillar text it came from.
VECTORS = {
    "building": [1.0, 0.0, 0.0],
    "acquisition": [0.0, 1.0, 0.0],
    "mindset": [0.0, 0.0, 1.0],
}


class FakeModel:
    """Mimics fastembed.TextEmbedding.embed(): takes a list of strings,
    returns a list of vectors. Vector chosen by matching a marker substring,
    with an explicit override for known transcript texts."""

    def __init__(self, transcript_vectors=None):
        self.transcript_vectors = transcript_vectors or {}
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for text in texts:
            if text in self.transcript_vectors:
                out.append(self.transcript_vectors[text])
                continue
            matched = next((vec for key, vec in VECTORS.items() if key in text.lower()), [0.5, 0.5, 0.5])
            out.append(matched)
        return out


def _classifier_with_fake_model(fake_model, **kwargs):
    clf = EmbeddingClassifier(min_similarity=kwargs.pop("min_similarity", 0.3), min_margin=kwargs.pop("min_margin", 0.05), **kwargs)
    clf._model = fake_model  # bypass real model loading
    return clf


def test_valid_single_pillar_classification():
    transcript = "I built a new automation for the pipeline."
    fake_model = FakeModel(transcript_vectors={transcript: [0.9, 0.1, 0.0]})
    clf = _classifier_with_fake_model(fake_model)

    result = clf.classify(transcript, PILLARS)

    assert result.pillar == "building"
    assert result.classifier == "embeddings"


def test_result_restricted_to_configured_pillars():
    transcript = "some transcript"
    fake_model = FakeModel(transcript_vectors={transcript: [0.9, 0.1, 0.0]})
    clf = _classifier_with_fake_model(fake_model)

    result = clf.classify(transcript, PILLARS)

    assert result.pillar in PILLARS or result.pillar is None


def test_score_ordering_is_descending():
    transcript = "some transcript"
    fake_model = FakeModel(transcript_vectors={transcript: [0.9, 0.4, 0.1]})
    clf = _classifier_with_fake_model(fake_model)

    scored = clf.score(transcript, PILLARS)

    scores = [s for _, s in scored]
    assert scores == sorted(scores, reverse=True)


def test_low_similarity_triggers_review():
    transcript = "completely unrelated content about a vacation"
    # Negatively correlated with every pillar axis -> low (negative) cosine
    # similarity to all three, well below the 0.5 gate.
    fake_model = FakeModel(transcript_vectors={transcript: [-0.9, -0.9, -0.9]})
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.5, min_margin=0.0)

    result = clf.classify(transcript, PILLARS)

    assert result.pillar is None
    assert "similarity" in result.reason.lower()


def test_small_margin_triggers_review():
    # Nearly equidistant between building and acquisition.
    transcript = "ambiguous content"
    fake_model = FakeModel(transcript_vectors={transcript: [0.71, 0.70, 0.0]})
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.3, min_margin=0.05)

    result = clf.classify(transcript, PILLARS)

    assert result.pillar is None
    assert "ambiguous" in result.reason.lower()
    assert result.margin is not None and result.margin < 0.05


def test_high_score_and_margin_gets_assigned():
    transcript = "clearly about building a tool"
    fake_model = FakeModel(transcript_vectors={transcript: [1.0, 0.0, 0.0]})
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.5, min_margin=0.1)

    result = clf.classify(transcript, PILLARS)

    assert result.pillar == "building"
    assert result.margin == pytest.approx(1.0)


def test_empty_transcript_returns_review_without_calling_model_embed_on_transcript():
    fake_model = FakeModel()
    clf = _classifier_with_fake_model(fake_model)

    result = clf.classify("   ", PILLARS)

    assert result.pillar is None
    assert result.classifier == "embeddings"
    assert result.confidence == 0.0


def test_one_configured_pillar():
    transcript = "anything"
    fake_model = FakeModel(transcript_vectors={transcript: [1.0, 0.0, 0.0]})
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.1, min_margin=0.0)

    result = clf.classify(transcript, {"building": PILLARS["building"]})

    assert result.pillar == "building"
    assert result.second_score == -1.0  # no second pillar to compare against


def test_arbitrary_number_of_pillars():
    many_pillars = {f"pillar_{i}": {"label": f"Pillar {i}", "description": "x"} for i in range(6)}
    fake_model = FakeModel(transcript_vectors={"t": [1.0] * 6})
    fake_model.embed = lambda texts: [[float(i == 2) for i in range(6)] if "Pillar 2" in t else [0.1] * 6 for t in texts]
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.0, min_margin=0.0)

    result = clf.classify("t", many_pillars)

    assert result.pillar in many_pillars


def test_pillar_vectors_are_cached_across_calls():
    fake_model = FakeModel()
    clf = _classifier_with_fake_model(fake_model, min_similarity=0.0, min_margin=0.0)

    clf.classify("first transcript", PILLARS)
    calls_after_first = fake_model.calls
    clf.classify("second transcript", PILLARS)

    # Pillar embeddings computed once (first call: pillars + transcript = 2
    # embed() calls); second call should only embed the new transcript.
    assert fake_model.calls == calls_after_first + 1


def test_missing_description_raises_invalid_pillar_configuration():
    fake_model = FakeModel()
    clf = _classifier_with_fake_model(fake_model)
    bad_pillars = {"building": {"label": "Building"}}  # no description

    with pytest.raises(ClassificationError) as excinfo:
        clf.classify("some transcript", bad_pillars)
    assert excinfo.value.reason_code == "INVALID_PILLAR_CONFIGURATION"


def test_empty_pillar_config_raises():
    fake_model = FakeModel()
    clf = _classifier_with_fake_model(fake_model)

    with pytest.raises(ClassificationError):
        clf.classify("some transcript", {})


def test_invalid_min_similarity_rejected():
    with pytest.raises(ClassificationError):
        EmbeddingClassifier(min_similarity=1.5)


def test_invalid_min_margin_rejected():
    with pytest.raises(ClassificationError):
        EmbeddingClassifier(min_margin=-0.1)


# ---------------------------------------------------------------------------
# Classifier selection (config.CLASSIFIER / build_classifier)
# ---------------------------------------------------------------------------

def test_embeddings_selected_by_default():
    clf = build_classifier("embeddings")
    assert isinstance(clf, EmbeddingClassifier)


def test_claude_selectable():
    from classification import ClaudeClassifier
    clf = build_classifier("claude")
    assert isinstance(clf, ClaudeClassifier)


def test_unknown_classifier_rejected():
    with pytest.raises(UnsupportedClassifierError):
        build_classifier("foo")
