"""
app.py — the FastAPI application (Milestone 3.6).

What it does:
  Wires together only what this milestone actually needs: /api/me and the
  TikTok connection flow (/api/platforms/tiktok/*). No generic CRUD, no
  batch-upload/scheduling/queue endpoints — those are explicitly future
  milestones' scope (see AGENTS.md "Scope Guardrails").

  CORS is an explicit allowlist (config.API_CORS_ALLOWED_ORIGINS), never
  "*" — this API verifies identity on every protected route, and a
  wildcard origin combined with credentialed requests would defeat that.

Run:
  python3 cli/run_api.py
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from content_automation.api.routes import me, platforms_tiktok
from content_automation.config import API_CORS_ALLOWED_ORIGINS

app = FastAPI(title="Pickle Batch API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(me.router, prefix="/api")
app.include_router(platforms_tiktok.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    """Unauthenticated liveness check only — returns no identity or
    connection information. Useful for the hosting platform's own health
    probe; not a product endpoint."""
    return {"status": "ok"}
