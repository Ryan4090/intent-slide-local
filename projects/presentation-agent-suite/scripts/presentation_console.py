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
import os
import webbrowser
from contextlib import nullcontext
from pathlib import Path
from http.client import HTTPConnection

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parents[1]
sys.path.insert(0, str(SUITE / "src"))

from presentation_agents.v2.engine import Engine
from presentation_agents.v2.runner import Runner
from presentation_agents.v2.server import create_app
from presentation_agents.v2.service import ServiceLease, QueueWatch
from presentation_agents.v2.contracts import ContractError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--provider", choices=("auto", "codex"), default="codex")
    parser.add_argument("--data-dir", type=Path, default=SUITE / ".runtime" / "live")
    parser.add_argument("--no-runner", action="store_true", help="read/review local results without starting AI jobs")
    parser.add_argument("--preflight", action="store_true", help="compatibility flag; Codex connection is always checked on startup")
    parser.add_argument("--open", action="store_true", help="open the authenticated local page when ready")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    data_dir = args.data_dir.resolve()
    if not data_dir.is_relative_to(REPO):
        parser.error("data-dir must be inside the Intent-Slide repository")
    try:
        with ServiceLease(data_dir):
            serve(args, data_dir)
    except ContractError as exc:
        if args.open and reopen_existing(data_dir):
            return
        parser.exit(2, f"Intent-Slide: {exc}\n")


def reopen_existing(data_dir: Path) -> bool:
    """Reopen only the exact local instance owned by this data directory."""
    pointer = data_dir / 'local-page.json'
    try:
        if pointer.is_symlink() or pointer.stat().st_size > 2048:
            return False
        data = json.loads(pointer.read_text(encoding='utf-8'))
        port, instance = data['port'], data['instance_id']
        if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(instance, str):
            return False
        url = f'http://127.0.0.1:{port}'
        connection = HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/healthz')
            response = connection.getresponse()
            if response.status != 200:
                return False
            health = json.loads(response.read(2048))
        finally:
            connection.close()
        if health != {'service':'intent-slide', 'instance_id':instance}:
            return False
        webbrowser.open(url, new=2)
        return True
    except (KeyError, OSError, ValueError, TypeError):
        return False


def serve(args, data_dir):
    engine = Engine(data_dir)
    engine.recover()
    runner = None if args.no_runner else Runner(engine, REPO, default_provider=args.provider)
    token = secrets.token_urlsafe(32)
    instance = secrets.token_hex(16)
    app = create_app(engine, SUITE / "console", runner, bootstrap_token=token, instance_id=instance)
    from werkzeug.serving import make_server
    server = None
    for port in range(args.port, min(args.port + 10, 65536)):
        try:
            server = make_server('127.0.0.1', port, app, threaded=True)
            break
        except (OSError, SystemExit):
            continue
    if server is None:
        if runner:
            runner.close()
        raise ContractError('사용할 로컬 포트를 찾지 못했습니다. --port로 다른 포트를 선택하세요.')
    url = f"http://127.0.0.1:{server.server_port}/#session={token}"
    pointer = data_dir / 'local-page.json'
    if pointer.is_symlink():
        server.server_close()
        raise ContractError('로컬 페이지 기록은 실제 파일이어야 합니다')
    pointer.write_text(json.dumps({'port':server.server_port, 'instance_id':instance}), encoding='utf-8')
    print(json.dumps({"url": url, "data_dir": str(data_dir)}, ensure_ascii=False), flush=True)
    if runner:
        runner.discover()
    if args.open:
        # The socket has been bound; the queued navigation is served by the loop below.
        webbrowser.open(url, new=2)
    try:
        with QueueWatch(runner) if runner else nullcontext():
            server.serve_forever()
    finally:
        server.server_close()
        pointer.unlink(missing_ok=True)
        if runner:
            runner.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    main()
