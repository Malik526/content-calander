"""
transcript_metrics.py — WEBVTT normalization and ASR accuracy metrics.

What it does:
  Pure, dependency-free text functions used by evaluate_transcription.py to
  compare a faster-whisper prediction against a dataset-supplied WEBVTT
  reference transcript: strip WEBVTT structure down to spoken text, normalize
  text for scoring, and compute word/character error rate via a local
  Levenshtein implementation (no ML framework, no external WER package).

  No I/O, no network, no imports beyond the standard library — safe to unit
  test directly with hand-written strings.

Milestone 1.3.1 (Shofo real-video evaluation corpus). See
evaluation/video_pipeline/README.md for how this fits into the pipeline test.
"""

import re

_TAG_RE = re.compile(r"<[^>]*>")
_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_TIMESTAMP_MARKER = "-->"


def strip_webvtt(text: str) -> str:
    """Extract spoken text from a WEBVTT transcript.

    Discards the leading "WEBVTT" header, cue identifiers, timestamp lines,
    NOTE/STYLE blocks, and inline tags (e.g. <v Speaker>, <00:00:01.000>),
    keeping only the cue text, joined with single spaces in cue order.

    Safe to call on plain (non-WEBVTT) text too: with no "-->" timestamp
    lines present, every block is skipped and "" is returned — callers
    should not feed already-plain reference text through this function.
    """
    if not text or not text.strip():
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        lines = lines[1:]

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    spoken: list[str] = []
    for block in blocks:
        first = block[0].strip().upper()
        if first.startswith("NOTE") or first.startswith("STYLE"):
            continue

        timestamp_idx = next((i for i, line in enumerate(block) if _TIMESTAMP_MARKER in line), None)
        if timestamp_idx is None:
            # No timestamp line in this block (e.g. a stray identifier or a
            # malformed fragment) — nothing reliably "spoken" to extract.
            continue

        for line in block[timestamp_idx + 1:]:
            cleaned = _TAG_RE.sub("", line).strip()
            if cleaned:
                spoken.append(cleaned)

    return " ".join(spoken)


def normalize_for_wer(text: str) -> str:
    """Lowercase, drop punctuation (keeping apostrophes and word chars),
    and collapse whitespace. Used identically for WER and CER so both
    metrics compare on the same normalized surface form."""
    if not text:
        return ""
    lowered = text.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()


def _levenshtein(a: list, b: list) -> int:
    """Classic O(len(a)*len(b)) edit distance, O(len(b)) memory."""
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous_row = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current_row = [i] + [0] * len(b)
        for j, item_b in enumerate(b, start=1):
            cost = 0 if item_a == item_b else 1
            current_row[j] = min(
                previous_row[j] + 1,      # deletion
                current_row[j - 1] + 1,   # insertion
                previous_row[j - 1] + cost,  # substitution
            )
        previous_row = current_row
    return previous_row[-1]


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    """Word-level WER = word edit distance / reference word count, both
    normalized first. Returns None when the reference has no words at all —
    WER is undefined there, not 0.0 (perfect) or 1.0 (worst-case); callers
    should treat None as "not scoreable" and exclude it from aggregates."""
    ref_words = normalize_for_wer(reference).split()
    hyp_words = normalize_for_wer(hypothesis).split()
    if not ref_words:
        return None
    return _levenshtein(ref_words, hyp_words) / len(ref_words)


def character_error_rate(reference: str, hypothesis: str) -> float | None:
    """Character-level CER, same normalization and None-when-empty rule as
    word_error_rate. Whitespace is dropped before comparing characters."""
    ref_chars = list(normalize_for_wer(reference).replace(" ", ""))
    hyp_chars = list(normalize_for_wer(hypothesis).replace(" ", ""))
    if not ref_chars:
        return None
    return _levenshtein(ref_chars, hyp_chars) / len(ref_chars)
