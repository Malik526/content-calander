"""
routes/platforms_instagram.py — the hosted Instagram connection flow
(status Milestone 4.0; connect, callback and disconnect Milestone 4.1).

What it does:
  status / connect / disconnect are protected (the user comes only from
  the verified bearer token). callback is deliberately PUBLIC: Instagram
  redirects the user's browser here, so no Authorization header exists.
  The callback is bound to a user only through the single-use,
  platform-scoped, expiring oauth_states row connect() created while the
  caller was authenticated — never through anything in the callback URL.
  Same protections as TikTok (routes/platforms_tiktok.py, ADR-0011), per
  docs/decisions/0018-instagram-integration-architecture.md Decision 6.

  connect     → oauth_states(user, 'instagram', state, code_verifier='',
                INSTAGRAM_REDIRECT_URI, return_target) and the Instagram
                authorization URL. Instagram Login documents no PKCE.
  callback    → consume the state (Instagram only) → code → short-lived →
                long-lived token (publishing/instagram/oauth.py) → encrypted
                platform_credentials row → connection ACTIVE with the
                current Instagram account id → redirect to the attempt's
                allowlisted return target with ?instagram=<outcome>.
  disconnect  → delete the caller's credential, mark only the caller's
                connection DISCONNECTED. Idempotent.
  status      → connected only with an ACTIVE connection AND a stored
                credential; account_label "@username" from a live,
                best-effort identity lookup (publishing/instagram/identity.py).

  Callback outcomes (the only detail the browser ever sees): connected,
  denied, invalid_state, expired_state (unknown, replayed, expired or
  another platform's state), exchange_failed, unavailable (server not
  configured to finish). Meta's own error text, codes and tokens are never
  forwarded, returned or logged. No response carries a credential or the
  numeric Instagram account id.

  Nothing here publishes: Instagram stays publishing_available=False until
  Milestone 4.2, so no platform_posts rows are created for it.

Dependencies:
  api.dependencies.auth, api.schemas.platforms, api.oauth_return_targets,
  publishing.platforms, publishing.instagram.{configuration, oauth,
  credential_store, identity}.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from content_automation import config
from content_automation.api import oauth_return_targets
from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.schemas.platforms import ConnectStartRequest, ConnectStartResponse, PlatformConnectionStatus
from content_automation.persistence.content_store import UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.credential_encryption import CredentialStoreError
from content_automation.publishing.instagram import oauth as instagram_oauth
from content_automation.publishing.instagram.configuration import is_configured
from content_automation.publishing.instagram.credential_store import save_hosted_instagram_token
from content_automation.publishing.instagram.identity import fetch_instagram_username
from content_automation.publishing.platforms import INSTAGRAM, get_platform

router = APIRouter()
logger = logging.getLogger(__name__)

# The query parameter carrying the callback outcome to the return target.
OUTCOME_PARAM = "instagram"


def connect_available() -> bool:
    """The connect flow exists (registry), Instagram is fully configured,
    and there is somewhere to send the browser afterwards."""
    return (
        get_platform(INSTAGRAM).connection_available
        and is_configured()
        and oauth_return_targets.default_return_target() is not None
    )


def _status(connected: bool, status: str, account_label: str | None = None) -> PlatformConnectionStatus:
    return PlatformConnectionStatus(
        platform=INSTAGRAM, connected=connected, status=status, account_label=account_label,
        connect_available=connect_available(),
    )


def _finish(return_target: str | None, outcome: str) -> Response:
    """Redirect to the attempt's stored target — re-checked against the
    current allowlist — or the default Settings page."""
    destination = return_target if oauth_return_targets.is_allowed_return_target(return_target) else None
    destination = destination or oauth_return_targets.default_return_target()
    if destination is None:
        # No frontend configured at all; connect is unavailable in this
        # state, so only a stray callback lands here.
        return PlainTextResponse(f"Instagram connection: {outcome}", status_code=200 if outcome == "connected" else 400)
    return RedirectResponse(oauth_return_targets.with_outcome(destination, OUTCOME_PARAM, outcome), status_code=302)


def _log(event: str, **fields) -> None:
    """key=value, ids and outcome codes only."""
    logger.info(" ".join([f"event={event}", *(f"{key}={value}" for key, value in fields.items())]))


@router.get("/platforms/instagram/status", response_model=PlatformConnectionStatus)
def get_instagram_status(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> PlatformConnectionStatus:
    connection = store.get_platform_connection(user.id, INSTAGRAM)
    if connection is None:
        return _status(False, "DISCONNECTED")
    # Same rule as TikTok: ACTIVE with a stored credential is connected;
    # an ACTIVE row without one is not.
    if connection.status != "ACTIVE":
        return _status(False, connection.status)
    if store.get_platform_credential(connection.id) is None:
        return _status(False, "DISCONNECTED")
    username = fetch_instagram_username(store, connection.id)
    return _status(True, "ACTIVE", f"@{username}" if username else None)


@router.post("/platforms/instagram/connect", response_model=ConnectStartResponse)
def start_instagram_connect(
    request: ConnectStartRequest | None = None,
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> ConnectStartResponse:
    if not connect_available():
        raise HTTPException(status_code=503, detail="Connecting Instagram is not available on this server.")
    try:
        return_target = oauth_return_targets.resolve_return_target(request.return_target if request else None)
    except oauth_return_targets.ReturnTargetNotAllowedError:
        raise HTTPException(status_code=400, detail="return_target is not an allowed destination.") from None

    redirect_uri = config.INSTAGRAM_REDIRECT_URI
    state = instagram_oauth.generate_state()
    now = datetime.now(timezone.utc)
    store.create_oauth_state(
        user.id, INSTAGRAM, state, "", redirect_uri, now.isoformat(),
        (now + timedelta(seconds=config.OAUTH_STATE_TTL_SECONDS)).isoformat(), return_target=return_target,
    )
    return ConnectStartResponse(authorization_url=instagram_oauth.build_authorization_url(state, redirect_uri))


@router.get("/platforms/instagram/callback", name="instagram_oauth_callback")
def instagram_oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None,
    store: ContentStoreProtocol = Depends(get_store),
) -> Response:
    if not state:
        return _finish(None, "invalid_state")
    now = datetime.now(timezone.utc).isoformat()
    consumed = store.consume_oauth_state(state, now, platform=INSTAGRAM)
    if consumed is None:
        # Unknown, already used, expired, or another platform's state —
        # all mean "start over", and none is consumed by this request.
        return _finish(None, "expired_state")
    target = consumed.return_target
    if error or not code:
        # The user cancelled (error=access_denied) or Instagram sent no code.
        _log("instagram_connect_denied", user_id=consumed.user_id)
        return _finish(target, "denied")
    if not is_configured():
        return _finish(target, "unavailable")

    try:
        token = instagram_oauth.exchange_code_for_long_lived_token(code, consumed.redirect_uri)
    except instagram_oauth.InstagramAuthError as exc:
        _log("instagram_connect_exchange_failed", user_id=consumed.user_id, failure_code=exc.reason_code)
        return _finish(target, "exchange_failed")

    # The owner is the user who started the attempt (from the state row),
    # never anything in the callback request.
    connection = store.get_platform_connection(consumed.user_id, INSTAGRAM)
    if connection is None:
        # Created inactive; activated only once its credential is stored.
        connection = store.create_platform_connection(consumed.user_id, INSTAGRAM, token["user_id"], "DISCONNECTED", now)
    try:
        save_hosted_instagram_token(store, connection.id, token)
    except CredentialStoreError:
        _log("instagram_connect_credential_unavailable", user_id=consumed.user_id)
        return _finish(target, "unavailable")
    # A reconnect may have chosen a different Instagram account.
    store.update_platform_connection_external_account(connection.id, token["user_id"], now)
    store.update_platform_connection_status(connection.id, "ACTIVE", now)
    _log("instagram_connected", user_id=consumed.user_id, platform_connection_id=connection.id)
    return _finish(target, "connected")


@router.post("/platforms/instagram/disconnect", response_model=PlatformConnectionStatus)
def disconnect_instagram(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> PlatformConnectionStatus:
    connection = store.get_platform_connection(user.id, INSTAGRAM)
    if connection is not None:
        now = datetime.now(timezone.utc).isoformat()
        store.delete_platform_credential(connection.id)
        store.update_platform_connection_status(connection.id, "DISCONNECTED", now)
    return _status(False, "DISCONNECTED")
