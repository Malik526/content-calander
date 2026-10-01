"""Tests for media.caption_editing and media.caption_generation (Milestone
3.10: Caption Generation + Editing) — provenance tracking, explicit
regeneration, locking after submission, and per-record (not per-file_hash)
caption state. Real temp-file SQLite ContentStore."""

import pytest

from content_automation.media import caption_editing as ce
from content_automation.media.caption_generation import (
    CaptionGenerationRequest,
    CaptionGenerationUnavailableError,
    build_generation_request,
    can_generate_caption,
    generate_caption,
)
from content_automation.persistence.content_store import ContentStore

NOW = "2026-09-30T08:00:00"


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def _video(store, name="v", file_hash=None, **fields):
    video = store.insert_video(file_hash or f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW)
    if fields:
        store.update_video(video.id, **fields)
    return store.get_video(video.id)


# --- generation contract ---------------------------------------------------

def test_generate_caption_uses_transcript_auto_derivation():
    generated = generate_caption(CaptionGenerationRequest(transcript="  hello\n  world  ", platform="tiktok"))
    assert generated.text == "hello world"
    assert generated.source == "transcript_auto"
    assert generated.metadata == {"generator": "transcript_auto"}


@pytest.mark.parametrize("transcript", [None, "", "   \n "])
def test_generate_caption_without_transcript_is_unavailable(transcript):
    with pytest.raises(CaptionGenerationUnavailableError):
        generate_caption(CaptionGenerationRequest(transcript=transcript))


def test_build_generation_request_carries_transcript_platform_and_metadata(store):
    video = _video(store, transcript="spoken words", duration_seconds=12.5)
    request = build_generation_request(video, platform="tiktok")
    assert request.transcript == "spoken words"
    assert request.platform == "tiktok"
    assert request.video_metadata["duration_seconds"] == 12.5
    assert request.creator_preferences == {}


def test_can_generate_caption_reflects_transcript_availability(store):
    assert can_generate_caption(_video(store, "a", transcript="words")) is True
    assert can_generate_caption(_video(store, "b")) is False


# --- provenance --------------------------------------------------------------

@pytest.mark.parametrize(
    "text, source, expected",
    [
        (None, None, "NONE"),
        (None, "manual", "NONE"),
        (None, "none", "NONE"),
        ("hi", "manual", "MANUAL"),
        ("hi", None, "MANUAL"),
        ("hi", "transcript_auto", "GENERATED"),
        ("hi", "transcript_auto_edited", "GENERATED_EDITED"),
    ],
)
def test_caption_provenance(store, text, source, expected):
    video = _video(store, caption_text=text, caption_source=source)
    assert ce.caption_provenance(video) == expected


# --- save_caption ------------------------------------------------------------

def test_manual_caption_persists_as_manual(store):
    video = _video(store)
    saved = ce.save_caption(store, video, "  my caption #fyp\nline two  ")
    assert saved.caption_text == "my caption #fyp\nline two"
    assert saved.caption_source == "manual"
    assert store.get_video(video.id).caption_text == "my caption #fyp\nline two"


def test_editing_a_generated_caption_keeps_generation_provenance(store):
    video = _video(store, transcript="hello world")
    generated = ce.regenerate_caption(store, video, overwrite=False)
    assert generated.caption_source == "transcript_auto"

    edited = ce.save_caption(store, generated, "hello world, edited")
    assert edited.caption_text == "hello world, edited"
    assert edited.caption_source == "transcript_auto_edited"
    assert ce.caption_provenance(edited) == "GENERATED_EDITED"

    # A second edit stays GENERATED_EDITED rather than stacking suffixes.
    again = ce.save_caption(store, edited, "third version")
    assert again.caption_source == "transcript_auto_edited"


def test_saving_unchanged_generated_text_does_not_mark_it_edited(store):
    video = ce.regenerate_caption(store, _video(store, transcript="hello world"), overwrite=False)
    saved = ce.save_caption(store, video, "hello world")
    assert saved.caption_source == "transcript_auto"


def test_clearing_a_caption_stores_null_with_manual_source(store):
    video = _video(store, caption_text="old", caption_source="transcript_auto")
    cleared = ce.save_caption(store, video, "   ")
    assert cleared.caption_text is None
    assert cleared.caption_source == "manual"
    assert ce.caption_provenance(cleared) == "NONE"


