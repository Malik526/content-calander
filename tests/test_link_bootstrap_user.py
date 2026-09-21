"""Tests for cli/link_bootstrap_user.py (Milestone 3.6): the explicit,
one-time link between a real Supabase Auth identity and the existing local
bootstrap user."""

import pytest

import link_bootstrap_user as lbu
from content_automation.persistence.content_store import LOCAL_BOOTSTRAP_USER_EMAIL, ContentStore


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def test_refuses_when_no_bootstrap_user_exists_yet(store):
    with pytest.raises(lbu.BootstrapLinkError, match="No bootstrap user"):
        lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-1", provider_email=None)


def test_dry_run_reports_would_link_without_writing(store):
    store.get_or_create_local_user()

    report = lbu.link_bootstrap_user(
        store, provider="supabase", provider_subject="sub-1", provider_email="me@real.com", dry_run=True,
    )

    assert report["action"] == "would_link"
    assert store.get_user_by_auth_identity("supabase", "sub-1") is None


def test_links_the_identity_to_the_existing_bootstrap_user(store):
    bootstrap = store.get_or_create_local_user()

    report = lbu.link_bootstrap_user(
        store, provider="supabase", provider_subject="sub-1", provider_email="me@real.com",
    )

    assert report["user_id"] == bootstrap.id
    linked = store.get_user_by_auth_identity("supabase", "sub-1")
    assert linked is not None
    assert linked.id == bootstrap.id
    # No second user was created — the bootstrap user's real videos/posts
    # stay attributed to the same id.
    assert store.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL).id == bootstrap.id


def test_rerunning_with_the_same_identity_is_a_harmless_no_op(store):
    store.get_or_create_local_user()
    lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-1", provider_email=None)

    report = lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-1", provider_email=None)

    assert report["action"] == "already_linked_to_bootstrap"
    assert report["already_linked"] is True


def test_refuses_to_reassign_an_identity_already_linked_to_a_different_user(store):
    store.get_or_create_local_user()
    other_user = store.create_user("someone-else@example.com", "Someone Else", "2026-01-01T00:00:00+00:00")
    store.create_auth_identity(other_user.id, "supabase", "sub-taken", None, "2026-01-01T00:00:00+00:00")

    with pytest.raises(lbu.BootstrapLinkError, match="already linked to a different user"):
        lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-taken", provider_email=None)


def test_refuses_a_second_different_identity_for_the_same_provider(store):
    store.get_or_create_local_user()
    lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-1", provider_email=None)

    with pytest.raises(lbu.BootstrapLinkError, match="already has a different supabase identity"):
        lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-2-different", provider_email=None)


def test_does_not_touch_existing_videos_content_slots_platform_posts(store):
    bootstrap = store.get_or_create_local_user()
    store._conn.execute(
        "INSERT INTO videos (file_hash, original_filename, original_path, status, created_at, user_id) "
        "VALUES ('h1', 'f.mp4', '/tmp/f.mp4', 'DISCOVERED', '2026-01-01T00:00:00+00:00', ?)",
        (bootstrap.id,),
    )
    before = store._conn.execute("SELECT * FROM videos WHERE file_hash = 'h1'").fetchone()

    lbu.link_bootstrap_user(store, provider="supabase", provider_subject="sub-1", provider_email=None)

    after = store._conn.execute("SELECT * FROM videos WHERE file_hash = 'h1'").fetchone()
    assert dict(before) == dict(after)
