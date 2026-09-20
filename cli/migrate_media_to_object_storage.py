"""
migrate_media_to_object_storage.py — One-time migration: upload every
existing video's local canonical_media_path to object storage, verified
byte-for-byte via SHA-256, without deleting the original local file
(Milestone 3.4, Phase 21-23).

What it does:
  For every video row with a local canonical_media_path (the existing
  file still on disk) and no storage_provider yet (not already migrated):

    1. Compute the local file's SHA-256 (media.inspection.file_hash —
       reused, not reimplemented).
    2. media.media_storage.upload_canonical_media() — uploads to the
       configured object-storage backend (storage.factory.build_storage())
       and stamps storage_provider/storage_key on the row.
    3. Materialize the just-uploaded object back down and recompute its
       SHA-256 — verifies the destination is byte-for-byte identical to
       the source, not merely that the upload call returned success.

  Uses persistence.store_factory.build_content_store() and
  storage.factory.build_storage() — whichever backend is actually
  configured (DATABASE_URL / STORAGE_BACKEND), the same selection every
  other Milestone 3.3/3.4 entry point uses; this script does not hardcode
  either backend.

  NEVER deletes or modifies the original local file — see
  docs/decisions/0009-object-storage-media-lifecycle.md "Retention" for
  why keeping the local source indefinitely is this milestone's deliberate
  V1 choice, not an oversight. A video whose local file no longer exists
  on disk is reported and skipped, not treated as an error requiring
  manual intervention.

  Idempotent: a video that already has storage_provider set is skipped
  entirely (not re-uploaded, not re-verified) — re-running this script
  after a partial run only processes what's left.

Run:
  python3 cli/migrate_media_to_object_storage.py
  python3 cli/migrate_media_to_object_storage.py --dry-run
"""

import argparse
import hashlib
from pathlib import Path

from content_automation.media import media_storage
from content_automation.persistence.store_factory import build_content_store
from content_automation.storage.factory import build_storage


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_media(store, storage, *, dry_run: bool = False) -> list[dict]:
    """Returns one report dict per video considered:
    {"video_id", "outcome", ...}. outcome is one of "migrated" (or
    "would_migrate" in dry_run), "skipped_already_migrated",
    "skipped_no_local_file", "skipped_no_owner", or "hash_mismatch"
    (real, reportable failure — the upload+verify did not round-trip
    correctly; never silently ignored)."""
    report = []
    rows = store._conn.execute(
        "SELECT id, user_id, canonical_media_path, storage_provider FROM videos "
        "WHERE canonical_media_path IS NOT NULL ORDER BY id"
    ).fetchall()

    for row in rows:
        video_id, user_id, canonical_media_path, storage_provider = (
            row["id"], row["user_id"], row["canonical_media_path"], row["storage_provider"],
        )
        if storage_provider:
            report.append({"video_id": video_id, "outcome": "skipped_already_migrated"})
            continue
        if user_id is None:
            report.append({"video_id": video_id, "outcome": "skipped_no_owner"})
            continue

        local_path = Path(canonical_media_path)
        if not local_path.exists():
            report.append({"video_id": video_id, "outcome": "skipped_no_local_file", "path": canonical_media_path})
            continue

        source_hash = _sha256(local_path)

        if dry_run:
            report.append({"video_id": video_id, "outcome": "would_migrate", "sha256": source_hash})
            continue

        updated = media_storage.upload_canonical_media(store, storage, video_id, user_id)

        with storage.materialize(updated.storage_key) as materialized:
            dest_hash = _sha256(materialized)

        if dest_hash != source_hash:
            report.append({
                "video_id": video_id, "outcome": "hash_mismatch",
                "source_sha256": source_hash, "dest_sha256": dest_hash, "storage_key": updated.storage_key,
            })
            continue

        report.append({
            "video_id": video_id, "outcome": "migrated",
            "storage_provider": updated.storage_provider, "storage_key": updated.storage_key, "sha256": source_hash,
        })

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time migration: upload every existing video's local canonical media to object storage, "
        "verified byte-for-byte via SHA-256. Never deletes or modifies the original local file. Safe to re-run."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be migrated without uploading anything.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with build_content_store() as store:
        storage = build_storage()
        report = migrate_media(store, storage, dry_run=args.dry_run)

    counts: dict[str, int] = {}
    for entry in report:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    for outcome, count in counts.items():
        print(f"  {outcome}: {count}")

    mismatches = [e for e in report if e["outcome"] == "hash_mismatch"]
    if mismatches:
        print(f"\n{len(mismatches)} hash mismatch(es) — investigate before trusting these objects:")
        for m in mismatches:
            print(f"  video {m['video_id']}: source={m['source_sha256']} dest={m['dest_sha256']} key={m['storage_key']}")

    if args.dry_run:
        print("\nDry run: nothing uploaded.")


if __name__ == "__main__":
    main()
