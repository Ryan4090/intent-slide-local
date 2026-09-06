"""Pure contracts shared by workers, the service, CLI and the local console."""
from __future__ import annotations

import copy
import ast
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    """Candidate cannot cross a workflow boundary."""


class Conflict(ContractError):
    """An operation was replayed differently or targets an old revision."""


STAGES = {"intent": "의도·구조 확정", "research": "리서치·분석", "design": "제작·검증·출고"}
WEIGHTS = {"intent": 20, "research": 35, "design": 45}
ROUTES = {
    "main-svg-generation": ".claude/skills/ppt-master/SKILL.md",
    "template-fill": ".claude/skills/ppt-template-fill/SKILL.md",
    "beautify": ".claude/skills/ppt-master/workflows/beautify-pptx.md",
    "native-enhance": ".claude/skills/native-enhance-pptx/SKILL.md",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        h = hashlib.file_digest(stream, "sha256")
    return h.hexdigest()


def required(value: Any, name: str) -> Any:
    if value is None or value == "" or value == [] or value == {}:
        raise ContractError(f"{name}: 값이 필요합니다")
    return value


def safe_path(root: Path, relative: str, *, exists: bool = True) -> Path:
    """Reject traversal and symlinks, including symlinked parent directories."""
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ContractError("workspace-relative file path required")
    result = root / raw
    cursor = root
    for part in raw.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ContractError("symlink file paths are not accepted")
    if not result.resolve().is_relative_to(root.resolve()):
        raise ContractError("file is outside workspace")
    if exists and not result.is_file():
        raise ContractError(f"file is missing: {relative}")
    return result


def normalize_intent(data: dict, source_mode: str, previous: dict | None = None) -> dict:
    result = copy.deepcopy(data)
    fields = result.get("fields", {})
    for key in ("topic", "audience", "objective", "success_criteria", "slide_count"):
        entry = fields.get(key)
        if not isinstance(entry, dict):
            raise ContractError(f"intent.fields.{key} is required")
        required(entry.get("value"), key)
        if entry.get("state") not in {"confirmed", "proposed"}:
            raise ContractError(f"{key}: resolve this decision before G1")
        required(entry.get("source"), f"{key}.source")
    count = fields["slide_count"]["value"]
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 200:
        raise ContractError("slide_count must be an integer from 1 to 200")
    slides = result.get("slides")
    if not isinstance(slides, list) or len(slides) != count:
        raise ContractError("slide count differs from intent")
    if any(q.get("blocking", True) for q in result.get("open_questions", [])):
        raise ContractError("blocking intent questions remain")
    requirements = []
    seen: set[str] = set()
    prior_ids = {s["uid"] for s in (previous or {}).get("slides", [])}
    for index, slide in enumerate(slides, 1):
        for key in ("title", "purpose", "content"):
            required(slide.get(key), f"slide {index}.{key}")
        slide.setdefault("uid", uid("slide"))
        if slide["uid"] in seen:
            raise ContractError("duplicate permanent slide identifier")
        seen.add(slide["uid"])
        slide["display_id"] = f"P{index:02d}"
        slide["order"] = index
        slide.setdefault("role", "context")
        needs = slide.get("evidence_needed", [])
        if not isinstance(needs, list):
            raise ContractError("evidence_needed must be a list, possibly empty")
        normalized = []
        for number, item in enumerate(needs, 1):
            item = {"question": item} if isinstance(item, str) else copy.deepcopy(item)
            required(item.get("question"), "research question")
            item.setdefault("uid", uid("req"))
            if item["uid"] in seen:
                raise ContractError("duplicate research requirement identifier")
            seen.add(item["uid"])
            item["slide_uid"] = slide["uid"]
            item.setdefault("legacy_id", f"EVID-{slide.get('slide_id', slide['display_id'])}-{number:02d}" if slide.get("slide_id") else None)
            normalized.append(item)
            requirements.append(copy.deepcopy(item))
        slide["evidence_needed"] = normalized
    result.update(schema_version="2.0.0", source_mode=source_mode, requirements=requirements)
    return result


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name}: nonempty text required")
    return value


def _records(value: Any, name: str, key: str = "id") -> dict[str, dict]:
    if not isinstance(value, list) or len(value) > 10000:
        raise ContractError(f"{name}: bounded record list required")
    result = {}
    for record in value:
        if not isinstance(record, dict):
            raise ContractError(f"{name}: every entry must be an object")
        identifier = _text(record.get(key), f"{name}.{key}")
        if len(identifier) > 200 or identifier in result:
            raise ContractError(f"{name}: duplicate or oversized identifier")
        result[identifier] = record
    return result


def _references(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(i, str) and i.strip() for i in value):
        raise ContractError(f"{name}: identifier list required")
    if len(value) != len(set(value)):
        raise ContractError(f"{name}: duplicate references")
    return value


