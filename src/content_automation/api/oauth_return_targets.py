"""
oauth_return_targets.py — where an OAuth callback may send the browser
afterwards (Milestone 4.1).

What it does:
  A connect request may name a return target; the server stores it on the
  oauth_states row only if it is an exact entry in a server-owned
  allowlist, and the callback redirects there with its outcome appended.
  This is what makes the flow native-ready (a future app scheme or
  universal link becomes one more allowlisted entry) without ever letting
  the client choose an arbitrary redirect destination — an open redirect
  in an OAuth callback would hand attackers a trusted-looking bounce.

  The allowlist:
    - the web Settings page, FRONTEND_BASE_URL + "/app/settings" (always,
      and the default when a request names none);
    - config.OAUTH_EXTRA_RETURN_TARGETS (empty by default).
  Matching is exact string equality: no prefixes, host suffixes, patterns
  or normalization, so lookalike hosts, protocol-relative URLs, embedded
  credentials or extra paths can't slip through. Entries themselves must be
  absolute https:// URLs without user info (or http:// on localhost, for
  development); a misconfigured entry is ignored, never honored.

  Instagram uses this from Milestone 4.1. TikTok keeps its fixed Settings
  redirect until it moves onto oauth_states.return_target (ADR-0018
  Decision 6).

Dependencies:
  content_automation.config (read at call time so tests can override it).
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from content_automation import config

SETTINGS_PATH = "/app/settings"
_LOCAL_DEVELOPMENT_HOSTS = frozenset({"localhost", "127.0.0.1"})


class ReturnTargetNotAllowedError(ValueError):
    """The requested return target is not an exact allowlisted entry. The
    message never echoes the requested value."""


def _is_acceptable(url: str) -> bool:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return False
    if not hostname or parts.username is not None or parts.password is not None:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and hostname in _LOCAL_DEVELOPMENT_HOSTS


def default_return_target() -> str | None:
    """The web Settings page, or None when FRONTEND_BASE_URL is unset or
    unusable (connecting is then unavailable)."""
    base = config.FRONTEND_BASE_URL.rstrip("/")
    target = f"{base}{SETTINGS_PATH}" if base else ""
    return target if target and _is_acceptable(target) else None


def allowed_return_targets() -> list[str]:
    default = default_return_target()
    extras = [target for target in config.OAUTH_EXTRA_RETURN_TARGETS if _is_acceptable(target)]
    return ([default] if default else []) + extras


def is_allowed_return_target(target: str | None) -> bool:
    return bool(target) and target in allowed_return_targets()


def resolve_return_target(requested: str | None) -> str:
    """The target to store for a new attempt: the request's own value if it
    is exactly allowlisted, the default when it named none. Raises
    ReturnTargetNotAllowedError otherwise, and when no default exists."""
    if requested is None:
        default = default_return_target()
        if default is None:
            raise ReturnTargetNotAllowedError("No default OAuth return target is configured (FRONTEND_BASE_URL).")
        return default
    if not is_allowed_return_target(requested):
        raise ReturnTargetNotAllowedError("return_target is not an allowed destination.")
    return requested


def with_outcome(target: str, param: str, outcome: str) -> str:
    """target with `param=outcome` set in its query string. The target's
    other query parameters and fragment are kept; an existing `param` is
    replaced, never duplicated."""
    parts = urlsplit(target)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != param]
    query.append((param, outcome))
    return urlunsplit(parts._replace(query=urlencode(query)))
