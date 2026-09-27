"""Tests for the API's CORS configuration (app.py's CORSMiddleware).

Regression coverage for the 2026-09-26 production incident: a browser
DELETE against /api/videos/{id} from https://picklebatch.netlify.app
failed because the preflight OPTIONS request returned a non-OK response.
Root cause, confirmed both locally and against the live Railway
deployment: CORSMiddleware's allow_methods was ["GET", "POST"] and had
never been updated when the DELETE route was added (Milestone 3.7's
Delete Video follow-up) — Starlette's CORSMiddleware rejects a preflight
with 400 "Disallowed CORS method" whenever the browser's
Access-Control-Request-Method isn't in that list, entirely inside the
middleware, before the request ever reaches routing/auth.

These tests deliberately do not hardcode the literal string
"https://picklebatch.netlify.app" as the Origin header: that value lives
in config.API_CORS_ALLOWED_ORIGINS, environment-specific configuration
data that legitimately differs between this repo's local .env and
Railway's real dashboard value (see AGENTS.md's CORS investigation note).
Testing against whatever origin this environment actually has configured
proves the fix at the layer that broke (allow_methods, not origin
matching) without coupling test pass/fail to one machine's .env content.
The live production origin was independently confirmed via a real
preflight request against the deployed API as part of this fix.
"""

from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.config import API_CORS_ALLOWED_ORIGINS

_ALLOWED_ORIGIN = API_CORS_ALLOWED_ORIGINS[0]


def _cors_middleware_kwargs() -> dict:
    return next(m for m in app_module.app.user_middleware if m.cls is CORSMiddleware).kwargs


def test_cors_allow_methods_includes_delete_get_and_post():
    """Reads the middleware's own baked-in configuration directly —
    independent of any origin — so this fails immediately again if
    allow_methods ever regresses, without needing a live HTTP round trip."""
    allow_methods = _cors_middleware_kwargs()["allow_methods"]
    assert "DELETE" in allow_methods
    assert "GET" in allow_methods
    assert "POST" in allow_methods
    assert "PUT" in allow_methods  # Milestone 3.8 — PUT /api/cadence


def test_delete_preflight_succeeds_for_an_allowed_origin():
    """End-to-end: mirrors the exact preflight a browser sends before
    DELETE /api/videos/{id} (Origin + Access-Control-Request-Method:
    DELETE + Access-Control-Request-Headers: authorization, since
    Authorization is a non-safelisted header)."""
    client = TestClient(app_module.app)

    response = client.options(
        "/api/videos/123",
        headers={
            "Origin": _ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_get_and_post_preflight_remain_unaffected_by_the_delete_fix():
    """GET /api/videos and POST /api/videos preflights must keep working
    exactly as before adding DELETE to allow_methods."""
    client = TestClient(app_module.app)

    for method in ("GET", "POST"):
        response = client.options(
            "/api/videos",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code == 200, f"{method} preflight regressed"
        assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN


def test_disallowed_method_preflight_still_fails_closed():
    """A method that's genuinely never been allowed (e.g. PATCH) must
    still be rejected — proves the fix added specific methods (DELETE,
    later PUT for Milestone 3.8's /api/cadence), not "*". PUT itself
    moved to the allowed list once /api/cadence needed it — see
    test_put_and_delete_preflight_are_allowed below."""
    client = TestClient(app_module.app)

    response = client.options(
        "/api/videos/123",
        headers={
            "Origin": _ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
        },
    )

    assert response.status_code == 400


def test_put_and_delete_preflight_are_allowed():
    """Milestone 3.8 added PUT (for PUT /api/cadence) to allow_methods,
    alongside DELETE (Milestone 3.7's own CORS fix)."""
    client = TestClient(app_module.app)

    for method in ("PUT", "DELETE"):
        response = client.options(
            "/api/cadence",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code == 200, f"{method} preflight should be allowed"
