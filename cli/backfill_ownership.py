"""
backfill_ownership.py — One-time backfill: attribute every pre-Milestone-3.2
videos/content_slots/platform_posts row (which predate the user_id column
entirely) to the single local bootstrap user, and bridge the existing local
TikTok credential file to a platform_connection row for that user.

What it does:
  Milestone 3.2 introduced `users`, `auth_identities`, `platform_connections`,
  and a nullable `user_id` column on videos/content_slots/platform_posts (see
  persistence/content_store.py's SCHEMA_USERS docstring for why nullable, not
  NOT NULL, at the SQLite level). Every row that existed before that
  migration has user_id = NULL. Going forward, every CLI entry point
  resolves/creates the local user itself (ContentStore.get_or_create_local_user)
  and passes it through — but that only stamps NEW rows; it never
  retroactively touches old ones. This script does that one-time retroactive
  attribution, exactly once, deterministically, and only for rows currently
  NULL:

    1. Resolve/create the local bootstrap user
       (ContentStore.get_or_create_local_user — idempotent).
    2. Resolve/create a "tiktok" platform_connection for that user,
       populating external_account_id from the existing cached TikTok token
       file's `open_id` if one is present (a local, non-network read —
       tiktok_auth.load_token(), never get_access_token(), so this script
       makes no TikTok API call and cannot trigger a token refresh).
    3. Backfill every videos/content_slots/platform_posts row with
       user_id IS NULL to the bootstrap user's id.

  Purely additive/backfill: never touches status, scheduled_at, published_at,
  platform_post_id, retry_count, next_retry_at, next_status_check_at,
  failure_reason, or any other existing column — the only values written are
  the new user_id foreign keys (plus, if this is the very first run, the new
  users/platform_connections rows themselves). Safe to re-run: every step is
  idempotent (get-or-create for the user/connection; the row backfill only
  ever matches WHERE user_id IS NULL, so a second run finds nothing left to
  do).

Run:
  python3 cli/backfill_ownership.py
  python3 cli/backfill_ownership.py --dry-run
"""

import argparse

from content_automation.persistence.content_store import LOCAL_BOOTSTRAP_USER_EMAIL, ContentStore
from content_automation.publishing.tiktok import auth as tiktok_auth


def backfill_ownership(store: ContentStore, *, dry_run: bool = False) -> dict:
    """Returns a report dict: {"user_id", "user_created", "connection_id",
    "connection_created", "videos_backfilled", "content_slots_backfilled",
    "platform_posts_backfilled"}. Applies immediately unless dry_run — in
    dry_run mode, no user/connection is created and no row is touched; the
    report instead reflects what *would* happen (using a placeholder-free
    count of currently-NULL rows, and reporting the user/connection as
    "would create" only if one doesn't already exist).
    """
    existing_user = store.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL)
    user_created = existing_user is None

    videos_null = store._conn.execute("SELECT COUNT(*) AS n FROM videos WHERE user_id IS NULL").fetchone()["n"]
    slots_null = store._conn.execute("SELECT COUNT(*) AS n FROM content_slots WHERE user_id IS NULL").fetchone()["n"]
    posts_null = store._conn.execute("SELECT COUNT(*) AS n FROM platform_posts WHERE user_id IS NULL").fetchone()["n"]

    if dry_run:
        existing_connection = existing_user and store.get_platform_connection(existing_user.id, "tiktok")
        return {
            "user_id": existing_user.id if existing_user else None,
            "user_created": user_created,
            "connection_id": existing_connection.id if existing_connection else None,
            "connection_created": existing_connection is None,
            "videos_backfilled": videos_null,
            "content_slots_backfilled": slots_null,
            "platform_posts_backfilled": posts_null,
        }

    user = store.get_or_create_local_user()

    cached_token = tiktok_auth.load_token()  # local file read only — no network call, no refresh
    external_account_id = cached_token.get("open_id") if cached_token else None
    connection_existed = store.get_platform_connection(user.id, "tiktok") is not None
    connection = store.get_or_create_platform_connection(user.id, "tiktok", external_account_id=external_account_id)

    store._conn.execute("UPDATE videos SET user_id = ? WHERE user_id IS NULL", (user.id,))
    store._conn.execute("UPDATE content_slots SET user_id = ? WHERE user_id IS NULL", (user.id,))
    store._conn.execute("UPDATE platform_posts SET user_id = ? WHERE user_id IS NULL", (user.id,))

    return {
        "user_id": user.id,
        "user_created": user_created,
        "connection_id": connection.id,
        "connection_created": not connection_existed,
        "videos_backfilled": videos_null,
        "content_slots_backfilled": slots_null,
        "platform_posts_backfilled": posts_null,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time backfill: attribute every pre-Milestone-3.2 videos/content_slots/platform_posts "
        "row to the local bootstrap user, and bridge the existing TikTok token file to a platform_connection. "
        "Never touches any column other than user_id. Safe to re-run."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing it.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with ContentStore() as store:
        report = backfill_ownership(store, dry_run=args.dry_run)

    verb = "Would attribute" if args.dry_run else "Attributed"
    print(f"Local user: id={report['user_id']} ({'would create' if report['user_created'] and args.dry_run else 'created' if report['user_created'] else 'already existed'})")
    print(f"TikTok platform_connection: id={report['connection_id']} ({'would create' if report['connection_created'] and args.dry_run else 'created' if report['connection_created'] else 'already existed'})")
    print(f"{verb} {report['videos_backfilled']} videos row(s), "
          f"{report['content_slots_backfilled']} content_slots row(s), "
          f"{report['platform_posts_backfilled']} platform_posts row(s).")

    if args.dry_run:
        print("\nDry run: no changes written.")


if __name__ == "__main__":
    main()
