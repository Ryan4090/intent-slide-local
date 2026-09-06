#!/usr/bin/env python3
"""Run the local Intent-Slide console with the user's official AI CLI.

Usage: .venv/bin/python projects/presentation-agent-suite/scripts/presentation_console.py
The printed one-time loopback URL authenticates this browser. Never expose the port remotely.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from contextlib import nullcontext
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]
sys.path.insert(0, str(SUITE / "src"))

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app
from presentation_agents.v2.service import ServiceLease, QueueWatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--data-dir", type=Path, default=SUITE / ".runtime" / "live")
    parser.add_argument("--no-runner", action="store_true", help="read/review local results without starting AI jobs")
    parser.add_argument("--preflight", action="store_true", help="check the selected AI CLI before serving")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if not data_dir.is_relative_to(REPO):
        parser.error("data-dir must be inside the Intent-Slide repository")
    with ServiceLease(data_dir):
        serve(args, data_dir)


def serve(args, data_dir):
    engine = Engine(data_dir)
    engine.recover()
    runner = None if args.no_runner else Runner(engine, REPO, default_provider=args.provider)
    if runner and args.preflight:
        result = runner.preflight()
        print(json.dumps({"provider": args.provider, "ready": result.get("ready"), "reason": result.get("reason")}, ensure_ascii=False), flush=True)
    token = secrets.token_urlsafe(32)
    app = create_app(engine, SUITE / "console", runner, bootstrap_token=token)
    print(json.dumps({"url": f"http://127.0.0.1:{args.port}/#session={token}", "data_dir": str(data_dir)}, ensure_ascii=False), flush=True)
    try:
        with QueueWatch(runner) if runner else nullcontext():
            app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        if runner:
            runner.close()


if __name__ == "__main__":
    main()
