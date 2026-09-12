# Evaluation Dataset

This directory holds your private, real-transcript golden dataset for `evaluate_classifier.py`. Its contents (`labels.csv`, `transcripts/*.txt`) are gitignored — this README is the only file meant to be committed here.

## Format

```
evaluation/
    labels.csv
    transcripts/
        video_001.txt
        video_002.txt
        ...
```

`labels.csv`:

```csv
video_id,true_pillar
video_001,building
video_002,acquisition
video_003,
```

- `video_id` must match a file `transcripts/<video_id>.txt`.
- `true_pillar` must be one of the pillar keys in `config.CONTENT_TYPES` (e.g. `building`, `acquisition`, `execution`, `mindset`), or blank / `none` for a transcript that should not match any configured pillar.

## Building it

Cache transcripts once via `faster-whisper` (not by hand) so classifier benchmarking is isolated from transcription quality — for example, run `process_content.py` over your real videos once, then export each `videos.transcript` into `transcripts/<video_id>.txt`. Transcription is not the thing being evaluated here; keep it constant across every classifier run.

Target ~20-40 real labeled videos (see `docs/decisions/0003-local-embedding-classification.md`): obvious pillar examples, overlapping/ambiguous ones, at least a few that don't fit any pillar, and a range of transcript lengths and phrasing — not artificial textbook examples.

## Running

```bash
python3 evaluate_classifier.py --classifier embeddings
python3 evaluate_classifier.py --classifier claude
python3 evaluate_classifier.py --classifier embeddings --sweep
```

This never touches `data/content.db` or claims a `content_slot` — it only reads `labels.csv`/`transcripts/` and prints a report.
