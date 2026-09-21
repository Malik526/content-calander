"""
dependencies/auth.py — FastAPI dependency that resolves the authenticated
caller for every protected route (Milestone 3.6).

What it does:
  get_current_user(request) extracts the `Authorization: Bearer <token>`
  header, verifies it (identity.token_verification.verify_access_token),
  resolves/creates the corresponding Pickle Batch user
  (identity.user_resolution.resolve_or_create_user), and returns the
  UserRecord. Every protected route depends on this — NEVER on a
  client-supplied user_id from a query param, header, or request body.
  This is the one place "who is calling this API" is decided; see
  docs/decisions/0011-real-authentication-and-tiktok-connection.md
  "Authentication" and hosted-product-boundary.md §5's multi-tenant
  execution invariant, which this dependency is what makes enforceable at
  the API boundary in the first place.

  Provider is currently fixed to AUTH_PROVIDER ("supabase") — the only
  identity provider this milestone wires up (Google via Supabase Auth). A
  future second provider adds a second accepted value here, not a new
  dependency.

  get_store() opens one store per request (SQLite or Postgres, selected by
  store_factory.build_content_store() exactly like every other part of
  this codebase that needs "the configured backend" — never a hardcoded
  ContentStore()) and closes it after the response is built.
"""

from fastapi import Depends, HTTPException, Request, status

from content_automation.identity.token_verification import TokenVerificationError, verify_access_token
from content_automation.identity.user_resolution import AccountConflictError, resolve_or_create_user
from content_automation.persistence.content_store import UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.persistence.store_factory import build_content_store

AUTH_PROVIDER = "supabase"


def get_store() -> ContentStoreProtocol:
    with build_content_store() as store:
        yield store


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or malformed Authorization header.")
    token = header[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or malformed Authorization header.")
    return token


def get_current_user(request: Request, store: ContentStoreProtocol = Depends(get_store)) -> UserRecord:
    """The one dependency every protected route uses. Never accepts an
    identity from anywhere but a verified bearer token — see the module
    docstring."""
    token = _extract_bearer_token(request)
    try:
        identity = verify_access_token(token)
    except TokenVerificationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    try:
        return resolve_or_create_user(store, identity, provider=AUTH_PROVIDER)
    except AccountConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
