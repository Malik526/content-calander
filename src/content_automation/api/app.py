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
from content_automation.config import API_CORS_ALLOWED_ORIGINS, SUPABASE_AUTH_JWKS_URL, SUPABASE_AUTH_JWT_MODE, SUPABASE_URL

# Temporary startup diagnostic (Milestone 3.6.1 deploy troubleshooting) —
# print(), not logging: verified directly that a plain logging.info() call
# here produces no visible output at all with uvicorn's default logging
# setup (no handler configured for this module's logger), which would
# make the diagnostic useless in Railway's captured logs; print() always
# reaches stdout regardless of logging config. Logs only booleans/the mode
# name, never the actual URL or any secret. Runs once, at process import
# time (this module is imported exactly once per worker process, no
# reload/multi-worker config on Railway — see railway.json), using
# whatever config.py already resolved SUPABASE_URL/SUPABASE_AUTH_JWKS_URL
# to from the process's actual environment. If "SUPABASE_URL configured"
# is False here despite Railway's dashboard showing it set, the variable
# is not reaching this process — a Railway service/environment/deploy
# issue, not a code defect (config.py's SUPABASE_URL = os.getenv(...) has
# no other source). Remove once the real deploy issue is confirmed fixed.
print(
    f"[startup config check] SUPABASE_URL configured: {bool(SUPABASE_URL)} | "
    f"SUPABASE_AUTH_JWT_MODE: {SUPABASE_AUTH_JWT_MODE} | "
    f"JWKS URL configured/derived: {bool(SUPABASE_AUTH_JWKS_URL)}",
    flush=True,  # stdout is block-buffered when not a TTY (Railway's log
    # collector attaches to a pipe, not a TTY) — verified directly that
    # without this, the line can sit in Python's internal buffer and never
    # reach the log collector at all before the process is later killed.
)

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
