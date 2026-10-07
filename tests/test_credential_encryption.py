"""Tests for publishing.credential_encryption (Milestone 4.1): the
provider-neutral Fernet helpers moved out of the TikTok credential store,
and the TikTok module's compatible re-exports."""

import pytest
from cryptography.fernet import Fernet

from content_automation.publishing import credential_encryption as ce
from content_automation.publishing.tiktok import credential_store as tiktok_cs


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(ce, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


def test_roundtrip_and_ciphertext_hides_the_values():
    credential = {"access_token": "secret-access", "nested": {"a": 1}}
    ciphertext = ce.encrypt_credential(credential)
    assert "secret-access" not in ciphertext
    assert ce.decrypt_credential(ciphertext) == credential


def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.setattr(ce, "CREDENTIAL_ENCRYPTION_KEY", "")
    with pytest.raises(ce.CredentialStoreError, match="CREDENTIAL_ENCRYPTION_KEY"):
        ce.encrypt_credential({"access_token": "x"})


def test_invalid_key_fails_closed_without_echoing_it(monkeypatch):
    monkeypatch.setattr(ce, "CREDENTIAL_ENCRYPTION_KEY", "not-a-fernet-key-value")
    with pytest.raises(ce.CredentialStoreError) as error:
        ce.encrypt_credential({"access_token": "x"})
    assert "not-a-fernet-key-value" not in str(error.value)


def test_wrong_key_fails_closed_without_revealing_plaintext(monkeypatch):
    ciphertext = ce.encrypt_credential({"access_token": "secret-access"})
    monkeypatch.setattr(ce, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    with pytest.raises(ce.CredentialStoreError, match="could not be decrypted") as error:
        ce.decrypt_credential(ciphertext)
    assert "secret-access" not in str(error.value)


def test_tiktok_re_exports_are_the_shared_helpers():
    assert tiktok_cs.encrypt_token is ce.encrypt_credential
    assert tiktok_cs.decrypt_token is ce.decrypt_credential
    assert tiktok_cs.CredentialStoreError is ce.CredentialStoreError


def test_ciphertext_written_by_tiktok_names_reads_back_through_shared_names():
    token = {"access_token": "a", "refresh_token": "r", "open_id": "o"}
    assert ce.decrypt_credential(tiktok_cs.encrypt_token(token)) == token
