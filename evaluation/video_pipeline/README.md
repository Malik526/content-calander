# Shofo Real-Video Evaluation Corpus

A small, local, reproducible real-video test fixture pulled from the
[Shofo/shofo-talking-head-en](https://huggingface.co/datasets/Shofo/shofo-talking-head-en)
Hugging Face dataset (~10,000 English talking-head clips, ~104GB total). Its
contents (`metadata.jsonl`, `videos/*.mp4`, `results.jsonl`) are gitignored —
this README is the only file meant to be committed here. See `PROJECT_STATE.md`
and the root `README.md` ("Shofo Real-Video Evaluation Corpus") for how this
fits into the pipeline.

## What this corpus is for

Exercising the real pipeline against real short-form social video:

```
video download → ffprobe/media inspection → audio extraction →
faster-whisper transcription → transcript comparison →
transcript-derived caption → FIFO assignment → Google Calendar scheduling
```

**Not** for evaluating pillar-classification accuracy — the topics are
arbitrary social talking-head content unrelated to this project's
engineering/career pillars (`config.CONTENT_TYPES`). Use `evaluation/` (the
sibling directory, not this one) with `evaluate_classifier.py` for that.

## Dataset access

The dataset is gated. Before running `download_shofo_samples.py`:

1. Visit the dataset page while logged into Hugging Face and accept its
   access conditions.
2. Authenticate locally: `huggingface-cli login`, or set the `HF_TOKEN`
   environment variable to a valid access token. Never put a token in
   source code or commit it anywhere.

Install the acquisition/evaluation-only dependencies first (kept out of the
production `requirements.txt` on purpose):

```bash
pip install -r ../../requirements-eval.txt
```

## Building it

```bash
python3 download_shofo_samples.py --count 12
python3 download_shofo_samples.py --count 12 --output evaluation/video_pipeline --seed 42
```

This does **not** download the full dataset. It streams dataset metadata
only (the `video` feature column is dropped before iterating, so nothing is
decoded), filters candidates (`has_audio=True`, `language=en`, a real
`file_name`, a non-empty `transcript`), stratifies a sample across
short/medium/long duration and `has_music`, and downloads only the selected
rows' raw MP4s via `huggingface_hub.hf_hub_download(file_name)` — the exact
original file, no transcoding. `--seed` (default 42) makes the selection
reproducible across runs against the same dataset revision.

Output:

```
evaluation/video_pipeline/
    metadata.jsonl       # one JSON record per downloaded clip (reference metadata)
    videos/<video_id>.mp4
```

`metadata.jsonl`'s `reference_transcript` field is the dataset's own
segment-level WEBVTT transcript, produced by another ASR model
(NVIDIA Canary-Qwen-2.5B per the dataset card) — **a reference, not
ground truth**. It may contain its own errors; treat mismatches against
faster-whisper as differences to investigate, not automatic faster-whisper
failures.

## Evaluating transcription

```bash
python3 evaluate_transcription.py
python3 evaluate_transcription.py --limit 3
```

Runs the real, production `transcription.FasterWhisperTranscriber` (no
second Whisper implementation) against each downloaded clip, normalizes the
WEBVTT reference down to plain text, and reports word/character error rate
plus `realtime_factor` (transcription time / video duration — useful for
judging whether local faster-whisper is practical for real workflows).
Writes `evaluation/video_pipeline/results.jsonl` (gitignored).

## FIFO pipeline test

Copy a known subset (recommended 5) into `content/incoming/` in a known
numbered order (e.g. `01_<video_id>.mp4` .. `05_<video_id>.mp4`) so expected
FIFO ordering is obvious on manual inspection, then run
`process_content.py` as usual (see the root `README.md`). Production
ordering still relies on `videos.created_at`/discovery order, not filename
numbering — the numbering here is only to make a manual test easy to read.

## Non-goals

TikTok publishing, classifier calibration, pillar-accuracy scoring against
this corpus, full dataset download, video transcoding, permanent dataset
storage. See the root `README.md` for the full list.