def _explanation(value: Any, name: str) -> None:
    if isinstance(value, list) and value:
        for entry in value:
            _text(entry, name)
    else:
        _text(value, name)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name}: a finite JSON number is required")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ContractError(f"{name}: number is outside the supported range") from exc
    if not math.isfinite(number) or abs(number) > 1e100:
        raise ContractError(f"{name}: number is outside the finite supported range")
    return number


def _calculate(expression: str, bindings: dict[str, float]) -> float:
    """Evaluate a small arithmetic grammar; no Python eval, attributes or imports."""
    if len(expression) > 1024:
        raise ContractError("calculation expression exceeds 1,024 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ContractError("calculation expression is not valid arithmetic") from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ContractError("calculation expression exceeds 100 syntax nodes")
    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            value = visit(node.body)
        elif isinstance(node, ast.Constant):
            value = _number(node.value, "calculation constant")
        elif isinstance(node, ast.Name) and node.id in bindings:
            value = bindings[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                value = left / right
            else:
                if abs(right) > 100:
                    raise ContractError("calculation exponent exceeds 100")
                value = left ** right
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [visit(arg) for arg in node.args]
            if node.func.id in {"min", "max"} and 1 <= len(args) <= 20:
                value = min(args) if node.func.id == "min" else max(args)
            elif node.func.id == "abs" and len(args) == 1:
                value = abs(args[0])
            else:
                raise ContractError("only min, max and abs arithmetic calls are supported")
        else:
            raise ContractError("unsupported calculation syntax or unbound variable")
        return _number(value, "calculation result")
    try:
        return visit(tree)
    except (ZeroDivisionError, OverflowError, RecursionError) as exc:
        raise ContractError("calculation divides by zero or exceeds the arithmetic limit") from exc


def validate_research(data: dict, intent: dict, artifacts: list[dict], *, partial: bool = False, artifact_root: Path | None = None) -> dict:
    """Verify research structure, source excerpts, provenance, and arithmetic.

    SUPPORTED is a worker's semantic assessment. Each support receives a separate
    service-generated verification record; matching source text is never claimed
    to prove semantic entailment. G2 still reviews the claim and its limitations.
    """
    from .evidence import EvidenceReader, canonical_source_url

    if not isinstance(data, dict) or not isinstance(intent, dict) or not isinstance(partial, bool):
        raise ContractError("research and intent must be objects")
    result = copy.deepcopy(data)
    sources = _records(result.get("sources", []), "sources")
    claims = _records(result.get("claims", []), "claims")
    known_artifacts = _records(artifacts, "artifacts")
    reader = EvidenceReader(artifact_root, known_artifacts)
    source_mode = intent.get("source_mode")
    if not isinstance(source_mode, str) or source_mode not in {"provided_only", "external", "hybrid"}:
        raise ContractError("intent source_mode is invalid")
    for source in sources.values():
        source.pop("canonical_url", None)
        if not isinstance(source.get("origin"), str) or source["origin"] not in {"provided", "external"}:
            raise ContractError("source.origin must be provided or external")
        if source_mode == "provided_only" and source["origin"] != "provided":
            raise ContractError("external sources are forbidden in provided_only mode")
        if not isinstance(source.get("status", "SNAPSHOT"), str) or source.get("status", "SNAPSHOT") not in {"SNAPSHOT", "UNAVAILABLE"}:
            raise ContractError("source.status must be SNAPSHOT or UNAVAILABLE")
        if source.get("status", "SNAPSHOT") == "SNAPSHOT":
            artifact_id = _text(source.get("artifact_id"), "source.artifact_id")
            artifact = known_artifacts.get(artifact_id)
            if not artifact or artifact.get("valid") is not True or artifact.get("sha256") != source.get("sha256"):
                raise ContractError("source snapshot is missing or its hash differs")
            reader.verified_path(artifact_id)
            _text(source.get("title"), "source.title")
            accessed_at = _text(source.get("accessed_at"), "source.accessed_at")
            try:
                datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ContractError("source.accessed_at must be an ISO date or timestamp") from exc
            if source["origin"] == "provided" and not reader.provided_origin(artifact_id):
                raise ContractError("provided source must trace to a service-imported user attachment")
        else:
            _explanation(source.get("limitations"), "unavailable source limitations")
        if source.get("url") is not None:
            source["canonical_url"] = canonical_source_url(_text(source["url"], "source.url"))
        elif source["origin"] == "external" and source.get("status", "SNAPSHOT") == "SNAPSHOT":
            raise ContractError("external snapshot requires its original URL")
    for claim in claims.values():
        _text(claim.get("text"), "claim.text")
        status = claim.get("evidence_status")
        if not isinstance(status, str) or status not in {"SUPPORTED", "PARTIAL", "UNAVAILABLE", "ASSUMPTION", "PROVIDED", "PROPOSAL"}:
            raise ContractError("invalid claim evidence status")
        if not isinstance(claim.get("critical", True), bool):
            raise ContractError("claim.critical must be a boolean")
        if status in {"PARTIAL", "UNAVAILABLE", "ASSUMPTION"}:
            _explanation(claim.get("limitations"), "claim limitations")
        if claim.get("critical", True) and status in {"PARTIAL", "UNAVAILABLE"} and not partial:
            raise ContractError("critical unsupported claim must be resolved, qualified or removed")
        kind = claim.get("kind", "fact")
        # The initial v2 worker protocol used this spelling. Canonicalize its
        # exact synonym without relaxing any source or numeric verification.
        if kind == "factual":
            kind = claim["kind"] = "fact"
        if not isinstance(kind, str) or kind not in {"fact", "qualitative", "numeric", "derived", "assumption", "proposal", "provided"}:
            raise ContractError("invalid claim.kind")
        supports = claim.get("supports", [])
        if not isinstance(supports, list) or not all(isinstance(support, dict) for support in supports):
            raise ContractError("claim.supports must be a list of objects")
        if status in {"SUPPORTED", "PARTIAL", "PROVIDED"} and not supports and kind != "derived":
            raise ContractError("claim.supports is required for sourced claims")
        for support in supports:
            source_id = _text(support.get("source_id"), "support.source_id")
            source = sources.get(source_id)
            if not source or source.get("status", "SNAPSHOT") != "SNAPSHOT":
                raise ContractError("claim references an unknown or unavailable source")
            if status == "PROVIDED" and source["origin"] != "provided":
                raise ContractError("PROVIDED claims must cite user-provided sources")
            support["verification"] = reader.verify_support(source, support)
        # Never retain a worker-invented validation stamp.
        claim["verification"] = {"source_locations": "VERIFIED" if supports else "NOT_APPLICABLE",
                                 "semantic_entailment": "UNVERIFIED"}
        if kind in {"numeric", "derived"}:
            _number(claim.get("value"), "numeric claim.value")
            for key in ("unit", "population", "as_of"):
                _text(claim.get(key), f"numeric claim.{key}")
            as_of = claim["as_of"]
            if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", as_of):
                raise ContractError("numeric claim.as_of must use YYYY, YYYY-MM or YYYY-MM-DD")
            try:
                datetime.fromisoformat(as_of + ("-01-01" if len(as_of) == 4 else "-01" if len(as_of) == 7 else ""))
            except ValueError as exc:
                raise ContractError("numeric claim.as_of is not a valid calendar date") from exc
        if kind == "numeric" and status in {"SUPPORTED", "PARTIAL", "PROVIDED"}:
            literal = _text(claim.get("value_text"), "numeric claim.value_text (exact source number)")
            numeric_literal = literal.replace("−", "-")
            if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?", numeric_literal):
                raise ContractError("numeric value_text must be a source number without unit or percent suffix")
            try:
                matches = Decimal(numeric_literal.replace(",", "")) == Decimal(str(claim["value"]))
            except InvalidOperation as exc:
                raise ContractError("numeric value_text is not a supported number") from exc
            if not matches:
                raise ContractError("numeric value differs from its exact source number; express conversions as derived claims")
            pattern = re.compile(r"(?<![0-9A-Za-z.,])" + re.escape(literal) + r"(?![0-9A-Za-z]|[.,][0-9])")
            if not any(pattern.search(support["excerpt"]) for support in supports):
                raise ContractError("numeric source value_text is absent from the verified excerpt")
            claim["verification"]["source_value"] = "VERIFIED"
        if kind == "derived":
            _text(claim.get("formula"), "derived claim.formula")
            inputs = _references(claim.get("input_claim_ids"), "derived claim.input_claim_ids")
            if not inputs or any(i not in claims or claims[i].get("kind") not in {"numeric", "derived"} for i in inputs):
                raise ContractError("derived inputs must reference existing numeric or derived claims")
            if status in {"SUPPORTED", "PROVIDED"} and any(claims[i].get("evidence_status") not in {"SUPPORTED", "PROVIDED"} for i in inputs):
                raise ContractError("a derived sourced claim cannot silently upgrade uncertain input claims")
    resolved: set[str] = set()
    visiting: set[str] = set()
    def verify_calculation(identifier: str) -> None:
        if identifier in resolved:
            return
        if identifier in visiting or len(visiting) >= 100:
            raise ContractError("derived claim graph contains a cycle or exceeds 100 levels")
        claim = claims[identifier]
        if claim.get("kind") != "derived":
            resolved.add(identifier)
            return
        visiting.add(identifier)
        for input_id in claim["input_claim_ids"]:
            verify_calculation(input_id)
        calculation = claim.get("calculation")
        if not isinstance(calculation, dict):
            raise ContractError("derived calculation requires expression and bindings")
        expression = _text(calculation.get("expression"), "calculation.expression")
        bindings = calculation.get("bindings")
        if not isinstance(bindings, dict) or not bindings or not all(
            isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,49}", key)
            and key not in {"min", "max", "abs"} and isinstance(value, str)
            for key, value in bindings.items()
        ):
            raise ContractError("calculation.bindings must map simple variable names to input claim ids")
        if set(bindings.values()) != set(claim["input_claim_ids"]):
            raise ContractError("calculation bindings must match the declared input claims")
        actual = _calculate(expression, {key: _number(claims[value].get("value"), "input claim.value") for key, value in bindings.items()})
        expected = _number(claim["value"], "derived claim.value")
        if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ContractError("derived claim value differs from the independently calculated result")
        claim["verification"]["calculation"] = {"status": "VERIFIED", "computed_value": actual}
        visiting.remove(identifier)
        resolved.add(identifier)
    for identifier in claims:
        verify_calculation(identifier)
    requirements = set(_records(intent.get("requirements", []), "intent.requirements", "uid"))
    packets = _records(result.get("packets", []), "packets", "requirement_uid")
    if set(packets) - requirements:
        raise ContractError("unknown research requirement")
    if not partial and set(packets) != requirements:
        raise ContractError("research requirements remain unprocessed")
    for packet in packets.values():
        if not isinstance(packet.get("work_status"), str) or packet["work_status"] not in {"DONE", "ACTIVE", "UNAVAILABLE"}:
            raise ContractError("invalid research work status")
        if not partial and packet["work_status"] == "ACTIVE":
            raise ContractError("active research work remains")
        ids = _references(packet.get("claim_ids", []), "packet.claim_ids")
        if any(c not in claims for c in ids):
            raise ContractError("research packet has undefined claims")
        if packet["work_status"] == "UNAVAILABLE":
            _explanation(packet.get("attempts"), "unavailable packet.attempts")
            _explanation(packet.get("limitations"), "unavailable packet.limitations")
        elif packet["work_status"] == "DONE" and not ids:
            raise ContractError("completed requirement must identify its claims")
    messages = _records(result.get("messages", []), "messages", "slide_uid")
    slide_ids = set(_records(intent.get("slides", []), "intent.slides", "uid"))
    if set(messages) - slide_ids:
        raise ContractError("research message references an unknown slide")
    for message in messages.values():
        _text(message.get("message"), "slide message")
        if any(c not in claims for c in _references(message.get("claim_ids", []), "message.claim_ids")):
            raise ContractError("slide message has undefined claim references")
    if not partial:
        if set(messages) != slide_ids:
            raise ContractError("every slide needs a research-to-design message")
        _explanation(result.get("analysis"), "analysis")
        _explanation(result.get("limitations"), "research limitations (use an explicit none-found statement)")
    result["verification"] = {"source_files": len({s.get("sha256") for s in sources.values() if s.get("status", "SNAPSHOT") == "SNAPSHOT"}),
                              "unique_urls": len({s["canonical_url"] for s in sources.values() if s.get("canonical_url")}),
                              "independent_sources": None, "independence_status": "UNVERIFIED"}
    result["schema_version"] = "2.0.0"
    return result


def make_units(intent: dict) -> list[dict]:
    units: list[dict] = []
    def add(key, label, stage, weight):
        units.append({"id": key, "label": label, "stage": stage, "weight": weight, "status": "PENDING", "artifact_ids": [], "reason": None})
    add("intent.contract", "의도 계약", "intent", 10)
    count = len(intent["slides"])
    for slide in intent["slides"]:
        add(f"outline.{slide['uid']}", f"{slide['display_id']} 구성", "intent", 5 / count)
    add("G1", "의도 승인", "intent", 5)
    needs = intent["requirements"]
    if needs:
        for need in needs:
            add(f"evidence.{need['uid']}", need["question"], "research", 20 / len(needs))
    else:
        add("research.scope", "제공 내용·조사 필요성 검토", "research", 20)
    add("research.analysis", "분석·페이지 메시지", "research", 10)
    add("G2", "연구 승인", "research", 5)
    add("design.direction", "디자인 방향", "design", 8)
    add("G3", "디자인 승인", "design", 2)
    for slide in intent["slides"]:
        add(f"page.{slide['uid']}", f"{slide['display_id']} 페이지" + ("·노트" if intent["fields"].get("speaker_notes", {}).get("value") else ""), "design", 20 / count)
    add("G4", "필수 자동 검사", "design", 10)
    add("G5", "독립 검토·출고", "design", 5)
    return units
