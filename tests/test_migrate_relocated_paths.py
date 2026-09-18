"""Tests for migrate_relocated_paths.py: the narrow, idempotent repair for
videos.canonical_media_path/original_path values left pointing at this
repository's old location after a move. Uses a real (temp-file)
ContentStore — this is precisely about what DB state results, not just
what the pure remap function returns in isolation."""

from pathlib import Path

import pytest

import migrate_relocated_paths as mrp
from content_automation.persistence.content_store import ContentStore

OLD_ROOT = "/home/malik/growth_agency/internal-tools/content-calendar"


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


# ---------------------------------------------------------------------------
# _remap_path — pure function
# ---------------------------------------------------------------------------

def test_remap_path_rewrites_path_rooted_under_old_root(monkeypatch):
    monkeypatch.setattr(mrp, "_NEW_ROOT", Path("/home/malik/content-automation"))
    value = f"{OLD_ROOT}/content/processed/video.mp4"
    new_value, changed = mrp._remap_path(value, OLD_ROOT)
    assert changed is True
    assert new_value == "/home/malik/content-automation/content/processed/video.mp4"


def test_remap_path_leaves_unrelated_absolute_path_untouched():
    new_value, changed = mrp._remap_path("/home/malik/growth_agency/credentials/service-account.json", OLD_ROOT)
    assert changed is False
    assert new_value == "/home/malik/growth_agency/credentials/service-account.json"


def test_remap_path_does_not_match_a_similarly_prefixed_sibling_directory():
    """A directory that merely starts with the same characters (but isn't
    actually old_root or a child of it) must never match — e.g. a sibling
    tool "content-calendar-archive" must not be treated as "content-calendar"."""
    value = "/home/malik/growth_agency/internal-tools/content-calendar-archive/file.mp4"
    new_value, changed = mrp._remap_path(value, OLD_ROOT)
    assert changed is False
    assert new_value == value


def test_remap_path_leaves_none_untouched():
    assert mrp._remap_path(None, OLD_ROOT) == (None, False)


def test_remap_path_leaves_empty_string_untouched():
    assert mrp._remap_path("", OLD_ROOT) == ("", False)


def test_remap_path_leaves_relative_path_untouched():
    new_value, changed = mrp._remap_path("content/incoming/video.mp4", OLD_ROOT)
    assert changed is False


def test_remap_path_handles_exact_root_match(monkeypatch):
    monkeypatch.setattr(mrp, "_NEW_ROOT", Path("/home/malik/content-automation"))
    new_value, changed = mrp._remap_path(OLD_ROOT, OLD_ROOT)
    assert changed is True
    assert new_value == "/home/malik/content-automation"


def test_remap_path_is_a_no_op_when_already_migrated(monkeypatch):
    monkeypatch.setattr(mrp, "_NEW_ROOT", Path("/home/malik/content-automation"))
    already_migrated = "/home/malik/content-automation/content/processed/video.mp4"
    new_value, changed = mrp._remap_path(already_migrated, OLD_ROOT)
    assert changed is False
    assert new_value == already_migrated


# ---------------------------------------------------------------------------
# repair_video_paths — full orchestration against a real ContentStore
# ---------------------------------------------------------------------------

def _insert_stale_video(store, tmp_path, video_id_hash, filename, create_processed_file=True):
    processed_dir = tmp_path / "content" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    if create_processed_file:
        (processed_dir / filename).write_bytes(b"fake video bytes")

    video = store.insert_video(
        video_id_hash, filename, f"{OLD_ROOT}/content/incoming/{filename}", "2026-01-01T00:00:00"
    )
    store.update_video(video.id, canonical_media_path=f"{OLD_ROOT}/content/processed/{filename}")
    return video


