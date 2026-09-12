"""
classification.py — ContentClassifier interface, EmbeddingClassifier (default),
and ClaudeClassifier (optional comparison/reference implementation).

What it does:
  Classifies a video transcript against the content pillars defined in
  config.CONTENT_TYPES, returning a structured, validated result. A
  classifier may only select a pillar key that exists in CONTENT_TYPES, or
  null when nothing fits confidently — and each classifier applies its own
  auto-assign policy before returning: pillar=None always means "abstain,
  route to NEEDS_REVIEW", so process_content.py never re-applies a
  classifier-specific threshold itself. See
  docs/decisions/0001-video-ingestion-pipeline.md and
  docs/decisions/0003-local-embedding-classification.md.

  ClassificationResult.confidence's meaning is classifier-dependent: for
  ClaudeClassifier it is the model's self-reported confidence (0-1); for
  EmbeddingClassifier it is the top pillar's raw cosine similarity, which is
  NOT a calibrated probability. `second_score`/`margin` are only populated by
  EmbeddingClassifier. Always check `classifier` before comparing these
  numbers across classifier types.

Dependencies:
  anthropic (ClaudeClassifier only, lazily imported)
  fastembed (EmbeddingClassifier only, lazily imported)
  config.py
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import (
    ANTHROPIC_API_KEY,
    AUTO_ASSIGN_THRESHOLD,
    CLAUDE_MODEL,
    CLASSIFIER,
    EMBEDDING_CACHE_DIR,
    EMBEDDING_MIN_MARGIN,
    EMBEDDING_MIN_SIMILARITY,
    EMBEDDING_MODEL,
)

CLASSIFY_TOOL_NAME = "classify_content"

SYSTEM_PROMPT = """\
You classify short-form video transcripts for MoreClientsCo, a growth \
systems agency, into one of its existing content pillars. Only choose a \
pillar from the provided list — never invent a new one. If the transcript \
does not clearly fit any pillar, or you are not confident, set pillar to \
null rather than guessing."""


class ClassificationError(Exception):
    """Raised when classification fails outright (not the same as abstaining).

    `reason_code` matches the failure taxonomy used by videos.failure_reason,
    mirroring media.MediaError's design. process_content.py maps this to
    status=FAILED; an abstention (pillar=None in a returned
    ClassificationResult) is a normal outcome, not this exception.
    """

    def __init__(self, message: str, reason_code: str = "CLASSIFICATION_FAILED"):
        super().__init__(message)
        self.reason_code = reason_code


class UnsupportedClassifierError(ClassificationError):
    def __init__(self, classifier_name: str):
        super().__init__(
            f"Unsupported CLASSIFIER={classifier_name!r}. Valid values: embeddings, claude",
            reason_code="INVALID_PILLAR_CONFIGURATION",
        )


@dataclass
class ClassificationResult:
    pillar: str | None
    confidence: float
    reason: str
    classifier: str = "unknown"
    second_score: float | None = None
    margin: float | None = None


class ContentClassifier(ABC):
    @abstractmethod
    def classify(self, transcript: str, pillars: dict[str, dict]) -> ClassificationResult:
        ...


# ---------------------------------------------------------------------------
# Claude (optional comparison/reference implementation)
# ---------------------------------------------------------------------------

class ClaudeClassifier(ContentClassifier):
    def __init__(
        self,
        api_key: str = ANTHROPIC_API_KEY,
        model: str = CLAUDE_MODEL,
        auto_assign_threshold: float = AUTO_ASSIGN_THRESHOLD,
    ):
        self.api_key = api_key
        self.model = model
        self.auto_assign_threshold = auto_assign_threshold
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ClassificationError(
                    "anthropic is not installed. Run: pip install anthropic"
                ) from exc
            if not self.api_key:
                raise ClassificationError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env."
                )
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def classify(self, transcript: str, pillars: dict[str, dict]) -> ClassificationResult:
        client = self._get_client()
        pillar_keys = list(pillars.keys())
        tool = _build_tool_schema(pillar_keys)
        pillar_summary = "\n".join(
            f"- {key}: {info['label']} — {info['description']}"
            for key, info in pillars.items()
        )
        user_message = (
            f"Content pillars:\n{pillar_summary}\n\n"
            f"Transcript:\n{transcript.strip() or '(empty — no speech detected)'}"
        )

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[tool],
                tool_choice={"type": "tool", "name": CLASSIFY_TOOL_NAME},
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            raise ClassificationError(f"Claude classification request failed: {exc}") from exc

        tool_use = next(
            (block for block in response.content if getattr(block, "type", None) == "tool_use"),
            None,
        )
        if tool_use is None:
            raise ClassificationError("Claude response did not include a tool_use block")

        result = _validate_result(tool_use.input, pillar_keys)

        # ClaudeClassifier owns its own auto-assign policy: below threshold,
        # abstain (pillar=None) rather than letting the caller re-apply a
        # threshold it may not know is Claude-specific.
        if result.pillar is not None and result.confidence < self.auto_assign_threshold:
            return ClassificationResult(
                pillar=None,
                confidence=result.confidence,
                reason=f"{result.reason} (below auto-assign threshold {self.auto_assign_threshold}.)",
                classifier="claude",
            )
        return ClassificationResult(pillar=result.pillar, confidence=result.confidence, reason=result.reason, classifier="claude")


def _build_tool_schema(pillar_keys: list[str]) -> dict:
    return {
        "name": CLASSIFY_TOOL_NAME,
        "description": "Record the classification of a video transcript into a content pillar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pillar": {
                    "type": ["string", "null"],
                    "enum": [*pillar_keys, None],
                    "description": "The matching pillar key, or null if none fit confidently.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Confidence in the chosen pillar (or in the null decision), 0 to 1.",
                },
                "reason": {
                    "type": "string",
                    "description": "One or two sentence justification.",
                },
            },
            "required": ["pillar", "confidence", "reason"],
        },
    }


def _validate_result(raw: dict, pillar_keys: list[str]) -> ClassificationResult:
    pillar = raw.get("pillar")
    if pillar is not None and pillar not in pillar_keys:
        raise ClassificationError(f"Claude returned an unknown pillar key: {pillar!r}")

    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ClassificationError(f"Claude returned a non-numeric confidence: {raw.get('confidence')!r}") from exc
    confidence = max(0.0, min(1.0, confidence))

    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise ClassificationError("Claude returned an empty reason")

    return ClassificationResult(pillar=pillar, confidence=confidence, reason=reason)


# ---------------------------------------------------------------------------
# Embeddings (default, fully local)
# ---------------------------------------------------------------------------

def _gate_decision(
    top_key: str,
    top_score: float,
    second_score: float,
    min_similarity: float,
    min_margin: float,
    label: str,
) -> tuple[str | None, str]:
    """Shared two-gate policy, factored out so evaluate_classifier.py's
    threshold sweep can re-apply it to cached scores without re-embedding."""
    margin = top_score - second_score
    if top_score < min_similarity:
        return None, f"Below minimum similarity ({top_score:.3f} < {min_similarity})."
    if margin < min_margin:
        return None, (
            f"Too ambiguous: top two pillars within {margin:.3f} "
            f"(min margin {min_margin}, top pillar {label!r})."
        )
    return top_key, f"Highest semantic similarity to {label} (margin {margin:.3f} over next best)."


def _cosine_similarity(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _pillar_profile_text(info: dict) -> str:
    """label + description + classification_examples, combined into one text
    per pillar before embedding (see docs/decisions/0003-...: a single
    combined embedding per pillar, not an ensemble of example embeddings)."""
    parts = [str(info.get("label", "")), str(info.get("description", ""))]
    parts.extend(str(example) for example in info.get("classification_examples") or [])
    return "\n".join(part for part in parts if part)


class EmbeddingClassifier(ContentClassifier):
    """Local, fully offline classification via sentence embeddings + cosine
    similarity. No API key, no network call once the model is cached.

    The embedding model is loaded once and pillar embeddings are computed
    once per distinct set of configured pillars, both cached on the instance
    — reuse one EmbeddingClassifier across a whole process_content.py run
    (this is the existing lazy-load pattern also used by
    FasterWhisperTranscriber/ClaudeClassifier, not a new mechanism).
    """

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        min_similarity: float = EMBEDDING_MIN_SIMILARITY,
        min_margin: float = EMBEDDING_MIN_MARGIN,
        cache_dir=EMBEDDING_CACHE_DIR,
    ):
        if not (-1.0 <= min_similarity <= 1.0):
            raise ClassificationError(
                f"EMBEDDING_MIN_SIMILARITY must be between -1 and 1 (got {min_similarity})",
                reason_code="INVALID_PILLAR_CONFIGURATION",
            )
        if not (0.0 <= min_margin <= 2.0):
            raise ClassificationError(
                f"EMBEDDING_MIN_MARGIN must be between 0 and 2 (got {min_margin})",
                reason_code="INVALID_PILLAR_CONFIGURATION",
            )
        self.model_name = model_name
        self.min_similarity = min_similarity
        self.min_margin = min_margin
        self.cache_dir = cache_dir
        self._model = None
        self._pillar_vectors: dict[str, object] | None = None
        self._pillar_signature: tuple[str, ...] | None = None

    def _get_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise ClassificationError(
                    "fastembed is not installed. Run: pip install fastembed",
                    reason_code="MODEL_LOAD_FAILED",
                ) from exc
            try:
                import os
                os.makedirs(self.cache_dir, exist_ok=True)
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(self.cache_dir))
            except Exception as exc:
                raise ClassificationError(
                    f"Could not load embedding model {self.model_name!r}: {exc}",
                    reason_code="MODEL_LOAD_FAILED",
                ) from exc
        return self._model

    def _get_pillar_vectors(self, pillars: dict[str, dict]) -> dict[str, object]:
        if not pillars:
            raise ClassificationError("CONTENT_TYPES has no configured pillars", reason_code="INVALID_PILLAR_CONFIGURATION")
        for key, info in pillars.items():
            if not info.get("description"):
                raise ClassificationError(
                    f"Pillar '{key}' is missing a description, required for embedding classification",
                    reason_code="INVALID_PILLAR_CONFIGURATION",
                )

        signature = tuple(pillars.keys())
        if self._pillar_vectors is None or self._pillar_signature != signature:
            model = self._get_model()
            texts = [_pillar_profile_text(info) for info in pillars.values()]
            try:
                vectors = list(model.embed(texts))
            except Exception as exc:
                raise ClassificationError(f"Could not embed pillar profiles: {exc}", reason_code="EMBEDDING_FAILED") from exc
            self._pillar_vectors = dict(zip(pillars.keys(), vectors))
            self._pillar_signature = signature
        return self._pillar_vectors

    def score(self, transcript: str, pillars: dict[str, dict]) -> list[tuple[str, float]]:
        """Return (pillar_key, cosine_similarity) pairs, sorted descending.

        Returns [] for an empty/whitespace-only transcript rather than
        raising — that is a legitimate "nothing to classify" case, not a
        failure. Exposed separately from classify() so evaluate_classifier.py
        can compute scores once per transcript and sweep multiple threshold
        combinations against the cached scores without re-embedding.
        """
        text = (transcript or "").strip()
        pillar_vectors = self._get_pillar_vectors(pillars)  # validates config even for an empty transcript
        if not text:
            return []

        model = self._get_model()
        try:
            transcript_vector = next(iter(model.embed([text])))
        except Exception as exc:
            raise ClassificationError(f"Could not embed transcript: {exc}", reason_code="EMBEDDING_FAILED") from exc

        scored = [(key, _cosine_similarity(transcript_vector, vector)) for key, vector in pillar_vectors.items()]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def classify(self, transcript: str, pillars: dict[str, dict]) -> ClassificationResult:
        scored = self.score(transcript, pillars)
        if not scored:
            return ClassificationResult(
                pillar=None, confidence=0.0, reason="Empty transcript: no speech detected to classify.",
                classifier="embeddings",
            )

        top_key, top_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else -1.0
        pillar, reason = _gate_decision(
            top_key, top_score, second_score, self.min_similarity, self.min_margin, pillars[top_key]["label"],
        )
        return ClassificationResult(
            pillar=pillar,
            confidence=top_score,
            reason=reason,
            classifier="embeddings",
            second_score=second_score,
            margin=top_score - second_score,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_classifier(classifier_name: str = CLASSIFIER) -> ContentClassifier:
    """Construct the configured ContentClassifier. process_content.py should
    call this instead of importing a concrete classifier class directly."""
    if classifier_name == "embeddings":
        return EmbeddingClassifier()
    if classifier_name == "claude":
        return ClaudeClassifier()
    raise UnsupportedClassifierError(classifier_name)
