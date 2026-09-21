"""
user_resolution.py — maps a verified Supabase identity onto this
codebase's users/auth_identities model (Milestone 3.6).

What it does:
  resolve_or_create_user() is the login-time entry point: given a verified
  identity (token_verification.VerifiedIdentity) and the provider name it
  came from, look up an existing auth_identities row
  (store.get_user_by_auth_identity); if none exists, this is a first-time
  login — create a new users row and a new auth_identities row linking
  them.

  Deliberately does NOT merge by email — a matching users.email from a
  different, already-existing account is treated as an unresolved
  conflict (AccountConflictError), never a silent auto-link. Auto-linking
  by email is a real security footgun (email reuse/typosquatting across
  identity providers) this milestone does not take on; see
  docs/decisions/0011-real-authentication-and-tiktok-connection.md "User /
  Auth Identity Mapping".

  users.display_name is derived from the verified identity's claims when
  available (Supabase's Google provider typically includes a "name" claim
  under user_metadata) — cosmetic only, never used for identity matching.

Concurrency (a known, accepted imperfection, not silently ignored):
  Two near-simultaneous first logins for the same brand-new identity could
  both pass the get_user_by_auth_identity check before either has
  committed. auth_identities' own UNIQUE(provider, provider_subject)
  constraint (content_store.SCHEMA_AUTH_IDENTITIES) is what actually
  prevents two Pickle Batch accounts from ending up linked to the same
  external identity — the loser's create_auth_identity call raises an
  integrity error, re-raised here as DuplicateIdentityRaceError so a
  caller can retry the whole resolve rather than seeing a raw
  sqlite3/psycopg exception. The loser's own create_user call, however,
  already committed by that point — this leaves a harmless orphaned
  `users` row (no linked auth_identity, so never reachable via any future
  login) rather than true single-statement atomicity across both inserts,
  which this module does not attempt to provide. Accepted for V1: no data
  corruption, no security exposure, just an occasional unused row: adding
  real cross-store transactional wrapping for this narrow race is not
  justified by this milestone's brief.

Dependencies:
  content_automation.persistence.protocol.ContentStoreProtocol.
  content_automation.identity.token_verification.VerifiedIdentity.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from content_automation.identity.token_verification import VerifiedIdentity
from content_automation.persistence.content_store import UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol

try:
    import psycopg

    _INTEGRITY_ERRORS: tuple[type[Exception], ...] = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
except ImportError:  # pragma: no cover - psycopg is a hard requirement in this repo (requirements.txt)
    _INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


class AccountConflictError(Exception):
    """A verified identity's email matches an existing users row that is
    not already linked to this (provider, provider_subject) — refuses to
    auto-link. See module docstring."""


class DuplicateIdentityRaceError(Exception):
    """Two concurrent first-logins for the same (provider, subject) raced;
    the caller should simply retry resolve_or_create_user() — the winning
    row is already committed. See module docstring's "Concurrency" note."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_name_from_identity(identity: VerifiedIdentity) -> str:
    claims = identity.raw_claims or {}
    user_metadata = claims.get("user_metadata") or {}
    for key in ("name", "full_name"):
        value = user_metadata.get(key) or claims.get(key)
        if value:
            return str(value)
    if identity.email:
        return identity.email.split("@", 1)[0]
    return identity.subject


def resolve_or_create_user(store: ContentStoreProtocol, identity: VerifiedIdentity, provider: str) -> UserRecord:
    """The login-time entry point. Returns the Pickle Batch UserRecord this
    verified identity maps to, creating a new account on first login."""
    existing = store.get_user_by_auth_identity(provider, identity.subject)
    if existing is not None:
        return existing

    now = _utc_now_iso()
    # Supabase's Google provider always includes an email, but this is
    # defensive for any future provider that might not — users.email is
    # NOT NULL UNIQUE, so a login-derived placeholder is used rather than
    # failing a real, verified login over a missing optional claim.
    email = identity.email or f"{provider}+{identity.subject}@users.pickle-batch.local"

    conflicting = store.get_user_by_email(email)
    if conflicting is not None:
        raise AccountConflictError(
            f"An account already exists for email {email!r} that is not linked to this "
            f"{provider} identity ({identity.subject}). Refusing to auto-link — see "
            "cli/link_bootstrap_user.py for the equivalent manual, verified linking flow used "
            "for the pre-3.6 bootstrap account."
        )

    try:
        user = store.create_user(email, _display_name_from_identity(identity), now)
        store.create_auth_identity(user.id, provider, identity.subject, identity.email, now)
    except _INTEGRITY_ERRORS as exc:
        raise DuplicateIdentityRaceError(
            f"Concurrent first login for {provider}:{identity.subject} — retry resolve_or_create_user()."
        ) from exc
    return user
