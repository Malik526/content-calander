"""
run_api.py — thin runner for the FastAPI app (Milestone 3.6). Matches this
repository's "cli/*.py is argument parsing/wiring only" convention — all
real logic lives in content_automation.api.app.

Run:
  python3 cli/run_api.py
  python3 cli/run_api.py --port 8080 --reload
  python3 cli/run_api.py --host 0.0.0.0  # e.g. Railway (Milestone 3.6.1) —
                                          # --port still defaults from $PORT
                                          # if set, so it's optional there too
"""

import argparse
import os

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Pickle Batch API (development server).")
    # Defaults to 0.0.0.0 whenever $PORT is set (Railway, and hosting
    # platforms generally, set it; local dev never does) — same "correct
    # even without an explicit flag" reasoning as --port below, since a
    # deploy's actual start command isn't guaranteed to be exactly what
    # railway.json declares (e.g. a dashboard-configured start command
    # overrides it) and binding to 127.0.0.1 in a hosted container makes
    # the app unreachable from outside it.
    parser.add_argument("--host", default="0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    # Defaults to the hosting platform's assigned $PORT (Railway sets this)
    # when present, else the local-dev default — so a deploy's start
    # command works even without an explicit --port flag.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--reload", action="store_true", help="Auto-reload on source changes (development only).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("content_automation.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