def test_identical_hash_records_are_captioned_independently(store):
    video_a = _video(store, "a", file_hash="xyz")
    video_b = _video(store, "b", file_hash="xyz")

    ce.save_caption(store, video_a, "caption for A")
    ce.save_caption(store, video_b, "completely different")

    assert store.get_video(video_a.id).caption_text == "caption for A"
    assert store.get_video(video_b.id).caption_text == "completely different"


# --- regenerate_caption ------------------------------------------------------

def test_regenerate_refuses_to_overwrite_existing_caption_without_confirmation(store):
    video = _video(store, transcript="spoken", caption_text="hand written", caption_source="manual")
    with pytest.raises(ce.CaptionOverwriteRequiredError):
        ce.regenerate_caption(store, video, overwrite=False)
    unchanged = store.get_video(video.id)
    assert unchanged.caption_text == "hand written"
    assert unchanged.caption_source == "manual"


def test_regenerate_with_overwrite_replaces_caption(store):
    video = _video(store, transcript="spoken words", caption_text="hand written", caption_source="manual")
    regenerated = ce.regenerate_caption(store, video, overwrite=True)
    assert regenerated.caption_text == "spoken words"
    assert regenerated.caption_source == "transcript_auto"


def test_regenerate_without_transcript_writes_nothing(store):
    video = _video(store)
    with pytest.raises(CaptionGenerationUnavailableError):
        ce.regenerate_caption(store, video, overwrite=True)
    assert store.get_video(video.id).caption_source is None


# --- locking ------------------------------------------------------------------

def test_pending_platform_post_leaves_caption_editable(store):
    video = _video(store)
    store.insert_platform_post(video.id, "tiktok", created_at=NOW)
    assert ce.is_caption_locked(store, video.id) is False
    assert ce.save_caption(store, video, "still editable").caption_text == "still editable"


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "PUBLISHING"},
        {"status": "PUBLISHED", "platform_post_id": "pub_1"},
        {"status": "FAILED", "platform_post_id": "pub_1"},
    ],
)
def test_submitted_platform_post_locks_caption(store, fields):
    video = _video(store, transcript="spoken", caption_text="sent", caption_source="manual")
    post = store.insert_platform_post(video.id, "tiktok", created_at=NOW)
    store.update_platform_post(post.id, updated_at=NOW, **fields)

    with pytest.raises(ce.CaptionLockedError):
        ce.save_caption(store, video, "changed")
    with pytest.raises(ce.CaptionLockedError):
        ce.regenerate_caption(store, video, overwrite=True)
    assert store.get_video(video.id).caption_text == "sent"


# --- derived hashtags (Milestone 3.10.1) --------------------------------------

def test_saved_caption_persists_hashtags_and_exact_text(store):
    caption = "I built this today.\n\n#coding  #saas\n#buildinpublic"
    saved = ce.save_caption(store, _video(store), caption)
    assert saved.caption_text == caption
    assert store.list_video_hashtags(saved.id) == ["#coding", "#saas", "#buildinpublic"]


def test_editing_caption_replaces_hashtags(store):
    video = ce.save_caption(store, _video(store), "first #old #stale")
    ce.save_caption(store, video, "second #new")
    assert store.list_video_hashtags(video.id) == ["#new"]


def test_clearing_caption_clears_hashtags(store):
    video = ce.save_caption(store, _video(store), "text #tag")
    ce.save_caption(store, video, "")
    assert store.list_video_hashtags(video.id) == []


def test_generated_and_regenerated_captions_sync_hashtags(store):
    video = _video(store, transcript="talking about #python today")
    generated = ce.regenerate_caption(store, video, overwrite=False)
    assert store.list_video_hashtags(video.id) == ["#python"]

    ce.save_caption(store, generated, "no tags now")
    assert store.list_video_hashtags(video.id) == []

    ce.regenerate_caption(store, store.get_video(video.id), overwrite=True)
    assert store.list_video_hashtags(video.id) == ["#python"]


def test_identical_hash_records_keep_independent_hashtags(store):
    video_a = _video(store, "a", file_hash="xyz")
    video_b = _video(store, "b", file_hash="xyz")
    ce.save_caption(store, video_a, "#alpha")
    ce.save_caption(store, video_b, "#beta #gamma")
    assert store.list_video_hashtags(video_a.id) == ["#alpha"]
    assert store.list_video_hashtags(video_b.id) == ["#beta", "#gamma"]


def test_delete_video_removes_its_hashtags(store):
    video = ce.save_caption(store, _video(store), "#gone")
    store.delete_video(video.id)
    assert store.list_video_hashtags(video.id) == []
