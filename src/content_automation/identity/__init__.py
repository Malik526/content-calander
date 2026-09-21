"""identity — real user authentication (Milestone 3.6): verifying who is
calling the API (token_verification.py) and mapping that identity onto this
codebase's own users/auth_identities model (user_resolution.py). See
docs/decisions/0011-real-authentication-and-tiktok-connection.md.

Deliberately not named "auth" — this codebase already uses that word for
two unrelated OAuth flows (publishing/tiktok/auth.py, calendar_manager.py's
Google Calendar OAuth). "Identity" is reserved for "who is this web user,"
never a third meaning layered onto an already-overloaded name.
"""
