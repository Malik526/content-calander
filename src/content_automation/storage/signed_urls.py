"""
signed_urls.py — rules for short-lived signed URLs to private stored media
(Milestone 4.0).

What it does:
  Platforms that pull media from a URL (Instagram's Reel video_url) need a
  link they can fetch, while canonical media stays in a private bucket.
  StorageProtocol.create_signed_url issues a time-limited link to one
  object; this module holds the shared rules:

    validate_ttl        5 minutes to 24 hours. Shorter can expire before a
                        platform fetches; longer is a long-lived link to
                        private media, which this design exists to avoid.
    redact_signed_url   A signed URL is a bearer credential for that object
                        until it expires. Anything logged or stored for
                        diagnostics must go through this, which drops the
                        query string (where the token lives).

  Never store a signed URL in the database and never return one from the
  API: issue it right before handing it to the platform.

Dependencies:
  stdlib only.
"""

from urllib.parse import urlsplit, urlunsplit

MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 86_400


class SignedUrlUnsupportedError(Exception):
    """The storage backend can't issue a URL an external platform could
    fetch (e.g. LocalStorage: files on this machine)."""


def validate_ttl(expires_in_seconds: int) -> int:
    if not MIN_TTL_SECONDS <= expires_in_seconds <= MAX_TTL_SECONDS:
        raise ValueError(f"Signed URL lifetime must be {MIN_TTL_SECONDS}–{MAX_TTL_SECONDS} seconds, got {expires_in_seconds}")
    return expires_in_seconds


def redact_signed_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "[redacted]" if parts.query else "", ""))
