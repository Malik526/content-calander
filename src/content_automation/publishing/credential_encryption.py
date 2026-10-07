"""
credential_encryption.py — provider-neutral encryption of hosted platform
credentials at rest (Milestone 4.1; moved here from
publishing/tiktok/credential_store.py, which re-exports these names
unchanged).

What it does:
  encrypt_credential() / decrypt_credential() turn a platform's credential
  dict into the Fernet ciphertext stored in platform_credentials
  .encrypted_payload and back. Every platform's hosted credential store
  (publishing/tiktok/credential_store.py, publishing/instagram/
  credential_store.py) goes through these two functions, so there is one
  key, one cipher and one failure mode for all of them. ContentStore never
  sees plaintext.

Encryption:
  Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) with
  config.CREDENTIAL_ENCRYPTION_KEY — a symmetric key generated once and set
  in the API/worker environment, never derived from anything guessable.
  Rotating it invalidates every stored hosted credential (decryption fails
  closed with CredentialStoreError). See
  docs/decisions/0011-real-authentication-and-tiktok-connection.md
  "Credential Storage".

  Error messages name the configuration variable only — never the key, the
  ciphertext or any decrypted value.

Dependencies:
  cryptography.fernet, content_automation.config.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from content_automation.config import CREDENTIAL_ENCRYPTION_KEY


class CredentialStoreError(Exception):
    """Misconfiguration (no/invalid encryption key), a stored credential
    that fails to decrypt (wrong/rotated key, corrupted data), or repeated
    CAS contention refreshing a credential."""


def _require_fernet() -> Fernet:
    if not CREDENTIAL_ENCRYPTION_KEY:
        raise CredentialStoreError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with: python3 -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and set it in .env. Never commit it."
        )
    try:
        return Fernet(CREDENTIAL_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CredentialStoreError(f"CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_credential(credential: dict) -> str:
    return _require_fernet().encrypt(json.dumps(credential).encode("utf-8")).decode("ascii")


def decrypt_credential(encrypted_payload: str) -> dict:
    try:
        raw = _require_fernet().decrypt(encrypted_payload.encode("ascii"))
    except InvalidToken as exc:
        raise CredentialStoreError(
            "Stored platform credential could not be decrypted (wrong/rotated CREDENTIAL_ENCRYPTION_KEY, "
            "or corrupted data)."
        ) from exc
    return json.loads(raw.decode("utf-8"))
