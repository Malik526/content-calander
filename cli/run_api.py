"""
run_api.py — thin runner for the FastAPI app (Milestone 3.6). Matches this
repository's "cli/*.py is argument parsing/wiring only" convention — all
real logic lives in content_automation.api.app.

Run:
  python3 cli/run_api.py
  python3 cli/run_api.py --port 8080 --reload
"""

import argparse

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Pickle Batch API (development server).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on source changes (development only).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("content_automation.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
