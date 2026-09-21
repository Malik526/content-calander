"""
token_verification.py — verifies a Supabase Auth access token presented by
the frontend (Milestone 3.6) and extracts the caller's identity.

What it does:
  Answers exactly one question: "is this a genuine, unexpired token
  Supabase issued, and who is it for." Never "log this user in" (see
  user_resolution.py) and never anything about local sessions/cookies —
  every request carries its own bearer token, verified independently.

  Two independent verification strategies, selected by
  config.SUPABASE_AUTH_JWT_MODE (see config.py's own comment for the
  tradeoff):
    "jwks"  (default, preferred) — Supabase's asymmetric signing keys.
             No shared secret needed server-side; the public JWKS is
             fetched from config.SUPABASE_AUTH_JWKS_URL via PyJWKClient,
             which caches fetched keys in-process (by "kid") so this does
             not do a network round trip on every request.
    "hs256" — Supabase's legacy shared JWT secret
             (config.SUPABASE_JWT_SECRET) — server-side only, never
             logged, never exposed client-side.

  Every failure mode (expired, wrong signature, wrong audience, malformed,
  missing 'sub' claim, misconfigured server) raises TokenVerificationError
  — there is no partially-trusted result and no fallback identity. The one
  caller that matters, api/dependencies/auth.py's get_current_user_id, must
  translate this into HTTP 401 and never accept any other source of
  identity (see that module's own docstring for the "never trust a
  client-supplied user_id" invariant this exists to enforce).

Dependencies:
  PyJWT[crypto] (RS256/ES256 + HS256 verification, JWKS client).
  content_automation.config (SUPABASE_AUTH_JWT_MODE/JWKS_URL/AUDIENCE,
  SUPABASE_JWT_SECRET).
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from content_automation.config import (
    SUPABASE_AUTH_AUDIENCE,
    SUPABASE_AUTH_JWKS_URL,
    SUPABASE_AUTH_JWT_MODE,
    SUPABASE_JWT_SECRET,
)


class TokenVerificationError(Exception):
    """The presented token is missing, malformed, expired, or fails
    signature/audience verification, or the server itself is missing
    required configuration for the selected mode. Always means "reject
    this request" — never a partial/degraded trust result."""


@dataclass
class VerifiedIdentity:
    """The one thing a verified token proves: which Supabase Auth subject
    made this request, and (if present) the email Supabase has on file for
    them. `raw_claims` is kept for observability/debugging only — callers
    should read `subject`/`email`, not reach into raw_claims for anything
    that matters, so a future claims-shape change doesn't ripple silently
    through call sites."""

    subject: str
    email: str | None
    raw_claims: dict[str, Any] = field(repr=False)


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    if not SUPABASE_AUTH_JWKS_URL:
        raise TokenVerificationError(
            "SUPABASE_AUTH_JWT_MODE=jwks but no JWKS URL is configured. Set SUPABASE_URL "
            "(the JWKS URL is derived from it), or set SUPABASE_AUTH_JWKS_URL explicitly, in .env."
        )
    return PyJWKClient(SUPABASE_AUTH_JWKS_URL)


def _require_config_for_mode() -> None:
    if SUPABASE_AUTH_JWT_MODE not in ("jwks", "hs256"):
        raise TokenVerificationError(
            f"Unknown SUPABASE_AUTH_JWT_MODE={SUPABASE_AUTH_JWT_MODE!r} — must be 'jwks' or 'hs256'."
        )
    if SUPABASE_AUTH_JWT_MODE == "hs256" and not SUPABASE_JWT_SECRET:
        raise TokenVerificationError("SUPABASE_AUTH_JWT_MODE=hs256 but SUPABASE_JWT_SECRET is not set.")


def verify_access_token(token: str) -> VerifiedIdentity:
    """Verify a bearer token's signature, expiry, and audience, and return
    the caller's identity. Raises TokenVerificationError for every failure
    — see the module docstring. `token` should be exactly what the
    frontend sends as `Authorization: Bearer <token>`, with the "Bearer "
    prefix already stripped by the caller."""
    _require_config_for_mode()
    try:
        if SUPABASE_AUTH_JWT_MODE == "jwks":
            signing_key = _jwks_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256", "ES256"], audience=SUPABASE_AUTH_AUDIENCE,
            )
        else:
            claims = jwt.decode(
                token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience=SUPABASE_AUTH_AUDIENCE,
            )
    except jwt.PyJWTError as exc:
        raise TokenVerificationError(f"Token verification failed: {exc}") from exc

    subject = claims.get("sub")
    if not subject:
        raise TokenVerificationError("Token has no 'sub' claim — cannot identify the caller.")
    return VerifiedIdentity(subject=subject, email=claims.get("email"), raw_claims=claims)