def test_repair_updates_both_columns_for_a_stale_row(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    video = _insert_stale_video(store, tmp_path, "h1", "video.mp4")

    report = mrp.repair_video_paths(store, OLD_ROOT)

    assert len(report) == 1
    assert report[0]["video_id"] == video.id
    assert report[0]["skipped"] == {}

    updated = store.get_video(video.id)
    assert updated.canonical_media_path == str(tmp_path / "content" / "processed" / "video.mp4")
    assert updated.original_path == str(tmp_path / "content" / "incoming" / "video.mp4")


def test_repair_preserves_all_other_video_fields(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    video = _insert_stale_video(store, tmp_path, "h1", "video.mp4")
    store.update_video(video.id, status="ASSIGNED", transcript="hello world", caption_text="hello world")

    mrp.repair_video_paths(store, OLD_ROOT)

    updated = store.get_video(video.id)
    assert updated.status == "ASSIGNED"
    assert updated.transcript == "hello world"
    assert updated.caption_text == "hello world"
    assert updated.file_hash == "h1"


def test_repair_preserves_content_slots_and_platform_posts_relationships(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    video = _insert_stale_video(store, tmp_path, "h1", "video.mp4")
    store.insert_slot_if_missing("2026-09-16T09:00:00", None, None, "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot_fifo("2000-01-01T00:00:00")
    store.assign_slot(video.id, slot.id)
    store.insert_platform_post(video.id, "tiktok", created_at="2026-01-02T00:00:00", scheduled_at=slot.scheduled_at)

    mrp.repair_video_paths(store, OLD_ROOT)

    updated_video = store.get_video(video.id)
    assert updated_video.assigned_slot_id == slot.id
    assert store.get_slot(slot.id).assigned_video_id == video.id
    record = store.get_platform_post(video.id, "tiktok")
    assert record is not None
    assert record.scheduled_at == "2026-09-16T09:00:00"
    assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repair_skips_canonical_media_path_when_target_file_missing(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    video = _insert_stale_video(store, tmp_path, "h1", "video.mp4", create_processed_file=False)

    report = mrp.repair_video_paths(store, OLD_ROOT)

    assert report[0]["updates"] == {"original_path": str(tmp_path / "content" / "incoming" / "video.mp4")}
    assert "canonical_media_path" in report[0]["skipped"]

    updated = store.get_video(video.id)
    assert updated.canonical_media_path == f"{OLD_ROOT}/content/processed/video.mp4"  # left untouched


def test_repair_does_not_touch_a_video_never_under_old_root(store, tmp_path):
    video = store.insert_video("h1", "video.mp4", "/some/other/place/video.mp4", "2026-01-01T00:00:00")
    store.update_video(video.id, canonical_media_path="/some/other/place/video.mp4")

    report = mrp.repair_video_paths(store, OLD_ROOT)

    assert report == []
    unchanged = store.get_video(video.id)
    assert unchanged.canonical_media_path == "/some/other/place/video.mp4"


def test_repair_is_idempotent_on_rerun(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    _insert_stale_video(store, tmp_path, "h1", "video.mp4")

    first_report = mrp.repair_video_paths(store, OLD_ROOT)
    second_report = mrp.repair_video_paths(store, OLD_ROOT)

    assert len(first_report) == 1
    assert second_report == []  # nothing left rooted under old_root


def test_dry_run_reports_without_writing(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    video = _insert_stale_video(store, tmp_path, "h1", "video.mp4")

    report = mrp.repair_video_paths(store, OLD_ROOT, dry_run=True)

    assert len(report) == 1
    assert report[0]["updates"]  # would have updated
    unchanged = store.get_video(video.id)
    assert unchanged.canonical_media_path == f"{OLD_ROOT}/content/processed/video.mp4"  # nothing written


def test_repair_handles_multiple_rows_independently(monkeypatch, store, tmp_path):
    monkeypatch.setattr(mrp, "_NEW_ROOT", tmp_path)
    _insert_stale_video(store, tmp_path, "h1", "video1.mp4")
    _insert_stale_video(store, tmp_path, "h2", "video2.mp4", create_processed_file=False)
    store.insert_video("h3", "video3.mp4", "/unrelated/path/video3.mp4", "2026-01-01T00:00:00")

    report = mrp.repair_video_paths(store, OLD_ROOT)

    by_video = {r["video_id"]: r for r in report}
    assert len(report) == 2  # h3 never touched at all
    v1 = store.get_video_by_hash("h1")
    v2 = store.get_video_by_hash("h2")
    v3 = store.get_video_by_hash("h3")
    assert v1.canonical_media_path == str(tmp_path / "content" / "processed" / "video1.mp4")
    assert v2.canonical_media_path == f"{OLD_ROOT}/content/processed/video2.mp4"  # skipped, file missing
    assert v3.canonical_media_path is None
