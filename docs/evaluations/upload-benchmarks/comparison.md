# Comparison — Iteration 1 vs. Iteration 2

| Metric | Iteration 1 | Iteration 2 | Delta | Observation |
|---|---|---|---|---|
| File size vs. duration (per file) | _pending_ | _pending_ | _pending_ | |
| Per-file upload duration (`duration_ms`) | _pending_ | _pending_ | _pending_ | |
| Total batch duration (`total_duration_ms`) | _pending_ | _pending_ | _pending_ | |
| Derived throughput (bytes/sec) | _pending_ | _pending_ | _pending_ | |
| Storage behavior (provider/key correctness) | _pending_ | _pending_ | _pending_ | |
| DB schema correctness (fields null/populated as expected) | _pending_ | _pending_ | _pending_ | |
| Duplicate/re-upload behavior | N/A (no re-upload attempted yet) | _pending_ | _pending_ | |
| Navigation-safe upload behavior | _pending_ | _pending_ | _pending_ | |
| Library results | _pending_ | _pending_ | _pending_ | |

**Whether optimization is justified:** _pending_ — fill in once both iterations are
captured. As a baseline expectation going in: with only two small files and no
architectural change to the upload path itself in this pass (Part A only changed
`file_hash`'s uniqueness semantics, not the upload/storage/DB write sequence), Iteration
2's timings should land close to Iteration 1's — a large, unexplained delta in either
direction would itself be the interesting finding, not the absolute numbers.
