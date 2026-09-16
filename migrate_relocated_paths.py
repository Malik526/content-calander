"""
migrate_relocated_paths.py — One-time repair for absolute paths persisted
in data/content.db that still point at this repository's OLD location.

What it does:
  This repository moved from
      ~/growth_agency/internal-tools/content-calendar
  to
      ~/content-automation
  (renamed Content Calendar -> Content Automation in the process). Rows
  written before the move — videos.canonical_media_path and
  videos.original_path — still hold absolute paths rooted under the old
  location. This script rewrites only the paths that are actually rooted
  under --old-root to the equivalent path under this repository's current
  root (derived from this file's own location, never hardcoded), leaving
  every other value completely untouched — including paths that merely
  happen to contain the substring "content-calendar" elsewhere, and
  including a path already correct.

  Idempotent: a path not rooted under --old-root (e.g. already migrated,
  or never was) is left alone, so re-running this after a successful
  migration is a safe no-op. Never deletes or recreates the database —
  only UPDATEs the specific columns that actually needed remapping, on the
  specific rows that need it.

  canonical_media_path is verified to exist at its new location before
  being rewritten (skipped with a warning otherwise, so a partial/failed
  move never silently corrupts the DB with a pointer to nothing).
  original_path is remapped without that check: by the time a video is
  ASSIGNED, its original content/incoming/ file has already been moved to
  content/processed/ by design (see process_content.py) — original_path is
  a stable historical identity key (content_store.get_video_by_path), not
  a live file reference, so requiring it to still exist would be wrong.

Run:
  python3 migrate_relocated_paths.py --old-root ~/growth_agency/internal-tools/content-calendar
  python3 migrate_relocated_paths.py --old-root ... --dry-run
"""

import argparse
from pathlib import Path

from content_store import ContentStore

_NEW_ROOT = Path(__file__).resolve().parent
_REPAIRED_COLUMNS = ("canonical_media_path", "original_path")


def _remap_path(value: str | None, old_root: str) -> tuple[str | None, bool]:
    """(new_value, changed). Only rewrites a value that is exactly
    old_root or old_root + "/..." — every other value (None, relative,
    or absolute under some other directory) is returned unchanged."""
    if not value:
        return value, False
    old_root = old_root.rstrip("/")
    if value != old_root and not value.startswith(old_root + "/"):
        return value, False
    remainder = value[len(old_root):].lstrip("/")
    new_value = str(_NEW_ROOT / remainder) if remainder else str(_NEW_ROOT)
    return new_value, new_value != value


def repair_video_paths(store: ContentStore, old_root: str, *, dry_run: bool = False) -> list[dict]:
    """Returns one report dict per video row that needed a change:
    {"video_id", "updates": {column: new_value}, "skipped": {column: reason}}.
    Applies the update immediately (unless dry_run) for every column that
    passed its check; a column that fails its check (canonical_media_path
    missing at the new location) is reported under "skipped" and left
    untouched, independent of any other column on the same row."""
    report = []
    rows = store._conn.execute(
        "SELECT id, canonical_media_path, original_path FROM videos ORDER BY id"
    ).fetchall()

    for row in rows:
        updates: dict[str, str] = {}
        skipped: dict[str, str] = {}

        for column in _REPAIRED_COLUMNS:
            new_value, changed = _remap_path(row[column], old_root)
            if not changed:
                continue
            if column == "canonical_media_path" and not Path(new_value).exists():
                skipped[column] = f"new path does not exist: {new_value}"
                continue
            updates[column] = new_value

        if not updates and not skipped:
            continue

        if updates and not dry_run:
            store.update_video(row["id"], **updates)

        report.append({"video_id": row["id"], "updates": updates, "skipped": skipped})

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair videos.canonical_media_path/original_path values left pointing at this "
        "repository's old location after a move. Never touches any other absolute path."
    )
    parser.add_argument(
        "--old-root", required=True,
        help="The repository's previous absolute root directory, e.g. "
        "/home/malik/growth_agency/internal-tools/content-calendar",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing it.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    old_root = str(Path(args.old_root).expanduser())

    with ContentStore() as store:
        report = repair_video_paths(store, old_root, dry_run=args.dry_run)

    if not report:
        print(f"No paths rooted under {old_root} found — nothing to repair (already migrated, or nothing to do).")
        return

    changed = sum(1 for r in report if r["updates"])
    skipped = sum(1 for r in report if r["skipped"])
    verb = "Would repair" if args.dry_run else "Repaired"
    print(f"{verb} {changed} video row(s); {skipped} column(s) skipped (target missing).")
    for r in report:
        for column, new_value in r["updates"].items():
            print(f"  video {r['video_id']}: {column} -> {new_value}")
        for column, reason in r["skipped"].items():
            print(f"  video {r['video_id']}: {column} SKIPPED — {reason}")

    if args.dry_run:
        print("\nDry run: no changes written.")


if __name__ == "__main__":
    main()
