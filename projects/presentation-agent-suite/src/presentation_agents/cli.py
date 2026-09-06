#!/usr/bin/env python3
"""
Presentation Agent Suite - Command Line Interface

Runs the state machine and artifact validators for three isolated AI agents.

Usage:
    python -m presentation_agents.cli <command> [options]

Dependencies:
    PyMuPDF only for complete-research.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from presentation_agents.pipeline import AgentPipeline, ContractError


_SUITE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_AGENT_PROMPTS = {
    "intent-architect": _SUITE_ROOT / "agents/01_intent_architect/AGENT.md",
    "research-analyst": _SUITE_ROOT / "agents/02_research_analyst/AGENT.md",
    "presentation-designer": _SUITE_ROOT / "agents/03_presentation_designer/AGENT.md",
}


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _print(value: Any) -> None:
    if isinstance(value, Path):
        print(value.as_posix())
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _pipeline(args: argparse.Namespace) -> AgentPipeline:
    if (Path(args.workspace) / "run.json").is_file():
        raise ContractError("v2 작업입니다. run/resume/cancel/request-changes와 웹 검토함을 사용하세요")
    return AgentPipeline(Path(args.workspace))


def _v2_reference(workspace: str):
    from presentation_agents.v2.engine import Engine
    project = Path(workspace).resolve()
    metadata = _json_object(project / "run.json")
    if metadata.get("schema_version") != "2.0.0":
        raise ValueError("unsupported run.json schema_version")
    for key in ("state_store", "run_id"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise ValueError(f"run.json {key} must be non-empty text")
    if not Path(metadata["state_store"]).is_absolute():
        raise ValueError("v2 state_store must be an absolute path")
    store = Path(metadata["state_store"]).resolve()
    if not store.is_relative_to(_REPO_ROOT):
        raise ValueError("v2 state_store must remain inside SlideMaster")
    if (store / "control.sqlite").is_symlink():
        raise ValueError("v2 control.sqlite must not be a symlink")
    if not (store / "control.sqlite").is_file():
        raise ValueError("v2 state_store does not contain an existing control.sqlite")
    return Engine(store), metadata["run_id"]


def _cmd_v2(args: argparse.Namespace) -> object:
    engine, run_id = _v2_reference(args.workspace)
    if args.command == "progress":
        return engine.snapshot(run_id)["progress"]
    if args.command == "events":
        return engine.events(run_id, args.after)
    payload = {"stage": args.stage, "reason": args.reason, "artifact_ids": args.artifact_id} if args.command == "request-changes" else {}
    return engine.command(run_id, args.command.replace("-", "_"), payload, args.operation_id, args.expected_revision)


def _cmd_init(args: argparse.Namespace) -> object:
    request = Path(args.request_file).read_text(encoding="utf-8") if args.request_file else args.request
    if not request:
        raise ValueError("--request or --request-file is required")
    pipeline = AgentPipeline.create(
        projects_root=Path(args.projects_root),
        slug=args.slug,
        request=request,
        canvas_format=args.canvas,
        date_prefix=args.date,
    )
    return {
        "workspace": pipeline.workspace.as_posix(),
        "status": pipeline.state["status"],
        "current_agent": pipeline.current_agent_id,
    }


def _cmd_status(args: argparse.Namespace) -> object:
    if (Path(args.workspace) / "run.json").is_file():
        engine, run_id = _v2_reference(args.workspace)
        return engine.snapshot(run_id)
    pipeline = _pipeline(args)
    return {
        "workspace": pipeline.workspace.as_posix(),
        "status": pipeline.state["status"],
        "current_agent": pipeline.current_agent_id,
        "findings": pipeline.validate(),
    }


def _cmd_prompt(args: argparse.Namespace) -> str:
    if (Path(args.workspace) / "run.json").is_file():
        from presentation_agents.v2.prompts import prompt_for
        engine, run_id = _v2_reference(args.workspace)
        return prompt_for(engine.snapshot(run_id)["active_phase"], _REPO_ROOT)
    pipeline = _pipeline(args)
    agent_id = pipeline.current_agent_id
    prompt = _AGENT_PROMPTS[agent_id].read_text(encoding="utf-8")
    return (
        f"Workspace: `{pipeline.workspace.as_posix()}`\n"
        f"Current state: `{pipeline.state['status']}`\n"
        f"Active agent id: `{agent_id}`\n\n"
        "Work in a fresh AI context. Read only the allowed inputs and write only the owned outputs.\n\n"
        + prompt
    )


def _cmd_record_intent(args: argparse.Namespace) -> object:
    return _pipeline(args).record_intent(
        _json_object(Path(args.intent_json)),
        _json_object(Path(args.outline_json)),
    )


def _cmd_approve_intent(args: argparse.Namespace) -> object:
    return _pipeline(args).approve_intent(args.note, args.decision)


def _cmd_start_research(args: argparse.Namespace) -> object:
    pipeline = _pipeline(args)
    pipeline.start_research()
    return pipeline.state


def _cmd_raise_question(args: argparse.Namespace) -> object:
    return _pipeline(args).raise_question(
        stage=args.stage,
        question=args.question,
        impact=args.impact,
    )


def _cmd_answer_question(args: argparse.Namespace) -> object:
    return _pipeline(args).answer_question(args.question_id, args.answer)


def _cmd_add_text(args: argparse.Namespace) -> object:
    return _pipeline(args).add_text_source(
        source_id=args.source_id,
        title=args.title,
        url=args.url,
        markdown_file=Path(args.file),
        license_status=args.license,
        evidence_locator=args.locator,
    )


def _cmd_add_media(args: argparse.Namespace) -> object:
    return _pipeline(args).add_media_source(
        source_id=args.source_id,
        title=args.title,
        url=args.url,
        media_file=Path(args.file),
        license_status=args.license,
        evidence_locator=args.locator,
    )


def _cmd_add_blocked(args: argparse.Namespace) -> object:
    return _pipeline(args).add_blocked_source(
        source_id=args.source_id,
        title=args.title,
        url=args.url,
        resource_type=args.resource_type,
        license_status=args.license,
        evidence_locator=args.locator,
        failure_reason=args.reason,
    )


def _cmd_complete_research(args: argparse.Namespace) -> object:
    return _pipeline(args).complete_research(Path(args.analysis), Path(args.guidelines))


def _cmd_reopen_research(args: argparse.Namespace) -> object:
    return _pipeline(args).reopen_research(args.note)


def _cmd_request_research_approval(args: argparse.Namespace) -> object:
    return _pipeline(args).request_research_handoff_approval(
        args.question,
        args.impact,
    )


def _cmd_answer_research_approval(args: argparse.Namespace) -> object:
    return _pipeline(args).answer_research_handoff_question(
        args.question_id,
        args.answer,
    )


def _cmd_approve_research_handoff(args: argparse.Namespace) -> object:
    return _pipeline(args).approve_research_handoff(args.note, args.decision)


def _cmd_prepare_design(args: argparse.Namespace) -> object:
    return _pipeline(args).prepare_design()


def _cmd_approve_design(args: argparse.Namespace) -> object:
    return _pipeline(args).approve_design(args.note, args.decision)


def _cmd_verify_design(args: argparse.Namespace) -> object:
    return _pipeline(args).verify_design()


def _cmd_complete_design(args: argparse.Namespace) -> object:
    return _pipeline(args).complete_design(
        contact_sheet_review=args.contact_sheet_review,
        review_note=args.review_note,
    )


def _cmd_reopen_design(args: argparse.Namespace) -> object:
    return _pipeline(args).reopen_design(args.note)


def _cmd_request_design_changes(args: argparse.Namespace) -> object:
    return _pipeline(args).request_design_changes(args.note)


def _cmd_validate(args: argparse.Namespace) -> object:
    if (Path(args.workspace) / "run.json").is_file():
        engine, run_id = _v2_reference(args.workspace)
        snapshot = engine.snapshot(run_id)
        if snapshot["findings"]:
            raise ContractError("; ".join(f["message"] for f in snapshot["findings"]))
        return {"workspace": args.workspace, "status": snapshot["status"], "findings": []}
    pipeline = _pipeline(args)
    findings = pipeline.validate()
    if findings:
        raise ContractError("; ".join(findings))
    return {
        "workspace": pipeline.workspace.as_posix(),
        "status": pipeline.state["status"],
        "current_agent": pipeline.current_agent_id,
        "findings": [],
    }


def _workspace_parser(subparsers, name: str, help_text: str):
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("workspace", help="Presentation workspace path")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the three-agent presentation pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a new presentation workspace")
    init.add_argument("--slug", required=True, help="Lowercase workspace slug")
    init.add_argument("--request", help="Initial user request")
    init.add_argument("--request-file", help="UTF-8 file containing the initial request")
    init.add_argument("--canvas", choices=["ppt169", "ppt43"], default="ppt169")
    init.add_argument("--date", help="Fixed YYYYMMDD prefix; defaults to today")
    init.add_argument(
        "--projects-root",
        default=str(_REPO_ROOT / "projects"),
        help="Workspace parent directory",
    )
    init.set_defaults(handler=_cmd_init)

    status = _workspace_parser(subparsers, "status", "Show active agent and findings")
    status.set_defaults(handler=_cmd_status)
    prompt = _workspace_parser(subparsers, "prompt", "Print the active agent prompt")
    prompt.set_defaults(handler=_cmd_prompt)

    record = _workspace_parser(subparsers, "record-intent", "Validate Agent 1 outputs")
    record.add_argument("--intent-json", required=True)
    record.add_argument("--outline-json", required=True)
    record.set_defaults(handler=_cmd_record_intent)

    approve = _workspace_parser(subparsers, "approve-intent", "Hash-lock explicit user approval")
    approve.add_argument("--decision", choices=["APPROVE"], required=True)
    approve.add_argument("--note", required=True, help="Concise paraphrase of the user's explicit approval")
    approve.set_defaults(handler=_cmd_approve_intent, supervisor_only=True)

    start = _workspace_parser(subparsers, "start-research", "Activate Agent 2")
    start.set_defaults(handler=_cmd_start_research)

    question = _workspace_parser(subparsers, "raise-question", "Pause for a user decision")
    question.add_argument("--stage", choices=["intent", "research"], required=True)
    question.add_argument("--question", required=True)
    question.add_argument("--impact", required=True)
    question.set_defaults(handler=_cmd_raise_question)

    answer = _workspace_parser(subparsers, "answer-question", "Record a user answer")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--answer", required=True)
    answer.set_defaults(handler=_cmd_answer_question, supervisor_only=True)

    for command_name, handler, help_text in (
        ("add-text-source", _cmd_add_text, "Register a normalized Markdown snapshot"),
        ("add-media-source", _cmd_add_media, "Register an original-format media snapshot"),
    ):
        source = _workspace_parser(subparsers, command_name, help_text)
        source.add_argument("--source-id", required=True)
        source.add_argument("--title", required=True)
        source.add_argument("--url", required=True)
        source.add_argument("--file", required=True)
        source.add_argument("--license", required=True)
        source.add_argument("--locator", required=True)
        source.set_defaults(handler=handler)

    blocked = _workspace_parser(
        subparsers,
        "add-blocked-source",
        "Record an inaccessible source and its failure reason",
    )
    blocked.add_argument("--source-id", required=True)
    blocked.add_argument("--title", required=True)
    blocked.add_argument("--url", required=True)
    blocked.add_argument("--resource-type", choices=["text", "media"], required=True)
    blocked.add_argument("--license", default="UNKNOWN")
    blocked.add_argument("--locator", required=True)
    blocked.add_argument("--reason", required=True)
    blocked.set_defaults(handler=_cmd_add_blocked)

    research = _workspace_parser(subparsers, "complete-research", "Render and lock Agent 2 outputs")
    research.add_argument("--analysis", required=True, help="Analysis Markdown path")
    research.add_argument("--guidelines", required=True, help="Design guidelines Markdown path")
    research.set_defaults(handler=_cmd_complete_research)

    reopen_research = _workspace_parser(
        subparsers,
        "reopen-research",
        "Return a completed research package to Agent 2 for remediation",
    )
    reopen_research.add_argument(
        "--note",
        required=True,
        help="Independent-review finding or other bounded remediation reason",
    )
    reopen_research.set_defaults(handler=_cmd_reopen_research, supervisor_only=True)

    request_research_approval = _workspace_parser(
        subparsers,
        "request-research-approval",
        "Ask for user approval bound to the completed research package",
    )
    request_research_approval.add_argument("--question", required=True)
    request_research_approval.add_argument("--impact", required=True)
    request_research_approval.set_defaults(
        handler=_cmd_request_research_approval,
        supervisor_only=True,
    )

    answer_research_approval = _workspace_parser(
        subparsers,
        "answer-research-approval",
        "Record the user's raw answer to the research handoff question",
    )
    answer_research_approval.add_argument("--question-id", required=True)
    answer_research_approval.add_argument("--answer", required=True)
    answer_research_approval.set_defaults(
        handler=_cmd_answer_research_approval,
        supervisor_only=True,
    )

    approve_research_handoff = _workspace_parser(
        subparsers,
        "approve-research-handoff",
        "Lock the user's explicit approval before Agent 3 activation",
    )
    approve_research_handoff.add_argument(
        "--decision",
        choices=["APPROVE"],
        required=True,
    )
    approve_research_handoff.add_argument("--note", required=True)
    approve_research_handoff.set_defaults(
        handler=_cmd_approve_research_handoff,
        supervisor_only=True,
    )

    design = _workspace_parser(subparsers, "prepare-design", "Create Agent 3 handoff")
    design.set_defaults(handler=_cmd_prepare_design)

    approve_design = _workspace_parser(
        subparsers,
        "approve-design",
        "Lock the user-approved design direction",
    )
    approve_design.add_argument(
        "--decision",
        choices=["APPROVE"],
        required=True,
    )
    approve_design.add_argument(
        "--note",
        required=True,
        help="Concise paraphrase of the user's explicit design-direction approval",
    )
    approve_design.set_defaults(handler=_cmd_approve_design, supervisor_only=True)

    verify_design = _workspace_parser(
        subparsers,
        "verify-design",
        "Run verify_deck and render the contact sheet",
    )
    verify_design.set_defaults(handler=_cmd_verify_design)

    complete = _workspace_parser(
        subparsers,
        "complete-design",
        "Lock the final deck after contact-sheet review",
    )
    complete.add_argument("--contact-sheet-review", choices=["PASS", "FAIL"], required=True)
    complete.add_argument("--review-note", required=True)
    complete.set_defaults(handler=_cmd_complete_design, supervisor_only=True)

    reopen_design = _workspace_parser(
        subparsers,
        "reopen-design",
        "Archive a completed design receipt and reopen verification remediation",
    )
    reopen_design.add_argument("--note", required=True)
    reopen_design.set_defaults(handler=_cmd_reopen_design, supervisor_only=True)

    request_changes = _workspace_parser(
        subparsers,
        "request-design-changes",
        "Reject the current review candidate and preserve it for remediation",
    )
    request_changes.add_argument("--note", required=True)
    request_changes.set_defaults(handler=_cmd_request_design_changes, supervisor_only=True)

    validate = _workspace_parser(subparsers, "validate", "Fail on any contract drift")
    validate.set_defaults(handler=_cmd_validate)
    for name in ("progress", "events", "run", "cancel", "resume", "request-changes"):
        command = _workspace_parser(subparsers, name, "Operate a v2 run.json workspace through the shared engine")
        command.set_defaults(handler=_cmd_v2)
        if name == "events":
            command.add_argument("--after", type=int, default=0)
        elif name not in {"progress"}:
            command.add_argument("--operation-id", required=True)
            command.add_argument("--expected-revision", type=int, required=True)
            if name == "request-changes":
                command.add_argument("--stage", choices=["intent", "research", "design"], required=True)
                command.add_argument("--reason", required=True)
                command.add_argument("--artifact-id", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "supervisor_only", False) and os.environ.get(
        "PRESENTATION_SUITE_ACTOR"
    ) != "supervisor":
        print(
            "ERROR: this command is supervisor-only; use scripts/presentation_supervisor.py",
            file=sys.stderr,
        )
        return 1
    try:
        value = args.handler(args)
        if isinstance(value, str):
            print(value)
        else:
            _print(value)
        return 0
    except (ContractError, FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
