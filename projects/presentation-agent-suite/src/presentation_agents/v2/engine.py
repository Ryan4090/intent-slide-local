"""Provider-independent orchestration and acceptance-based progress.

Only trusted service code calls publish/finish_job. Browser commands cannot mint
worker results, gate receipts or release records.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from .contracts import (
    STAGES, WEIGHTS, ROUTES, Conflict, ContractError, canonical, digest,
    file_hash, make_units, normalize_intent, now, required, safe_path, uid,
    validate_research,
)
from .store import Store
from .provider_registry import normalize_selection


class Engine:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.root = self.store.root

    def create(self, title: str, request: str, source_mode: str, operation_id: str, *, execution=None) -> dict:
        if source_mode not in {"provided_only", "external", "hybrid"}:
            raise ContractError("invalid source mode")
        for value, name in ((title, "title"), (request, "request"), (operation_id, "operation_id")):
            if not isinstance(value, str):
                raise ContractError(f"{name} must be text")
            required(value, name)
        run_id = uid("run")
        body = {"schema_version": "2.0.0", "id": run_id, "project_id": uid("project"),
                "title": title[:200], "request": request[:20000], "source_mode": source_mode,
                "revision": 1, "content_revision": 0, "plan_revision": 0,
                "status": "INTENT_INTERVIEW", "active_stage": "intent", "active_phase": "intent",
                "created_at": now(), "updated_at": now(), "intent": None, "research": None,
                "direction": None, "candidate": None, "release": None,
                "artifacts": [], "reviews": [], "approvals": [], "questions": [], "jobs": [],
                "units": [], "findings": [], "model": {}, "execution": normalize_selection(execution),
                "changes": [], "plan_history": [],
                "messages": [{"id": uid("msg"), "role": "user", "content": request[:20000], "stage": "intent"}]}
        return self._view(self.store.create(body, operation_id))

    def snapshot(self, run_id: str) -> dict:
        return self._view(self.store.read(run_id))

    def list(self) -> list[dict]:
        return [self._view(s) for s in self.store.list()]

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self.store.events(run_id, after)

    def _integrity(self, body: dict) -> set[str]:
        invalid = set()
        for artifact in body["artifacts"]:
            if not artifact["valid"]:
                invalid.add(artifact["id"])
                continue
            try:
                path = safe_path(self.root, artifact["path"])
                if file_hash(path) != artifact["sha256"]:
                    invalid.add(artifact["id"])
            except (ContractError, OSError):
                invalid.add(artifact["id"])
        if body.get("candidate"):
            from .verification import receipt_environment_matches
            if not receipt_environment_matches(Path(__file__).resolve().parents[5], body["candidate"]["receipt"]):
                invalid.update(a["id"] for a in body["artifacts"] if a["valid"] and a["kind"] == "gate_receipt")
        changed = True
        while changed:
            changed = False
            for artifact in body["artifacts"]:
                if artifact["id"] not in invalid and set(artifact.get("depends_on", [])) & invalid:
                    invalid.add(artifact["id"])
                    changed = True
        return invalid

    def _view(self, body: dict) -> dict:
        if body.get("legacy"):
            return self._legacy_view(body)
        result = copy.deepcopy(body)
        result.setdefault("execution", normalize_selection())
        invalid = self._integrity(result)
        known = {a["id"] for a in result["artifacts"]}
        for artifact in result["artifacts"]:
            artifact["valid"] = artifact["valid"] and artifact["id"] not in invalid
            artifact["download_url"] = f"/api/v2/runs/{body['id']}/artifacts/{artifact['id']}"
        active_invalid = [a["id"] for a in body["artifacts"] if a["valid"] and a["id"] in invalid]
        if active_invalid:
            result["findings"].append({"code": "ARTIFACT_INTEGRITY", "message": "입력 또는 산출물이 변경·유실되었습니다. 영향을 받은 결과를 다시 검토해야 합니다.", "artifact_ids": active_invalid})
            result["status"] = "STALE"
        for approval in result["approvals"]:
            if set(approval["artifact_ids"]) & invalid or not set(approval["artifact_ids"]) <= known:
                approval["valid"] = False
        expected_gates = []
        if body["active_stage"] in {"research", "design"} or any(u["id"] == "G1" and u["status"] == "VALID" for u in body["units"]):
            expected_gates.append("G1")
        if body.get("direction") or body.get("candidate") or body.get("release"):
            expected_gates.append("G2")
        if body.get("candidate") or body.get("release"):
            expected_gates.append("G3")
        missing_gates = [g for g in expected_gates if not any(a["gate"] == g and a["valid"] for a in result["approvals"])]
        if missing_gates and body["status"] not in {"INTENT_INTERVIEW", "INTENT_REVIEW", "RESEARCH_READY", "RESEARCH_REVIEW", "DESIGN_READY", "DESIGN_DIRECTION_REVIEW"}:
            result["status"] = "STALE"
            result["findings"].append({"code": "APPROVAL_INVALID", "message": "현재 유효한 승인 없음: " + ", ".join(missing_gates)})
        for unit in result["units"]:
            if unit["status"] == "VALID" and (set(unit["artifact_ids"]) & invalid or not set(unit["artifact_ids"]) <= known):
                unit.update(status="STALE", reason="의존 산출물이 변경·유실됨")
            if unit["id"].startswith("G") and unit["id"] in {"G1", "G2", "G3"}:
                if not any(a["gate"] == unit["id"] and a["valid"] for a in result["approvals"]):
                    if unit["status"] == "VALID":
                        unit.update(status="STALE", reason="현재 유효한 승인이 없음")
        stages = []
        progress_stages = []
        for stage, label in STAGES.items():
            units = [u for u in result["units"] if u["stage"] == stage]
            earned = sum(u["weight"] for u in units if u["status"] == "VALID")
            outputs = [a for a in result["artifacts"] if a["stage"] == stage and a["kind"] not in {"attachment", "user_request"}]
            input_ids = {i for a in outputs if a["valid"] for i in a.get("depends_on", [])}
            if not input_ids:
                input_ids = set(self._input_ids(result, stage))
            stages.append({"id": stage, "label": label, "status": "COMPLETE" if round(earned, 8) >= WEIGHTS[stage] else (result["status"] if result["active_stage"] == stage else "PENDING"),
                           "inputs": [a for a in result["artifacts"] if a["id"] in input_ids], "outputs": outputs,
                           "findings": result["findings"] if result["active_stage"] == stage else []})
            progress_stages.append({"id": stage, "label": label, "weight": WEIGHTS[stage], "earned": round(earned, 6), "total": WEIGHTS[stage], "percent": min(100, math.floor(round(100 * earned / WEIGHTS[stage], 7)))})
        earned = sum(u["weight"] for u in result["units"] if u["status"] == "VALID")
        percent = math.floor(round(earned, 7))
        release_valid = bool(result["release"] and result["status"] == "COMPLETE" and not active_invalid)
        if percent >= 100 and not release_valid:
            percent = 99
        result["stages"] = stages
        result["progress"] = {"percent": percent, "stages": progress_stages, "units": result["units"], "plan_revision": result["plan_revision"],
                              "reason": "완료 기준 계획 작성 전" if not result["units"] else ("변경 영향 재검토 필요" if active_invalid else "검증된 유효 완료 단위의 가중 합계")}
        for job in result["jobs"]:
            pages = job.get("execution", {}).get("pages")
            if not pages or pages.get("status") == "SUPERSEDED":
                continue
            try:
                checker_current = file_hash(Path(__file__).resolve().parents[5]
                                            / ".claude/skills/ppt-master/scripts/svg_quality_checker.py")
            except OSError:
                checker_current = None
            stale = (pages.get("input_hash") != self.input_hash(body)
                     or pages.get("checker_sha256") != checker_current
                     or not set(pages["artifact_ids"]) <= known
                     or bool(set(pages["artifact_ids"]) & invalid))
            if stale:
                pages.update(status="STALE", last_completed=pages["completed"], completed=0)
            elif job["status"] in {"CANCELLED", "INTERRUPTED"}:
                pages["status"] = job["status"]
        result["events"] = self.store.recent_events(body["id"])
        def elapsed(start, end=None):
            if not start:
                return 0.0
            return max(0.0, ((datetime.fromisoformat(end) if end else datetime.now(timezone.utc)) - datetime.fromisoformat(start)).total_seconds())
        execution = sum(max(0.0, elapsed(j.get("started_at"), j.get("finished_at")) - j.get("user_wait_seconds", 0) - (elapsed(j.get("wait_started_at"), j.get("finished_at")) if j.get("wait_started_at") else 0)) for j in body["jobs"])
        # Review intervals can overlap during an interview; union them rather than double count.
        intervals = []
        for item in body["reviews"] + body["questions"]:
            if not item.get("created_at"):
                continue
            finish = item.get("approved_at") or item.get("answered_at") or item.get("resolved_at")
            if item["status"] not in {"PENDING", "DISPATCHING"} and not finish:
                continue
            start = datetime.fromisoformat(item["created_at"]).timestamp()
            end = datetime.fromisoformat(finish).timestamp() if finish else datetime.now(timezone.utc).timestamp()
            intervals.append((start, max(start, end)))
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result["metrics"] = {"execution_seconds": round(execution, 1), "user_wait_seconds": round(sum(b - a for a, b in merged), 1), "definition": "실행 시도 경과에서 도구 응답 대기를 제외; 사용자 대기는 겹친 구간을 한 번만 계산"}
        return result

    def register_legacy(self, project: Path, operation_id: str) -> dict:
        """Register an existing archive without changing or upgrading its bytes."""
        state_path = safe_path(project, "agent_pipeline/state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        created = self.create(f"[기존 기록] {project.name}", "기존 작업 기록 읽기 전용 조회", "provided_only", operation_id)
        def apply(body):
            body["legacy"] = {"path": str(project.resolve()), "historical_status": state.get("status"), "historical_revision": state.get("revision"), "registered_at": now()}
            body["status"] = "LEGACY_READ_ONLY"
            return "기존 기록을 읽기 전용으로 연결했습니다"
        return self._view(self.store.mutate(created["id"], "legacy.registered", {}, apply))

    def _legacy_view(self, body: dict) -> dict:
        import re
        result = copy.deepcopy(body)
        project = Path(body["legacy"]["path"])
        findings = [{"code": "NO_APPROVED_PROGRESS_PLAN", "message": "당시 완료율 계산 계획이 없어 전체 진행률은 미계측입니다"}]
        plan = safe_path(project, "agent_pipeline/02_research/research_plan.md").read_text(encoding="utf-8")
        known = set(re.findall(r"EVID-P\d+-\d+", plan))
        used = set()
        for relative in ("spec_lock.md", "notes/total.md"):
            try:
                used.update(re.findall(r"EVID-P\d+-\d+", safe_path(project, relative).read_text(encoding="utf-8")))
            except ContractError:
                pass
        if used - known:
            findings.append({"code": "UNDEFINED_EVIDENCE", "message": "미정의 근거 참조: " + ", ".join(sorted(used - known))})
        result.update(status="LEGACY_READ_ONLY", findings=findings, stages=[{"id": s, "label": l, "status": "HISTORICAL", "inputs": [], "outputs": [], "findings": []} for s, l in STAGES.items()],
                      progress={"percent": None, "reason": "NO_APPROVED_PROGRESS_PLAN", "stages": [], "units": []}, events=self.store.recent_events(body["id"]))
        return result

    def _input_ids(self, body: dict, stage: str) -> list[str]:
        previous = {"intent": set(), "research": {"intent"}, "design": {"intent", "research"}}[stage]
        return [a["id"] for a in body["artifacts"] if a["valid"] and (a["stage"] in previous or a["kind"] in {"attachment", "user_request"})]

    def _ensure_user_sources(self, body: dict) -> None:
        """Snapshot service-recorded user statements, never worker-authored text.

        Deferred until research queueing so existing text-only runs can retry
        without migrating records or changing an already-approved G1 bundle.
        """
        for message in body["messages"]:
            if message.get("role") != "user":
                continue
            content = message["content"].encode("utf-8")
            expected = hashlib.sha256(content).hexdigest()
            prior = next((a for a in body["artifacts"] if a["kind"] == "user_request"
                          and a.get("provenance", {}).get("message_id") == message["id"]), None)
            if prior:
                if (not prior["valid"] or prior["sha256"] != expected
                        or file_hash(safe_path(self.root, prior["path"])) != expected):
                    raise ContractError("service user statement snapshot changed or is stale")
                continue
            artifact = self._artifact(body, f"user_request_{message['id']}.txt", content, "intent", "user_request")
            artifact["provenance"] = {"origin": "user-message", "message_id": message["id"],
                                      "recorded_at": now(), "fact_verification": "UNVERIFIED"}

    def _artifact(self, body: dict, name: str, content: bytes, stage: str, kind: str, depends_on=None) -> dict:
        artifact_id = uid("artifact")
        name = Path(name).name
        path = Path("runs") / body["id"] / "artifacts" / artifact_id / name
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        record = {"id": artifact_id, "run_id": body["id"], "name": name, "path": str(path),
                  "stage": stage, "kind": kind, "sha256": file_hash(target), "bytes": len(content),
                  "version": 1 + sum(a["name"] == name and a["stage"] == stage for a in body["artifacts"]),
                  "valid": True, "created_at": now(), "depends_on": depends_on or [],
                  "mime": mimetypes.guess_type(name)[0] or "application/octet-stream"}
        body["artifacts"].append(record)
        return record

    def add_attachment(self, run_id: str, name: str, content: bytes, operation_id: str, revision: int, *, provenance=None) -> dict:
        if not isinstance(operation_id, str) or not operation_id or type(revision) is not int:
            raise ContractError("operation_id and expected_revision are required")
        if not isinstance(name, str) or not isinstance(content, bytes):
            raise ContractError("attachment name and bytes are required")
        if len(content) > 100 * 1024 * 1024:
            raise ContractError("첨부 파일은 100 MB 이하만 지원합니다")
        if not Path(name).suffix.lower() in {".pdf", ".pptx", ".docx", ".xlsx", ".csv", ".tsv", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".hwpx"}:
            raise ContractError("지원되지 않는 첨부 형식입니다")
        def apply(body):
            if body.get("legacy"):
                raise ContractError("기존 기록은 읽기 전용입니다. 새 작업으로 시작해 주세요")
            if any(a["valid"] for a in body["approvals"]):
                self._invalidate(body, "research", "입력 자료 추가")
            artifact = self._artifact(body, name, content, "intent", "attachment")
            artifact["provenance"] = provenance or {"origin": "user-upload", "imported_at": now()}
            return f"자료를 첨부했습니다: {Path(name).name}"
        state = self.store.mutate(run_id, "attachment.added", {"name": name, "sha256": hashlib.sha256(content).hexdigest(), "provenance": provenance}, apply, operation_id=operation_id, expected_revision=revision)
        return self._view(state)

    def artifact_path(self, run_id: str, artifact_id: str) -> Path:
        body = self.store.read(run_id)
        artifact = next((a for a in body["artifacts"] if a["id"] == artifact_id and a["run_id"] == run_id), None)
        if not artifact:
            raise ContractError("이 작업의 산출물이 아닙니다")
        return safe_path(self.root, artifact["path"])

    def _accept(self, body: dict, unit_ids: list[str], artifact_ids: list[str]) -> None:
        for unit in body["units"]:
            if unit["id"] in unit_ids:
                unit.update(status="VALID", artifact_ids=artifact_ids, reason=None)

    def _review(self, body: dict, gate: str, stage: str, artifacts: list[str], summary: str):
        by_id = {a["id"]: a for a in body["artifacts"]}
        manifest = {i: by_id[i]["sha256"] for i in sorted(artifacts)}
        bundle = digest({"run_id": body["id"], "gate": gate, "artifacts": manifest})
        for review in body["reviews"]:
            if review["gate"] == gate and review["status"] == "PENDING":
                review.update(status="SUPERSEDED", resolved_at=now())
        body["reviews"].append({"id": uid("review"), "run_id": body["id"], "stage": stage, "gate": gate,
                                "title": {"G1": "의도·구조 확정", "G2": "리서치·분석 검토", "G3": "디자인 방향 검토"}[gate],
                                "summary": summary, "bundle_sha256": bundle, "manifest": manifest,
                                "artifact_ids": artifacts, "status": "PENDING", "created_at": now()})

    def _approved(self, body: dict, gate: str):
        invalid = self._integrity(body)
        matches = [a for a in body["approvals"] if a["gate"] == gate and a["valid"]]
        if not matches or set(matches[-1]["artifact_ids"]) & invalid:
            raise ContractError(f"{gate}: 현재 입력에 대한 유효한 사용자 승인이 필요합니다")
        approval = matches[-1]
        manifest = {a["id"]: a["sha256"] for a in body["artifacts"] if a["id"] in approval["artifact_ids"]}
        if not manifest or manifest != approval["manifest"] or digest({"run_id": body["id"], "gate": gate, "artifacts": manifest}) != approval["bundle_sha256"]:
            raise ContractError(f"{gate}: 승인 manifest가 현재 작업과 일치하지 않습니다")

    def _invalidate(self, body: dict, stage: str, reason: str, artifact_ids=None):
        affected = list(STAGES)[list(STAGES).index(stage):]
        selected = set(artifact_ids or [])
        if selected:
            known = {a["id"] for a in body["artifacts"] if a["valid"]}
            if not selected <= known:
                raise ContractError("변경 대상이 현재 작업의 유효한 산출물이 아닙니다")
            while True:
                expanded = selected | {a["id"] for a in body["artifacts"] if set(a.get("depends_on", [])) & selected}
                if expanded == selected:
                    break
                selected = expanded
        else:
            selected = {a["id"] for a in body["artifacts"] if a["stage"] in affected and a["kind"] not in {"attachment", "user_request"}}
        for artifact in body["artifacts"]:
            if artifact["id"] in selected:
                artifact["valid"] = False
        local_page_change = bool(artifact_ids) and stage == "design" and all(
            a["kind"] in {"page", "notes"} for a in body["artifacts"] if a["id"] in set(artifact_ids)
        )
        affected_gates = {"G1", "G2", "G3", "G4", "G5"} if stage == "intent" else ({"G2", "G3", "G4", "G5"} if stage == "research" else ({"G4", "G5"} if local_page_change else {"G3", "G4", "G5"}))
        for approval in body["approvals"]:
            if approval["gate"] in affected_gates:
                approval["valid"] = False
        for review in body["reviews"]:
            if review["gate"] in affected_gates and review["status"] == "PENDING":
                review.update(status="SUPERSEDED", resolved_at=now())
        for unit in body["units"]:
            if unit["id"] in affected_gates or set(unit["artifact_ids"]) & selected:
                unit.update(status="STALE", reason=reason)
        body["release"] = None
        body["candidate"] = None
        body["active_stage"] = stage
        body["active_phase"] = stage if stage != "design" else "design_direction"
        body["status"] = {"intent": "INTENT_INTERVIEW", "research": "RESEARCH_READY", "design": "DESIGN_READY"}[stage]
        if local_page_change:
            body.update(active_phase="design_build", status="REWORK")
        body["content_revision"] += 1
        body["changes"].append({"stage": stage, "reason": reason, "artifact_ids": sorted(selected), "created_at": now()})

    def command(self, run_id: str, command: str, payload: dict, operation_id: str, expected_revision: int) -> dict:
        if not isinstance(operation_id, str) or not operation_id or type(expected_revision) is not int or not isinstance(payload, dict):
            raise ContractError("operation_id and expected_revision are required")
        def apply(body):
            if body.get("legacy"):
                raise ContractError("기존 기록은 읽기 전용입니다. 새 작업으로 시작해 주세요")
            if command == "configure_provider":
                if any(j["status"] in {"QUEUED", "RUNNING", "WAITING_USER"} for j in body["jobs"]):
                    raise Conflict("실행을 취소하거나 마친 뒤 AI 도구를 변경해 주세요")
                selection = normalize_selection(payload)
                body["execution"] = selection
                return "다음 실행에 사용할 AI 도구와 모델을 저장했습니다"
            if command == "message":
                text = str(required(payload.get("content"), "message"))[:20000]
                body["messages"].append({"id": uid("msg"), "role": "user", "content": text, "stage": body["active_stage"]})
                if any(a["valid"] for a in body["approvals"]):
                    body.setdefault("pending_change", {"phase": body["active_phase"], "stage": body["active_stage"], "status": body["status"]})
                    body.update(active_phase="clarification", active_stage="intent", status="CHANGE_ASSESSMENT")
                return "사용자 메시지를 기록했습니다"
            if command == "approve":
                review = next((r for r in body["reviews"] if r["id"] == payload.get("review_id") and r["status"] == "PENDING"), None)
                if not review or review["bundle_sha256"] != payload.get("bundle_sha256"):
                    raise ContractError("현재 검토 대상과 승인이 일치하지 않습니다")
                invalid = self._integrity(body)
                if set(review["artifact_ids"]) & invalid:
                    raise ContractError("승인 대상 산출물이 변경되거나 유실되었습니다")
                if review["gate"] != "G1":
                    self._approved(body, "G1")
                if review["gate"] == "G3":
                    self._approved(body, "G2")
                review.update(status="APPROVED", approved_at=now())
                body["approvals"].append({**copy.deepcopy(review), "id": uid("approval"), "review_id": review["id"], "valid": True, "actor": "local-user", "approved_at": now(), "authorization_note": str(payload.get("authorization_note", "현재 검토 대상에 대한 직접 승인"))[:2000]})
                self._accept(body, [review["gate"]], review["artifact_ids"])
                transitions = {"G1": ("research", "research", "RESEARCH_READY"), "G2": ("design", "design_direction", "DESIGN_READY"), "G3": ("design", "design_build", "DESIGN_BUILD")}
                body["active_stage"], body["active_phase"], body["status"] = transitions[review["gate"]]
                body["content_revision"] += 1
                return f"{review['gate']} 현재 결과를 승인했습니다"
            if command == "request_changes":
                stage = payload.get("stage", body["active_stage"])
                if stage not in STAGES:
                    raise ContractError("unknown stage")
                reason = str(required(payload.get("reason"), "change reason"))
                self._invalidate(body, stage, reason, payload.get("artifact_ids"))
                return f"{STAGES[stage]} 수정 요청: {reason}"
            if command == "answer":
                question = next((q for q in body["questions"] if q["id"] == payload.get("question_id") and q["status"] == "PENDING" and q.get("provider_request_id") is None), None)
                if not question:
                    raise ContractError("답변할 질문을 찾을 수 없습니다")
                answer = str(required(payload.get("answer"), "answer"))[:20000]
                question.update(status="ANSWERED", answer=answer, answered_at=now())
                body["messages"].append({"id": uid("msg"), "role": "user", "content": answer, "stage": question["stage"]})
                body["status"] = "READY"
                return "답변을 기록했습니다"
            if command in {"run", "resume"}:
                if body["status"] == "COMPLETE":
                    raise ContractError("완료 결과를 바꾸려면 먼저 수정 요청을 남겨 주세요")
                if (body["active_phase"] != "clarification" and any(r["status"] == "PENDING" for r in body["reviews"])) or any(q["status"] == "PENDING" for q in body["questions"]):
                    raise ContractError("검토함의 승인 또는 질문 답변이 필요합니다")
                if any(j["status"] in {"QUEUED", "RUNNING", "WAITING_USER"} for j in body["jobs"]):
                    raise Conflict("이미 실행 중이거나 대기 중입니다")
                for gate in ({"intent": [], "research": ["G1"], "design": ["G1", "G2"]}[body["active_stage"]]):
                    self._approved(body, gate)
                if body["active_phase"] in {"design_build", "design_review"}:
                    self._approved(body, "G3")
                if body["active_phase"] == "research":
                    self._ensure_user_sources(body)
                body["jobs"].append({"id": uid("job"), "run_id": run_id, "phase": body["active_phase"], "stage": body["active_stage"], "attempt": len(body["jobs"]) + 1,
                                     "status": "QUEUED", "created_at": now(), "input_hash": self.input_hash(body),
                                     "provider_selection": normalize_selection(body.get("execution"))})
                body["findings"] = [f for f in body["findings"] if f.get("code") != "JOB_ERROR"]
                body["status"] = "QUEUED"
                return "실행을 대기열에 넣었습니다"
            if command == "cancel":
                for job in body["jobs"]:
                    if job["status"] in {"QUEUED", "RUNNING", "WAITING_USER"}:
                        job.update(status="CANCELLED", finished_at=now())
                for q in body["questions"]:
                    if q.get("provider_request_id") is not None and q["status"] in {"PENDING", "DISPATCHING"}:
                        q.update(status="CANCELLED", resolved_at=now())
                body["status"] = "CANCELLED"
                return "실행 취소를 요청했습니다"
            raise ContractError(f"지원하지 않는 명령: {command}")
        return self._view(self.store.mutate(run_id, f"command.{command}", payload, apply, operation_id=operation_id, expected_revision=expected_revision))

    def input_hash(self, body: dict) -> str:
        input_ids = set(self._input_ids(body, body["active_stage"]))
        if body["active_phase"] in {"design_build", "design_review"}:
            input_ids.update(a["id"] for a in body["artifacts"] if a["valid"] and a["kind"] in {"direction", "spec"})
        if body["active_phase"] == "design_review" and body["candidate"]:
            input_ids.update(body["candidate"]["artifact_ids"])
        return digest({"phase": body["active_phase"], "revision": body["content_revision"],
                       "artifacts": [(a["id"], a["sha256"]) for a in body["artifacts"] if a["valid"] and a["id"] in input_ids],
                       "user_messages": [m["content"] for m in body["messages"] if m["role"] == "user"]})

    def claim_job(self, run_id: str) -> dict:
        claimed = {}
        def apply(body):
            job = next((j for j in body["jobs"] if j["status"] == "QUEUED"), None)
            if not job:
                raise ContractError("queued job not found")
            job.update(status="RUNNING", started_at=now(), heartbeat_at=now())
            body["status"] = "RUNNING"
            claimed.update(job)
            return "AI 작업을 시작했습니다"
        self.store.mutate(run_id, "job.started", {}, apply)
        return claimed

    def recover(self):
        for run in self.store.list():
            if any(j["status"] in {"RUNNING", "WAITING_USER"} for j in run["jobs"]):
                def apply(body):
                    for job in body["jobs"]:
                        if job["status"] in {"RUNNING", "WAITING_USER"}:
                            job.update(status="INTERRUPTED", finished_at=now())
                    for q in body["questions"]:
                        if q.get("provider_request_id") is not None and q["status"] in {"PENDING", "DISPATCHING"}:
                            q.update(status="INTERRUPTED", resolved_at=now())
                    body["status"] = "INTERRUPTED"
                    return "서비스 중단을 감지했습니다. 기존 결과를 보존했습니다"
                self.store.mutate(run["id"], "job.interrupted", {}, apply)

    def publish(self, run_id: str, phase: str, data: dict, *, job_id: str | None = None, input_hash: str | None = None) -> dict:
        """Trusted candidate adoption. Never exposed as a browser command."""
        def apply(body):
            if job_id:
                job = next((j for j in body["jobs"] if j["id"] == job_id), None)
                if not job or job["status"] not in {"RUNNING", "WAITING_USER"} or self.input_hash(body) != input_hash:
                    raise Conflict("작업 중 입력이 변경되었습니다. 결과는 초안으로 보존합니다")
            stage = "design" if phase.startswith("design") else ("research" if phase.startswith("research") else "intent")
            if phase == "clarification":
                previous = body.pop("pending_change", None)
                if not previous or data.get("action") not in {"answer", "change"}:
                    raise ContractError("message change assessment is required")
                if data["action"] == "change":
                    target = data.get("stage")
                    if target not in STAGES:
                        raise ContractError("change target stage is required")
                    self._invalidate(body, target, str(required(data.get("reason"), "change reason")))
                else:
                    body.update(active_phase=previous["phase"], active_stage=previous["stage"], status=previous["status"])
                if data.get("message"):
                    body["messages"].append({"id": uid("msg"), "role": "assistant", "content": data["message"], "stage": body["active_stage"]})
            elif phase == "intent":
                if body["active_stage"] != "intent":
                    raise ContractError("의도를 바꾸려면 의도 단계로 수정 요청이 필요합니다")
                normalized = normalize_intent(data, body["source_mode"], body["intent"])
                old_units = copy.deepcopy(body["units"])
                self._invalidate(body, "intent", "새 의도 후보")
                body["intent"] = normalized
                body["plan_history"].append({"revision": body["plan_revision"], "units": old_units, "created_at": now()})
                body["plan_revision"] += 1
                body["units"] = make_units(normalized)
                artifact = self._artifact(body, "intent_contract.v2.json", canonical(normalized).encode(), stage, "intent", self._input_ids(body, stage))
                self._accept(body, [u["id"] for u in body["units"] if u["stage"] == "intent" and u["id"] != "G1"], [artifact["id"]])
                self._review(body, "G1", stage, [artifact["id"]] + self._input_ids(body, stage), f"{normalized['fields']['topic']['value']} · {len(normalized['slides'])}장 · 조사 요구 {len(normalized['requirements'])}개")
                body["status"] = "INTENT_REVIEW"
            elif phase in {"research", "research_checkpoint"}:
                self._approved(body, "G1")
                if body["active_stage"] != "research":
                    raise ContractError("research is not the active stage")
                normalized = validate_research(data, body["intent"], body["artifacts"], partial=phase.endswith("checkpoint"), artifact_root=self.root,
                                               user_messages=body["messages"])
                for a in body["artifacts"]:
                    if a["kind"] in {"research", "analysis_report", "analysis_pdf"} and a["valid"]:
                        a["valid"] = False
                artifact = self._artifact(body, "research_bundle.v2.json", canonical(normalized).encode(), stage, "research", self._input_ids(body, stage))
                body["research"] = normalized
                done = [f"evidence.{p['requirement_uid']}" for p in normalized.get("packets", []) if p["work_status"] in {"DONE", "UNAVAILABLE"}]
                if phase == "research":
                    from .reporting import research_markdown, markdown_pdf
                    markdown = research_markdown(normalized, body["intent"])
                    report = self._artifact(body, "analysis.md", markdown.encode("utf-8"), stage, "analysis_report", [artifact["id"]])
                    pdf = self._artifact(body, "analysis.pdf", markdown_pdf(markdown), stage, "analysis_pdf", [report["id"], artifact["id"]])
                    done += ["research.analysis", "research.scope"]
                    self._review(body, "G2", stage, [artifact["id"], report["id"], pdf["id"]] + self._input_ids(body, stage) + [s["artifact_id"] for s in normalized.get("sources", []) if s.get("artifact_id")], "분석 보고서·PDF와 주장별 근거·계산·한계·페이지 메시지를 확인해 주세요")
                    body["status"] = "RESEARCH_REVIEW"
                self._accept(body, done, [artifact["id"]])
                if phase == "research":
                    self._accept(body, ["research.analysis"], [artifact["id"], report["id"], pdf["id"]])
            elif phase == "design_direction":
                for gate in ("G1", "G2"):
                    self._approved(body, gate)
                if body["active_stage"] != "design" or body["active_phase"] != "design_direction":
                    raise ContractError("design direction is not the active phase")
                if data.get("route") not in ROUTES:
                    raise ContractError("a known design route is required")
                for key in ("summary", "design_spec", "spec_lock", "preview_artifact_ids"):
                    required(data.get(key), key)
                ids = {a["id"] for a in body["artifacts"] if a["valid"] and a["stage"] == "design"}
                if not set(data["preview_artifact_ids"]) <= ids:
                    raise ContractError("design preview is not a current artifact")
                body["direction"] = copy.deepcopy(data)
                artifact = self._artifact(body, "design_direction.v2.json", canonical(data).encode(), stage, "direction", self._input_ids(body, stage) + data["preview_artifact_ids"])
                self._accept(body, ["design.direction"], [artifact["id"]])
                self._review(body, "G3", stage, [artifact["id"]] + self._input_ids(body, stage) + data["preview_artifact_ids"], data["summary"])
                body["status"] = "DESIGN_DIRECTION_REVIEW"
            else:
                raise ContractError("publish supports intent, research, research_checkpoint, design_direction")
            if phase != "research_checkpoint":
                body["content_revision"] += 1
            if job_id and phase != "research_checkpoint":
                job.update(status="COMPLETED", finished_at=now())
            return f"{STAGES[stage]} 결과를 검증하고 검토함에 등록했습니다"
        return self._view(self.store.mutate(run_id, f"candidate.{phase}", {"sha256": digest(data)}, apply))

    def import_worker_artifacts(self, run_id: str, workspace: Path, descriptors: list[dict], stage: str, *, job_id=None, input_hash=None) -> dict[str, str]:
        """Copy only declared files; approval/control files cannot be promoted."""
        mappings = {}
        def apply(body):
            if job_id:
                job = next((j for j in body["jobs"] if j["id"] == job_id), None)
                if not job or job["status"] != "RUNNING" or self.input_hash(body) != input_hash:
                    raise Conflict("worker output targets an interrupted or stale attempt")
            for item in descriptors:
                key = str(required(item.get("key"), "artifact.key"))
                if key in mappings:
                    raise ContractError("duplicate worker artifact key")
                path = safe_path(workspace, str(item.get("path", "")))
                if path.stat().st_size > 100 * 1024 * 1024:
                    raise ContractError("worker artifact exceeds 100 MB")
                if item.get("kind", "source") not in {"source", "analysis", "preview", "page", "notes", "pptx", "contact_sheet", "spec", "review"}:
                    raise ContractError("worker cannot mint an approval or gate artifact")
                artifact = self._artifact(body, path.name, path.read_bytes(), stage, item.get("kind", "source"), self._input_ids(body, stage))
                artifact["accepted"] = False
                mappings[key] = artifact["id"]
            return f"작업 결과 파일 {len(mappings)}개를 초안으로 보존했습니다"
        if descriptors:
            self.store.mutate(run_id, "artifacts.imported", {"keys": [d.get("key") for d in descriptors]}, apply)
        return mappings

    def update_job(self, run_id: str, job_id: str, values: dict, message: str, *, event="job.updated"):
        def apply(body):
            job = next((j for j in body["jobs"] if j["id"] == job_id), None)
            if not job:
                raise ContractError("job not found")
            job.update(values)
            if values.get("status") in {"FAILED", "BLOCKED", "INTERRUPTED", "WAITING_USER"}:
                body["status"] = values["status"]
            if values.get("error"):
                body["findings"].append({"code": "JOB_ERROR", "message": values["error"], "job_id": job_id})
            return message
        return self.store.mutate(run_id, event, values, apply)

    def add_question(self, run_id: str, question: str, impact: str, *, provider_request_id=None, provider_params=None):
        def apply(body):
            body["questions"].append({"id": uid("question"), "stage": body["active_stage"], "question": question, "impact": impact, "status": "PENDING",
                                      "provider_request_id": provider_request_id, "provider_params": provider_params, "created_at": now()})
            body["status"] = "WAITING_USER"
            return question
        return self.store.mutate(run_id, "question.raised", {}, apply)

    def assistant_message(self, run_id: str, content: str):
        def apply(body):
            body["messages"].append({"id": uid("msg"), "role": "assistant", "content": content[:50000], "stage": body["active_stage"]})
            return "AI 응답을 기록했습니다"
        return self.store.mutate(run_id, "message.assistant", {}, apply)

    def accept_page_checkpoint(self, run_id: str, workspace: Path, data: dict, receipt: dict,
                               *, job_id: str, input_hash: str) -> dict:
        """Freeze inspected page drafts as execution history; never award gate/page units."""
        def apply(body):
            job = next((item for item in body["jobs"] if item["id"] == job_id), None)
            if (not job or job["status"] != "RUNNING" or job["phase"] != "design_build"
                    or body["active_phase"] != "design_build" or self.input_hash(body) != input_hash):
                raise Conflict("page checkpoint targets a stale or cancelled attempt")
            if workspace.resolve() != (self.root / "attempts" / job_id).resolve():
                raise ContractError("page checkpoint must come from its own isolated attempt")
            for gate in ("G1", "G2", "G3"):
                self._approved(body, gate)
            expected = [slide["uid"] for slide in body["intent"]["slides"]]
            pages = receipt.get("pages", [])
            if (receipt.get("schema_version") != "design-pages-check.v1" or not pages
                    or [page.get("slide_uid") for page in pages] != expected[:len(pages)]
                    or len(pages) > len(expected)
                    or receipt.get("data_sha256") != digest(data)):
                raise ContractError("page checkpoint does not match the inspected cumulative page prefix")
            if receipt.get("checker_sha256") != file_hash(Path(__file__).resolve().parents[5]
                                                         / ".claude/skills/ppt-master/scripts/svg_quality_checker.py"):
                raise ContractError("page checker changed after inspection")
            previous = job.get("execution", {}).get("pages", {})
            if len(pages) < previous.get("completed", 0):
                raise ContractError("page checkpoints must be cumulative within an attempt")
            manifest = receipt.get("input_sha256", {})
            if not isinstance(manifest, dict) or not manifest or len(manifest) > 1000:
                raise ContractError("page checkpoint needs a bounded inspected file manifest")
            contents = {}
            total_bytes = 0
            for relative, expected_hash in manifest.items():
                path = safe_path(workspace, relative)
                size = path.stat().st_size
                total_bytes += size
                if size > 100 * 1024 * 1024 or total_bytes > 256 * 1024 * 1024:
                    raise ContractError("page checkpoint file exceeds 100 MB")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise ContractError("page checkpoint input changed after inspection")
                contents[relative] = content
            for page in pages:
                if (manifest.get(page["path"]) != page["sha256"]
                        or (page.get("notes_path") and manifest.get(page["notes_path"]) != page["notes_sha256"])):
                    raise ContractError("inspected page files are missing from the manifest")
            dependencies = self._input_ids(body, "design") + [a["id"] for a in body["artifacts"]
                                                             if a["valid"] and a["kind"] == "direction"]
            invalid = self._integrity(body)
            existing = {(a.get("checkpoint_path"), a["sha256"]): a for a in body["artifacts"]
                        if a.get("checkpoint_job_id") == job_id and a["valid"] and a["id"] not in invalid}
            paths, records = {}, []
            for relative, content in contents.items():
                artifact = existing.get((relative, manifest[relative]))
                if artifact is None:
                    artifact = self._artifact(body, Path(relative).name, content, "design", "preview", dependencies)
                    artifact.update(accepted=False, checkpoint_job_id=job_id, checkpoint_path=relative,
                                    checkpoint_input_hash=input_hash)
                paths[relative] = artifact["id"]
            artifact_ids = list(paths.values())
            for artifact in body["artifacts"]:
                if artifact.get("checkpoint_job_id") == job_id and artifact["id"] not in artifact_ids:
                    artifact["valid"] = False
            for page in pages:
                record = {**page, "artifact_id": paths[page["path"]]}
                if page.get("notes_path"):
                    record["notes_artifact_id"] = paths[page["notes_path"]]
                records.append(record)
            job.setdefault("execution", {})["pages"] = {
                "schema_version": "design-pages.v1", "completed": len(pages), "total": len(expected),
                "slide_uids": [page["slide_uid"] for page in pages], "artifact_ids": artifact_ids,
                "page_records": records, "status": "CURRENT", "updated_at": now(),
                "input_hash": input_hash, "checkpoint_sha256": digest(receipt),
                "weighted_progress": False, "checker_sha256": receipt["checker_sha256"],
                "validation": "SVG_AND_REFERENCES_CHECKED",
                "limit": "작성 중 원본 검사입니다. 전체 PPTX 자동 검사·시각 검토 완료율에는 반영하지 않습니다.",
            }
            return f"슬라이드 작성 {len(pages)}/{len(expected)} · 원본 검사 통과, 최종 검증 전"
        return self._view(self.store.mutate(run_id, "design.pages_checkpoint", {"sha256": digest(receipt)}, apply))

    def accept_candidate(self, run_id: str, workspace: Path, data: dict, receipt: dict, *, job_id: str, input_hash: str):
        """Adopt only a candidate checked by the service-owned verifier adapter."""
        def apply(body):
            job = next((j for j in body["jobs"] if j["id"] == job_id), None)
            if not job or job["status"] != "RUNNING" or self.input_hash(body) != input_hash:
                raise Conflict("candidate targets a stale or cancelled job")
            for gate in ("G1", "G2", "G3"):
                self._approved(body, gate)
            if receipt.get("verdict") != "PASS" or receipt.get("route") != body["direction"]["route"]:
                raise ContractError("trusted verification did not pass the approved route")
            for relative, expected in receipt.get("input_sha256", {}).items():
                if expected is None:
                    if safe_path(workspace, relative, exists=False).exists():
                        raise ContractError("an absent verification input appeared after the gate")
                    continue
                if file_hash(safe_path(workspace, relative)) != expected:
                    raise ContractError("candidate input changed after verification")
            # Previous exports remain in the immutable history, but only the
            # replacement package participates in current integrity/progress.
            previous_outputs = {i for a in body["artifacts"] if a["kind"] == "gate_receipt" for i in a["depends_on"]}
            for artifact in body["artifacts"]:
                if artifact["id"] in previous_outputs or artifact["kind"] in {"gate_receipt", "release"} or (artifact["kind"] == "review" and set(artifact["depends_on"]) & previous_outputs):
                    artifact["valid"] = False
                if artifact.get("checkpoint_job_id"):
                    artifact["valid"] = False
            for previous_job in body["jobs"]:
                checkpoint = previous_job.get("execution", {}).get("pages")
                if checkpoint:
                    checkpoint.update(status="SUPERSEDED", superseded_at=now(), superseded_by_job_id=job_id)
            artifact_ids = []
            path_to_artifact = {}
            for item in receipt["artifacts"]:
                path = safe_path(workspace, item["path"])
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ContractError("candidate output changed after verification")
                artifact = self._artifact(body, path.name, content, "design", item["kind"], self._input_ids(body, "design") + [a["id"] for a in body["artifacts"] if a["valid"] and a["kind"] == "direction"])
                artifact_ids.append(artifact["id"])
                path_to_artifact[item["path"]] = artifact["id"]
            if receipt["pptx_path"] not in path_to_artifact or receipt["contact_sheet_path"] not in path_to_artifact:
                raise ContractError("verified PPTX and render must both be frozen")
            g4 = self._artifact(body, "g4_receipt.json", canonical(receipt).encode(), "design", "gate_receipt", artifact_ids)
            candidate = {"id": uid("candidate"), "run_id": run_id, "artifact_ids": artifact_ids + [g4["id"]],
                         "pptx_artifact_id": path_to_artifact[receipt["pptx_path"]], "contact_sheet_artifact_id": path_to_artifact[receipt["contact_sheet_path"]],
                         "pptx_sha256": receipt["pptx_sha256"], "contact_sheet_sha256": receipt["contact_sheet_sha256"],
                         "renderer": receipt["renderer"], "receipt": receipt, "created_at": now()}
            body["candidate"] = candidate
            for page in data["pages"]:
                page_files = [path_to_artifact[p] for p in (page["path"], page.get("notes_path")) if p in path_to_artifact]
                self._accept(body, [f"page.{page['slide_uid']}"], page_files or artifact_ids)
            self._accept(body, ["G4"], candidate["artifact_ids"])
            job.update(status="COMPLETED", finished_at=now())
            body.update(status="DESIGN_REVIEW", active_phase="design_review", active_stage="design")
            body["content_revision"] += 1
            return "명시적인 최종 후보의 자동 검사를 통과했습니다"
        return self._view(self.store.mutate(run_id, "candidate.verified", {"sha256": digest(receipt)}, apply))

    def accept_review(self, run_id: str, data: dict, observations: dict, *, job_id: str, input_hash: str):
        def apply(body):
            job = next((j for j in body["jobs"] if j["id"] == job_id), None)
            if not job or job["status"] != "RUNNING" or self.input_hash(body) != input_hash:
                raise Conflict("review targets a stale or cancelled job")
            candidate = body.get("candidate")
            if not candidate or data.get("candidate_sha256") != candidate["pptx_sha256"] or data.get("contact_sheet_sha256") != candidate["contact_sheet_sha256"]:
                raise ContractError("review and candidate hashes differ")
            if data.get("verdict") not in {"PASS", "FAIL"}:
                raise ContractError("review verdict must be PASS or FAIL")
            if set(candidate["artifact_ids"]) & self._integrity(body):
                raise ContractError("candidate integrity changed before final review")
            for gate in ("G1", "G2", "G3"):
                self._approved(body, gate)
            if data["verdict"] == "PASS":
                if set(data.get("reviewed_slide_uids", [])) != {s["uid"] for s in body["intent"]["slides"]}:
                    raise ContractError("review does not cover every page")
                if data.get("findings"):
                    raise ContractError("resolve final review findings before release")
                render = next(a for a in body["artifacts"] if a["id"] == candidate["contact_sheet_artifact_id"])
                viewed = observations.get("images_viewed", [])
                opened = {Path(path).name for path in viewed if isinstance(path, str)}
                if f"{render['id']}--{render['name']}" not in opened:
                    raise ContractError("independent reviewer did not open the candidate render")
                current_ids = set(candidate["artifact_ids"])
                page_renders = [a for a in body["artifacts"] if a["id"] in current_ids and a["kind"] == "render_page"]
                missing = [a["name"] for a in page_renders if f"{a['id']}--{a['name']}" not in opened]
                if missing:
                    raise ContractError("independent reviewer did not open every candidate page render: " + ", ".join(missing))
            report = self._artifact(body, "independent_review.json", canonical({**data, "observations": observations}).encode(), "design", "review", candidate["artifact_ids"])
            job.update(status="COMPLETED", finished_at=now())
            if data["verdict"] == "FAIL":
                body["changes"].append({"stage": "design", "reason": data.get("summary", "시각 검토 반려"), "candidate": candidate, "created_at": now()})
                for unit in body["units"]:
                    if unit["id"] in {"G4", "G5"}:
                        unit.update(status="STALE", reason="독립 검토 반려")
                body.update(status="REWORK", active_phase="design_build", candidate=None, release=None)
                return "최종 검토에서 수정할 사항을 발견했습니다"
            release = {"schema_version": "release.v2", "run_id": run_id, "candidate_id": candidate["id"],
                       "pptx_artifact_id": candidate["pptx_artifact_id"], "pptx_sha256": candidate["pptx_sha256"],
                       "review_artifact_id": report["id"], "g4_receipt": candidate["receipt"],
                       "approval_ids": [a["id"] for a in body["approvals"] if a["valid"]], "released_at": now()}
            manifest = self._artifact(body, "release_manifest.json", canonical(release).encode(), "design", "release", candidate["artifact_ids"] + [report["id"]])
            self._accept(body, ["G5"], [manifest["id"]])
            if any(u["status"] != "VALID" for u in body["units"]):
                raise ContractError("required acceptance units remain incomplete")
            body.update(status="COMPLETE", release=release)
            return "독립 검토와 출고 기록을 완료했습니다"
        return self._view(self.store.mutate(run_id, "review.completed", {"sha256": digest(data)}, apply))
