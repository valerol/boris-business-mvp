from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from boris_mvp.app import create_app, load_state, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="boris", description="BORIS Business MVP")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Start the local BORIS desktop-style UI")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--no-browser", action="store_true", help="Do not open a browser window")

    ask = sub.add_parser("ask", help="Debug mode: run one task in the terminal")
    ask.add_argument("text", help="Natural-language request")

    sub.add_parser("status", help="Print current JSON state")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()

    if args.command == "run":
        app = create_app(workspace)
        url = f"http://{args.host}:{args.port}"
        if not args.no_browser:
            threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
        print(f"BORIS Business MVP running at {url}")
        print(f"Workspace: {workspace}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if args.command == "ask":
        state = run_pipeline(args.text, workspace)
        print(state.result or "No result produced.")
        if state.stop_events:
            print("\nStop events:")
            for event in state.stop_events:
                print(f"- {event.layer}: {event.reason}")
        print(f"\nState saved to {workspace / 'state.json'}")
        return 0

    if args.command == "status":
        print(load_state(workspace).model_dump_json(indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _open_browser(url: str) -> None:
    time.sleep(0.8)
    webbrowser.open(url)


if __name__ == "__main__":
    raise SystemExit(main())
