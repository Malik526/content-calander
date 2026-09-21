-- 0003_add_platform_credentials_and_oauth_states.sql — Milestone 3.6 (real
-- authentication + hosted TikTok connection). A new forward migration, not
-- a rewrite of 0001/0002 (both already applied against the real "public"
-- schema) — see docs/decisions/0011-real-authentication-and-tiktok-connection.md
-- "Credential Storage".
--
-- platform_credentials deliberately stays a separate table from
-- platform_connections (which is documented, in 0001, to carry no
-- credential secrets) rather than adding columns to it — identity/status
-- stays in platform_connections, the encrypted access/refresh token pair
-- lives only here. UNIQUE(platform_connection_id): one credential per
-- connection. updated_at backs an optimistic-concurrency (CAS) refresh —
-- see PostgresContentStore.update_platform_credential_if_unchanged.
--
-- oauth_states is the hosted, DB-backed, user-bound equivalent of
-- publishing/tiktok/auth.py's local TIKTOK_PENDING_AUTH_PATH file, needed
-- because the connect and callback legs of a hosted OAuth flow are two
-- separate stateless API requests, not one long-lived local CLI process.
-- `state` is UNIQUE; `consumed_at` makes replaying an already-used state a
-- no-op rejection (see ContentStore.consume_oauth_state's CAS write).

CREATE TABLE IF NOT EXISTS platform_credentials (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform_connection_id BIGINT NOT NULL UNIQUE REFERENCES platform_connections(id),
    encrypted_payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);
