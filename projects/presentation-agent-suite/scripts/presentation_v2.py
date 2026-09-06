#!/usr/bin/env python3
"""Inspect and operate local v2 runs through the same transactional engine.

Usage: presentation_v2.py status RUN_ID; presentation_v2.py events RUN_ID --after 0
AI execution uses presentation_console.py; this CLI never impersonates a web approval.
"""
import argparse
import json
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE / "src"))
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.contracts import uid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "status", "progress", "events", "validate", "run", "cancel", "resume", "request-changes", "import-legacy"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("--data-dir", type=Path, default=SUITE / ".runtime/live")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--stage", choices=["intent", "research", "design"])
    parser.add_argument("--reason")
    parser.add_argument("--operation-id")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    engine = Engine(args.data_dir)
    if args.command == "list":
        result = [{"id": s["id"], "title": s["title"], "status": s["status"], "percent": s["progress"]["percent"]} for s in engine.list()]
    elif args.command == "import-legacy":
        project = Path(args.target or "").resolve()
        if not project.is_relative_to(SUITE.parents[1] / "projects"):
            parser.error("legacy project must be inside SlideMaster/projects")
        result = engine.register_legacy(project, args.operation_id or uid("op"))
    else:
        snapshot = engine.snapshot(args.target)
        if args.command in {"status", "progress", "validate", "events"}:
            result = {"status": snapshot, "progress": snapshot["progress"], "validate": {"findings": snapshot["findings"], "status": snapshot["status"]}, "events": engine.events(args.target, args.after)}[args.command]
        else:
            if args.expected_revision is None or not args.operation_id:
                parser.error("mutations require --expected-revision and --operation-id")
            command = args.command.replace("-", "_")
            result = engine.command(args.target, command, {"stage": args.stage, "reason": args.reason} if command == "request_changes" else {}, args.operation_id, args.expected_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
