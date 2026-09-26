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

import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from content_automation.api.routes import me, platforms_tiktok
from content_automation.config import API_CORS_ALLOWED_ORIGINS, SUPABASE_AUTH_JWKS_URL, SUPABASE_AUTH_JWT_MODE, SUPABASE_URL

# Milestone 3.6 security review: uvicorn's own access logger ("uvicorn.access",
# enabled by default whenever uvicorn.run() is called without access_log=False —
# see cli/run_api.py) logs every request's raw path *including its query
# string*. For GET /api/platforms/tiktok/callback?code=...&state=..., that
# means the one-time TikTok authorization code and OAuth state land in
# Railway's captured stdout on every real connection — not because any of
# our own logging/print statements put them there (grepped: none do), but
# because uvicorn.logging.AccessFormatter.formatMessage() always includes
# the full request target. This redacts just those two query parameters,
# only on that one path, in this one access-log line — every other request
# (and every other logger) is untouched. It fails open (leaves the record
# alone) rather than raising, so a future uvicorn internals change can
# never turn a logging filter into a request-handling failure.
_OAUTH_CALLBACK_PATH = "/api/platforms/tiktok/callback"
_SENSITIVE_QUERY_PARAMS_PATTERN = re.compile(r"\b(code|state)=[^&\s]+")


class _RedactOAuthCallbackQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            client_addr, method, full_path, http_version, status_code = record.args  # type: ignore[misc]
        except (TypeError, ValueError):
            return True
        if isinstance(full_path, str) and full_path.startswith(_OAUTH_CALLBACK_PATH) and "?" in full_path:
            path, _, query = full_path.partition("?")
            redacted_query = _SENSITIVE_QUERY_PARAMS_PATTERN.sub(r"\1=[redacted]", query)
            record.args = (client_addr, method, f"{path}?{redacted_query}", http_version, status_code)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactOAuthCallbackQueryFilter())

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
