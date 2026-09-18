"""
cli/tiktok_auth.py — Thin CLI entry point for TikTok OAuth authorization
(Milestone 3.0's package refactor thinned this from the original
standalone tiktok_auth.py).

All actual logic (authorize_interactive, start_manual_authorization,
complete_manual_authorization, get_access_token, refresh_access_token,
and everything else) lives in content_automation.publishing.tiktok.auth
— this file only parses arguments and reports the result.

Run:
  python3 cli/tiktok_auth.py --authorize
  python3 cli/tiktok_auth.py --print-auth-url
  python3 cli/tiktok_auth.py --exchange-code <code> --state <state>
"""

import argparse
import sys

from content_automation.publishing.tiktok.auth import (
    TIKTOK_REDIRECT_URI,
    TIKTOK_TOKEN_PATH,
    TikTokAuthError,
    authorize_interactive,
    complete_manual_authorization,
    start_manual_authorization,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TikTok OAuth authorization for the dedicated test account.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--authorize", action="store_true",
        help="Run the full interactive flow: PKCE + localhost callback (preferred).",
    )
    group.add_argument(
        "--print-auth-url", action="store_true",
        help="Manual fallback step 1: print the URL and cache a pending PKCE/state pair.",
    )
    group.add_argument(
        "--exchange-code", metavar="CODE",
        help="Manual fallback step 2: exchange the code (requires --state) for tokens.",
    )
    parser.add_argument(
        "--state", metavar="STATE",
        help="The `state` query parameter from the redirect URL — required with --exchange-code.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.authorize:
            authorize_interactive()
            print(f"\nTikTok authorization complete. Token cached at {TIKTOK_TOKEN_PATH}")
        elif args.print_auth_url:
            url = start_manual_authorization()
            print("Open this URL in a browser, log in as the dedicated TikTok test account, and approve:\n")
            print(url)
            print(f"\nAfter approving, note the `code` and `state` query parameters from the redirect to {TIKTOK_REDIRECT_URI}")
            print("and run: python3 cli/tiktok_auth.py --exchange-code <code> --state <state>")
        else:
            if not args.state:
                print("ERROR: --state is required with --exchange-code (copy it from the redirect URL).", file=sys.stderr)
                sys.exit(1)
            complete_manual_authorization(args.exchange_code, args.state)
            print(f"TikTok authorization complete. Token cached at {TIKTOK_TOKEN_PATH}")
    except TikTokAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
