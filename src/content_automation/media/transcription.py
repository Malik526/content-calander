"""
transcription.py — Transcriber interface and local faster-whisper implementation.

What it does:
  Defines a vendor-neutral Transcriber interface so process_content.py never
  depends on a specific speech-to-text engine. V1 uses faster-whisper running
  locally (see docs/decisions/0001-video-ingestion-pipeline.md for why: this
  is a batch desktop tool, not a live UI, so there is no latency pressure and
  local transcription avoids a per-minute API bill and an extra vendor
  credential).

Dependencies:
  faster-whisper (imported lazily so importing this module — or running any
  part of process_content.py that doesn't reach transcription — never
  requires the model download).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from content_automation.config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL_SIZE


class TranscriptionError(Exception):
    """Raised when transcription fails; caller maps this to TRANSCRIPTION_FAILED."""


@dataclass
class TranscriptResult:
    text: str
    language: str
    duration_seconds: float


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptResult:
        ...


class FasterWhisperTranscriber(Transcriber):
    """Local transcription via faster-whisper (CTranslate2-backed Whisper)."""

    def __init__(
        self,
        model_size: str = WHISPER_MODEL_SIZE,
        device: str = WHISPER_DEVICE,
        compute_type: str = WHISPER_COMPUTE_TYPE,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(
                    "faster-whisper is not installed. Run: pip install faster-whisper"
                ) from exc
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        model = self._get_model()
        try:
            segments, info = model.transcribe(str(audio_path))
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise TranscriptionError(f"faster-whisper failed on {audio_path}: {exc}") from exc

        return TranscriptResult(
            text=text,
            language=info.language or "unknown",
            duration_seconds=float(info.duration or 0.0),
        )
