"""api — the first real FastAPI surface for Pickle Batch (Milestone 3.6).

See docs/architecture/hosted-product-boundary.md §12 for the planned
location this fills, and §4 for which operations belong here (fast,
synchronous reads/writes and connection flows) vs. behind a background job
(not this package — see scheduling/, media/).
"""
