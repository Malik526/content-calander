"""
routes/platforms_tiktok.py — hosted TikTok OAuth connection flow
(Milestone 3.6).

What it does:
  status / connect / disconnect are protected (require a verified user —
  see api/dependencies/auth.py). callback is deliberately PUBLIC: TikTok
  redirects the user's browser here directly (a top-level navigation, not
  an XHR the frontend's own bearer-token-attaching client controls), so no
  Authorization header is available at that point. The binding to "which
  authenticated user does this belong to" comes entirely from the
  server-side oauth_states row created by connect() while the caller WAS
  authenticated — never from anything the browser/TikTok redirect itself
  carries. This is the concrete implementation of "OAuth state must
  securely bind the callback to the authenticated user/session" and
  "never let the browser decide which user_id receives the TikTok
  credential" (see
  docs/decisions/0011-real-authentication-and-tiktok-connection.md
  "TikTok OAuth").

  Reuses publishing/tiktok/auth.py's PKCE/state generation and token
  exchange/refresh functions verbatim — this module only adds the hosted
  connect/callback HTTP legs and where the resulting credential is stored
  (publishing/tiktok/credential_store.py), never re-implementing TikTok's
  OAuth wire protocol.

  Every response shape here is Phase 16's explicit requirement: never
  return access_token/refresh_token/client_secret/any raw credential —
  see api/schemas/platforms.py.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.schemas.platforms import TikTokConnectionStatus, TikTokConnectStartResponse
from content_automation.config import FRONTEND_BASE_URL, OAUTH_STATE_TTL_SECONDS
from content_automation.persistence.content_store import UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.tiktok import auth as tiktok_auth
from content_automation.publishing.tiktok import credential_store

router = APIRouter()

PLATFORM = "tiktok"


def _settings_redirect(reason: str) -> RedirectResponse:
    """Every callback outcome (success or failure) lands back on the real
    product page, never a bare JSON error a browser would show raw —
    Phase 19's "clear error UX," not "expose sensitive error details."""
    return RedirectResponse(f"{FRONTEND_BASE_URL}/app/settings?tiktok={reason}", status_code=302)


@router.get("/platforms/tiktok/status", response_model=TikTokConnectionStatus)
def get_tiktok_status(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> TikTokConnectionStatus:
    connection = store.get_platform_connection(user.id, PLATFORM)
    if connection is None or connection.status != "ACTIVE":
        return TikTokConnectionStatus(connected=False, status=connection.status if connection else "DISCONNECTED")
    credential = store.get_platform_credential(connection.id)
    if credential is None:
        # A connection row exists (e.g. from the pre-3.6 backfill) but no
        # hosted credential was ever saved for it — not connected from the
        # hosted product's point of view, even though the row says ACTIVE.
        return TikTokConnectionStatus(connected=False, status="DISCONNECTED")
    return TikTokConnectionStatus(
        connected=True, status=connection.status, account_label=connection.external_account_id,
    )


@router.post("/platforms/tiktok/connect", response_model=TikTokConnectStartResponse)
def start_tiktok_connect(
    request: Request,
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> TikTokConnectStartResponse:
    redirect_uri = str(request.url_for("tiktok_oauth_callback"))
    state = tiktok_auth.generate_state()
    verifier, challenge = tiktok_auth.generate_pkce_pair()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    store.create_oauth_state(
        user.id, PLATFORM, state, verifier, redirect_uri, now.isoformat(), expires_at.isoformat(),
    )

    authorization_url = tiktok_auth.build_authorization_url(state=state, code_challenge=challenge, redirect_uri=redirect_uri)
    return TikTokConnectStartResponse(authorization_url=authorization_url)


@router.get("/platforms/tiktok/callback", name="tiktok_oauth_callback")
def tiktok_oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None,
    error_description: str | None = None, store: ContentStoreProtocol = Depends(get_store),
) -> RedirectResponse:
    if error:
        # TikTok itself denied/failed the authorization before ever
        # issuing a code — the specific TikTok-provided reason is not
        # forwarded to the browser (Phase 19: no sensitive detail leakage).
        return _settings_redirect("denied")
    if not state:
        return _settings_redirect("invalid_state")

    now = datetime.now(timezone.utc).isoformat()
    consumed = store.consume_oauth_state(state, now)
    if consumed is None:
        # Covers three distinct real cases identically (state never
        # existed / already used / expired) — all mean the same thing to
        # the caller: start over.
        return _settings_redirect("expired_state")
    if not code:
        return _settings_redirect("denied")

    try:
        token = tiktok_auth.exchange_code_for_token(code, code_verifier=consumed.code_verifier, redirect_uri=consumed.redirect_uri)
    except tiktok_auth.TikTokAuthError:
        return _settings_redirect("exchange_failed")

    connection = store.get_or_create_platform_connection(consumed.user_id, PLATFORM, external_account_id=token.get("open_id"))
    if connection.status != "ACTIVE":
        store.update_platform_connection_status(connection.id, "ACTIVE", now)
    credential_store.save_hosted_tiktok_token(store, connection.id, token)

    return _settings_redirect("connected")


@router.post("/platforms/tiktok/disconnect", response_model=TikTokConnectionStatus)
def disconnect_tiktok(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> TikTokConnectionStatus:
    connection = store.get_platform_connection(user.id, PLATFORM)
    if connection is None:
        return TikTokConnectionStatus(connected=False, status="DISCONNECTED")

    now = datetime.now(timezone.utc).isoformat()
    store.delete_platform_credential(connection.id)
    store.update_platform_connection_status(connection.id, "DISCONNECTED", now)
    return TikTokConnectionStatus(connected=False, status="DISCONNECTED", account_label=connection.external_account_id)
