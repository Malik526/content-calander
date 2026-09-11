"""
classification.py — ContentClassifier interface and Claude implementation.

What it does:
  Classifies a video transcript against the content pillars defined in
  config.CONTENT_TYPES, returning a structured, validated result. The
  classifier may only select a pillar key that exists in CONTENT_TYPES, or
  null when nothing fits confidently (see docs/decisions/0001-video-ingestion-pipeline.md).

  Uses Claude via forced tool-use so the model output is a validated
  structured object rather than parsed prose. This mirrors the existing
  Anthropic integration pattern in
  internal-tools/content-analytics/analysis/claude_analysis.py.

Dependencies:
  anthropic
  config.py for ANTHROPIC_API_KEY and CLAUDE_MODEL
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

CLASSIFY_TOOL_NAME = "classify_content"

SYSTEM_PROMPT = """\
You classify short-form video transcripts for MoreClientsCo, a growth \
systems agency, into one of its existing content pillars. Only choose a \
pillar from the provided list — never invent a new one. If the transcript \
does not clearly fit any pillar, or you are not confident, set pillar to \
null rather than guessing."""


class ClassificationError(Exception):
    """Raised when classification fails; caller maps this to CLASSIFICATION_FAILED."""


@dataclass
class ClassificationResult:
    pillar: str | None
    confidence: float
    reason: str


class ContentClassifier(ABC):
    @abstractmethod
    def classify(self, transcript: str, pillars: dict[str, dict]) -> ClassificationResult:
        ...


class ClaudeClassifier(ContentClassifier):
    def __init__(self, api_key: str = ANTHROPIC_API_KEY, model: str = CLAUDE_MODEL):
        self.api_key = api_key
        self.model = model
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

        return _validate_result(tool_use.input, pillar_keys)


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
