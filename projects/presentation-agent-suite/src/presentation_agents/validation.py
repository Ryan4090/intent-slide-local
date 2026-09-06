#!/usr/bin/env python3
"""
Presentation Agent Suite - Agent Manifest Validator

Validates three distinct agent identities, ownership boundaries, and prompts.

Usage:
    Import validate_agent_manifests or run the suite validator.

Dependencies:
    None (only uses standard library).
"""

from __future__ import annotations

import json
from pathlib import Path


_EXPECTED_IDS = {
    "intent-architect",
    "research-analyst",
    "presentation-designer",
}
_REQUIRED_KEYS = {
    "schema_version",
    "agent_id",
    "display_name",
    "order",
    "mission",
    "allowed_inputs",
    "owned_outputs",
    "forbidden_actions",
    "blocking_gates",
    "handoff",
}
_PROMPT_MARKERS = {
    "intent-architect": ["사용자 확인", "presentation_blueprint.md", "추정값을 확정하지 말라"],
    "research-analyst": ["source_manifest.json", "analysis.pdf", "design_guidelines.md"],
    "presentation-designer": ["routing.md", "verify_deck.py", "프레젠테이션 디자인"],
}
_EXPECTED_OWNED_OUTPUTS = {
    "intent-architect": {
        "agent_pipeline/01_intent/intent_contract.json",
        "agent_pipeline/01_intent/story_outline.json",
        "agent_pipeline/01_intent/presentation_blueprint.md",
        "agent_pipeline/01_intent/questions.json",
    },
    "research-analyst": {
        "agent_pipeline/02_research/research_plan.md",
        "agent_pipeline/02_research/text/",
        "agent_pipeline/02_research/media/",
        "agent_pipeline/02_research/source_manifest.json",
        "agent_pipeline/02_research/questions.json",
        "agent_pipeline/02_research/analysis.md",
        "agent_pipeline/02_research/analysis.pdf",
        "agent_pipeline/02_research/design_guidelines.md",
        "agent_pipeline/02_research/research_receipt.json",
    },
    "presentation-designer": {
        "agent_pipeline/03_design/SLIDEMASTER_HANDOFF.md",
        "agent_pipeline/03_design/verification_candidate.json",
        "design_spec.md",
        "spec_lock.md",
        "svg_output/",
        "svg_final/",
        "images/",
        "icons/",
        "exports/",
    },
}
_EXPECTED_USER_OUTPUTS = {
    "intent-architect": {
        "agent_pipeline/01_intent/approval.json",
        "agent_pipeline/01_intent/user_answers.json",
    },
    "research-analyst": {"agent_pipeline/02_research/user_answers.json"},
    "presentation-designer": {
        "agent_pipeline/03_design/design_approval.json",
        "agent_pipeline/03_design/verification.json",
    },
}


def validate_agent_manifests(agents_root: Path) -> list[str]:
    """Return manifest and prompt contract findings."""
    findings: list[str] = []
    manifests: list[dict[str, object]] = []
    if not agents_root.is_dir():
        return [f"agents directory missing: {agents_root}"]

    for manifest_path in sorted(agents_root.glob("*/agent.json")):
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(f"invalid manifest {manifest_path}: {exc}")
            continue
        if not isinstance(value, dict):
            findings.append(f"manifest must be an object: {manifest_path}")
            continue
        missing = sorted(_REQUIRED_KEYS - set(value))
        if missing:
            findings.append(f"manifest missing {missing}: {manifest_path}")
            continue
        for key in ("schema_version", "agent_id", "display_name", "mission", "handoff"):
            if not isinstance(value.get(key), str) or not str(value.get(key)).strip():
                findings.append(f"manifest {key} must be a non-empty string: {manifest_path}")
        if type(value.get("order")) is not int:
            findings.append(f"manifest order must be an integer: {manifest_path}")
        for key in ("allowed_inputs", "owned_outputs", "forbidden_actions", "blocking_gates"):
            raw_list = value.get(key)
            if not isinstance(raw_list, list) or not raw_list:
                findings.append(f"manifest {key} must be a non-empty array: {manifest_path}")
            elif any(not isinstance(item, str) or not item.strip() for item in raw_list):
                findings.append(f"manifest {key} entries must be non-empty strings: {manifest_path}")
        manifests.append(value)
        prompt_path = manifest_path.with_name("AGENT.md")
        if not prompt_path.is_file():
            findings.append(f"agent prompt missing: {prompt_path}")
            continue
        prompt = prompt_path.read_text(encoding="utf-8")
        agent_id = str(value["agent_id"])
        for marker in _PROMPT_MARKERS.get(agent_id, []):
            if marker not in prompt:
                findings.append(f"{agent_id} prompt missing marker: {marker}")

    ids = [str(item["agent_id"]) for item in manifests]
    if set(ids) != _EXPECTED_IDS or len(ids) != 3:
        findings.append(f"expected exactly three agent ids, found {sorted(ids)}")
    orders = [item["order"] for item in manifests if type(item.get("order")) is int]
    if sorted(orders) != [1, 2, 3]:
        findings.append(f"agent orders must be 1,2,3: {orders}")

    owned: dict[str, str] = {}
    for manifest in manifests:
        agent_id = str(manifest["agent_id"])
        raw_owned_outputs = manifest.get("owned_outputs")
        if not isinstance(raw_owned_outputs, list):
            findings.append(f"owned_outputs must be a list: {agent_id}")
            continue
        owned_tokens = {str(path) for path in raw_owned_outputs}
        expected_owned = _EXPECTED_OWNED_OUTPUTS.get(agent_id)
        if expected_owned is not None and owned_tokens != expected_owned:
            findings.append(f"owned_outputs do not match the agent contract: {agent_id}")
        for token in owned_tokens:
            if token in owned:
                findings.append(f"owned output collision: {token} ({owned[token]}, {agent_id})")
            owned[token] = agent_id
        if not isinstance(manifest.get("blocking_gates"), list) or not manifest["blocking_gates"]:
            findings.append(f"blocking_gates must not be empty: {agent_id}")
        if not isinstance(manifest.get("forbidden_actions"), list) or not manifest["forbidden_actions"]:
            findings.append(f"forbidden_actions must not be empty: {agent_id}")
        raw_user_outputs = manifest.get("user_controlled_outputs", [])
        if not isinstance(raw_user_outputs, list):
            findings.append(f"user_controlled_outputs must be a list: {agent_id}")
            user_outputs: set[str] = set()
        else:
            user_outputs = {str(path) for path in raw_user_outputs}
        expected_user_outputs = _EXPECTED_USER_OUTPUTS.get(agent_id)
        if expected_user_outputs is not None and user_outputs != expected_user_outputs:
            findings.append(f"user_controlled_outputs do not match the agent contract: {agent_id}")
        if agent_id == "intent-architect":
            approval_path = "agent_pipeline/01_intent/approval.json"
            if approval_path in owned_tokens:
                findings.append("intent approval must not be owned by the intent agent")
            if approval_path not in user_outputs:
                findings.append("intent approval must be declared user-controlled")
        if agent_id == "presentation-designer":
            controlled_paths = {
                "agent_pipeline/03_design/design_approval.json",
                "agent_pipeline/03_design/verification.json",
            }
            overlap = controlled_paths & owned_tokens
            if overlap:
                findings.append(
                    "designer must not own user-controlled receipts: " + ", ".join(sorted(overlap))
                )
            if not controlled_paths.issubset(user_outputs):
                findings.append("designer approvals must be declared user-controlled")
    return findings
