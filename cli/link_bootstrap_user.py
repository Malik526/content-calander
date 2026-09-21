"""
link_bootstrap_user.py — One-time, explicit, verified link between a real
Supabase Auth identity and the existing local bootstrap user (Milestone
3.6).

What it does:
  Milestone 3.2's local bootstrap user (LOCAL_BOOTSTRAP_USER_EMAIL,
  "local@pickle-batch.local") owns every real pre-3.6 row: videos,
  content_slots, platform_posts, and the existing TikTok
  platform_connection. Milestone 3.6 introduces real Supabase Auth logins,
  each of which would otherwise resolve to a brand-new Pickle Batch account
  (identity.user_resolution.resolve_or_create_user's normal first-login
  behavior) — orphaning all of that real data under an account nobody can
  ever log into again.

  This script closes that gap for exactly the case where the operator's
  first real login IS the same person who has been using the bootstrap
  user all along: it creates one auth_identities row pointing
  (provider, provider_subject) at the EXISTING bootstrap user's id,
  instead of a new user. After this runs, logging in with that identity
  resolves to the bootstrap user (via the same get_user_by_auth_identity
  lookup user_resolution.py already uses) — no videos/content_slots/
  platform_posts/platform_connections row is touched or needs to be.

  This is a manual, explicit, one-off action — never automatic. Automatic
  first-login-becomes-bootstrap-user linking would let anyone who can
  reach the login flow silently claim ownership of the real bootstrap
  data; this script exists specifically so that decision is made once, by
  a human, with the real provider_subject already known (obtained by
  actually logging in once with Supabase Auth and reading the resulting
  JWT's `sub` claim, or via Supabase's dashboard), not inferred.

  Refuses (does not silently proceed) if:
    - the bootstrap user does not exist yet (nothing to link to — run
      any CLI entry point once first, or backfill_ownership.py).
    - (provider, provider_subject) is already linked to a DIFFERENT user
      (would silently reassign a real identity's login target).
    - the bootstrap user already has an auth_identity for this exact
      provider (re-running with the same provider_subject is a harmless
      no-op; a different provider_subject for an already-linked provider
      is refused rather than silently adding a second one).

Run:
  python3 cli/link_bootstrap_user.py --provider supabase --provider-subject <sub> [--provider-email <email>] --dry-run
  python3 cli/link_bootstrap_user.py --provider supabase --provider-subject <sub> [--provider-email <email>]
"""

import argparse
from datetime import datetime, timezone

from content_automation.persistence.content_store import LOCAL_BOOTSTRAP_USER_EMAIL, ContentStore


class BootstrapLinkError(Exception):
    """Refused — see the specific message for which guard tripped."""


def link_bootstrap_user(
    store: ContentStore, *, provider: str, provider_subject: str, provider_email: str | None,
    dry_run: bool = False,
) -> dict:
    """Returns a report dict: {"user_id", "already_linked", "action"} where
    action is one of "would_link"/"linked"/"already_linked_to_bootstrap".
    Raises BootstrapLinkError (never silently proceeds) if the bootstrap
    user doesn't exist yet, or if the identity/provider is already spoken
    for by something other than an identical no-op."""
    bootstrap_user = store.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL)
    if bootstrap_user is None:
        raise BootstrapLinkError(
            f"No bootstrap user ({LOCAL_BOOTSTRAP_USER_EMAIL}) exists yet — nothing to link to. "
            "Run any existing CLI entry point once (it calls get_or_create_local_user()), or "
            "cli/backfill_ownership.py, first."
        )

    existing_owner = store.get_user_by_auth_identity(provider, provider_subject)
    if existing_owner is not None:
        if existing_owner.id == bootstrap_user.id:
            return {"user_id": bootstrap_user.id, "already_linked": True, "action": "already_linked_to_bootstrap"}
        raise BootstrapLinkError(
            f"{provider}:{provider_subject} is already linked to a different user (id={existing_owner.id}), "
            f"not the bootstrap user (id={bootstrap_user.id}). Refusing to reassign an existing identity link."
        )

    # Bootstrap user already has SOME identity linked for this provider,
    # just not this provider_subject — refuse rather than silently
    # allowing a second, different identity to also claim the same
    # account under the same provider.
    row = store._conn.execute(
        "SELECT provider_subject FROM auth_identities WHERE user_id = ? AND provider = ?",
        (bootstrap_user.id, provider),
    ).fetchone()
    if row is not None:
        raise BootstrapLinkError(
            f"The bootstrap user already has a different {provider} identity linked "
            f"(provider_subject={row['provider_subject']!r}). Refusing to link a second one for the "
            "same provider — this script is for the one-time initial link only."
        )

    if dry_run:
        return {"user_id": bootstrap_user.id, "already_linked": False, "action": "would_link"}

    now = datetime.now(timezone.utc).isoformat()
    store.create_auth_identity(bootstrap_user.id, provider, provider_subject, provider_email, now)
    return {"user_id": bootstrap_user.id, "already_linked": False, "action": "linked"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time, explicit link between a real Supabase Auth identity and the existing local "
        "bootstrap user, so a real first login resolves to the account that already owns the real "
        "videos/content_slots/platform_posts/platform_connections rows, instead of creating a new, empty "
        "account. Never touches any row other than inserting one new auth_identities row."
    )
    parser.add_argument("--provider", required=True, help='e.g. "supabase" or "google".')
    parser.add_argument(
        "--provider-subject", required=True,
        help="The verified identity's stable subject (JWT 'sub' claim) — obtain by logging in once and "
        "reading the resulting token, or from the Supabase dashboard's Auth > Users table.",
    )
    parser.add_argument("--provider-email", default=None, help="Optional, recorded for observability only.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing it.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with ContentStore() as store:
        report = link_bootstrap_user(
            store,
            provider=args.provider,
            provider_subject=args.provider_subject,
            provider_email=args.provider_email,
            dry_run=args.dry_run,
        )

    if report["action"] == "already_linked_to_bootstrap":
        print(f"Already linked: {args.provider}:{args.provider_subject} -> bootstrap user id={report['user_id']}.")
    elif report["action"] == "would_link":
        print(f"Would link: {args.provider}:{args.provider_subject} -> bootstrap user id={report['user_id']}.")
        print("\nDry run: no changes written.")
    else:
        print(f"Linked: {args.provider}:{args.provider_subject} -> bootstrap user id={report['user_id']}.")


if __name__ == "__main__":
    main()
