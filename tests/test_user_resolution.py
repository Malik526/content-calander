"""Tests for identity.user_resolution (Milestone 3.6): mapping a verified
Supabase identity onto users/auth_identities."""

import pytest

from content_automation.identity.token_verification import VerifiedIdentity
from content_automation.identity.user_resolution import (
    AccountConflictError,
    resolve_or_create_user,
)
from content_automation.persistence.content_store import ContentStore


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def _identity(subject="google-sub-1", email="creator@example.com", name="Ada Creator") -> VerifiedIdentity:
    return VerifiedIdentity(
        subject=subject,
        email=email,
        raw_claims={"sub": subject, "email": email, "user_metadata": {"name": name}},
    )


def test_first_login_creates_a_new_user_and_links_the_identity(store):
    identity = _identity()

    user = resolve_or_create_user(store, identity, provider="supabase")

    assert user.email == "creator@example.com"
    assert user.display_name == "Ada Creator"
    linked = store.get_user_by_auth_identity("supabase", identity.subject)
    assert linked is not None
    assert linked.id == user.id


def test_second_login_resolves_the_same_user_without_creating_a_duplicate(store):
    identity = _identity()
    first = resolve_or_create_user(store, identity, provider="supabase")

    second = resolve_or_create_user(store, identity, provider="supabase")

    assert second.id == first.id


def test_two_different_identities_get_two_different_users(store):
    user_a = resolve_or_create_user(store, _identity(subject="sub-a", email="a@example.com"), provider="supabase")
    user_b = resolve_or_create_user(store, _identity(subject="sub-b", email="b@example.com"), provider="supabase")

    assert user_a.id != user_b.id


def test_email_without_a_name_claim_falls_back_to_the_email_local_part(store):
    identity = VerifiedIdentity(subject="sub-c", email="plainuser@example.com", raw_claims={})

    user = resolve_or_create_user(store, identity, provider="supabase")

    assert user.display_name == "plainuser"


def test_missing_email_falls_back_to_a_provider_scoped_placeholder(store):
    identity = VerifiedIdentity(subject="sub-no-email", email=None, raw_claims={})

    user = resolve_or_create_user(store, identity, provider="supabase")

    assert user.email == "supabase+sub-no-email@users.pickle-batch.local"
    assert user.display_name == "sub-no-email"


def test_conflicting_email_from_an_unlinked_account_refuses_to_auto_link(store):
    # An existing account (e.g. the bootstrap user, or a differently-linked
    # identity) already owns this email.
    store.create_user("shared@example.com", "Existing Account", "2026-01-01T00:00:00+00:00")
    identity = VerifiedIdentity(subject="new-sub", email="shared@example.com", raw_claims={})

    with pytest.raises(AccountConflictError):
        resolve_or_create_user(store, identity, provider="supabase")


def test_different_providers_for_the_same_subject_value_create_different_users(store):
    # (provider, provider_subject) is the identity key — the same raw
    # subject string under two different providers must not collide.
    user_google = resolve_or_create_user(
        store, _identity(subject="shared-sub", email="g@example.com"), provider="google",
    )
    user_other = resolve_or_create_user(
        store, _identity(subject="shared-sub", email="o@example.com"), provider="other-provider",
    )

    assert user_google.id != user_other.id
