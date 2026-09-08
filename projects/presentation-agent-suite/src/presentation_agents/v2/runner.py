"""Single-flight worker queue joining Codex events to trusted workflow commands."""
from __future__ import annotations

import copy
from collections import OrderedDict
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .contracts import Conflict, ContractError, digest, file_hash, normalize_interview, now, safe_path, uid
from .engine import Engine
from .design_catalog import get_design_preset
from .prompts import prompt_for
from .provider import ProviderError
from .research_activity import append_research_activity, research_event
from .login import BrowserLogin
from .provider_registry import MVP_PROVIDER_IDS, mvp_selection, normalize_selection, provider_factory as make_provider, provider_metadata

_LIVE_JOB_STATUSES = {"RUNNING", "WAITING_USER"}
_PENDING_QUESTION_STATUSES = {"PENDING", "DISPATCHING"}


class _ApprovalContextCache:
    """Bounded, per-job display metadata; never an authorization decision."""

    _MAX_CONTEXT_BYTES = 32 * 1024
    _MAX_CACHE_BYTES = 256 * 1024
    _MAX_ITEMS = 32
    _MAX_FILES = 16
    _METHOD_TYPES = {"item/fileChange/requestApproval": "fileChange",
                     "item/commandExecution/requestApproval": "commandExecution"}

    def __init__(self, job_id: str, workspace: Path):
        self.job_id, self.workspace = job_id, workspace.resolve()
        self._items = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _text(value, limit):
        if not isinstance(value, str):
            return None, False
        changed = False
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError:
            raw = value.encode("utf-8", errors="replace")
            changed = True
        return raw[:limit].decode("utf-8", errors="ignore"), changed or len(raw) > limit

    @classmethod
    def _key(cls, params, item_id):
        values = (params.get("threadId"), params.get("turnId"), item_id)
        if any(not isinstance(value, str) or not value or len(value.encode("utf-8", errors="replace")) > 256 for value in values):
            return None
        return values

    @staticmethod
    def _size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _path(self, value):
        shown, truncated = self._text(value, 2048)
        result = {"path": shown, "resolved_path": None, "scope": "unknown", "path_truncated": truncated}
        # Relative paths have no established base in a file-change event.
        if not shown or truncated or "\x00" in shown or not Path(shown).is_absolute():
            return result
        try:
            resolved = Path(shown).resolve(strict=False)
            normalized, too_long = self._text(str(resolved), 4096)
            if not too_long:
                result.update(resolved_path=normalized, scope="inside" if resolved.is_relative_to(self.workspace) else "outside")
        except (OSError, RuntimeError, ValueError):
            pass
        return result

    @staticmethod
    def _scope(files, omitted=0):
        scopes = [scope for item in files for scope in
                  (item["scope"], (item.get("move_to") or {}).get("scope", "inside"))]
        if "outside" in scopes:
            return "outside"
        return "unknown" if omitted or not scopes or "unknown" in scopes else "inside"

    def _command(self, item):
        text, truncated = self._text(item.get("command"), 8192)
        location = self._path(item.get("cwd"))
        if text is None and location["path"] is None:
            return None
        return {"text": text, "cwd": location["path"], "resolved_cwd": location["resolved_path"],
                "cwd_scope": location["scope"], "truncated": truncated or location["path_truncated"]}

    def _bounded(self, context):
        def mark_omissions():
            if context["omitted_files"]:
                if context["scope"] != "outside":
                    context["scope"] = "unknown"
                if "일부 파일은 표시 한도를 넘어 생략됐습니다." not in context["notes"]:
                    context["notes"].append("일부 파일은 표시 한도를 넘어 생략됐습니다.")
        mark_omissions()
        while self._size(context) > self._MAX_CONTEXT_BYTES and context["files"]:
            context["files"].pop()
            context["omitted_files"] += 1
            context["truncated"] = True
            mark_omissions()
        # Control characters take six bytes each when JSON-escaped.
        while self._size(context) > self._MAX_CONTEXT_BYTES and context.get("command") and context["command"].get("text"):
            context["command"]["text"] = context["command"]["text"][:len(context["command"]["text"]) // 2]
            context["command"]["truncated"] = context["truncated"] = True
        return context

    def _base(self, item_id, item_type):
        return {"schema_version": "approval-context.v1", "job_id": self.job_id,
                "item_id": self._text(item_id, 256)[0], "item_type": item_type,
                "match_status": "MISSING", "source": "none", "workspace": str(self.workspace),
                "scope": "unknown", "files": [], "command": None,
                "truncated": False, "omitted_files": 0, "notes": []}

    def record(self, params: dict):
        item = params.get("item") if isinstance(params, dict) else None
        if not isinstance(item, dict) or item.get("type") not in self._METHOD_TYPES.values():
            return
        key = self._key(params, item.get("id"))
        if key is None:
            return
        context = self._base(item["id"], item["type"])
        context.update(match_status="MATCHED", source="item/started")
        if item["type"] == "fileChange":
            changes = item.get("changes")
            if isinstance(changes, list):
                context["omitted_files"] = max(0, len(changes) - self._MAX_FILES)
                for change in changes[:self._MAX_FILES]:
                    if not isinstance(change, dict):
                        context["omitted_files"] += 1
                        continue
                    path = self._path(change.get("path"))
                    kind = change.get("kind")
                    kind_name = kind.get("type") if isinstance(kind, dict) else kind
                    diff, diff_truncated = self._text(change.get("diff"), 4096)
                    move = self._path(kind["move_path"]) if isinstance(kind, dict) and kind.get("move_path") is not None else None
                    context["files"].append({**path, "kind": kind_name if isinstance(kind_name, str) and kind_name in {"add", "update", "delete"} else "unknown",
                        "diff": diff, "diff_available": diff is not None, "diff_truncated": diff_truncated, "move_to": move})
                    context["truncated"] |= path["path_truncated"] or diff_truncated or bool(move and move["path_truncated"])
            context["scope"] = self._scope(context["files"], context["omitted_files"])
            if not context["files"]:
                context["notes"].append("시작 이벤트에 대상 파일 정보가 없습니다.")
            elif any(not item["diff_available"] for item in context["files"]):
                context["notes"].append("일부 파일의 변경 내용(diff)이 제공되지 않았습니다.")
        else:
            context["command"] = self._command(item)
            context["truncated"] = bool(context["command"] and context["command"]["truncated"])
            context["notes"].append("작업 폴더 위치만 분류했으며 명령 전체의 접근 범위는 확인되지 않았습니다.")
        context["truncated"] |= bool(context["omitted_files"])
        context = self._bounded(context)
        prior = self._items.pop(key, None)
        if prior:
            self._bytes -= self._size(prior)
        self._items[key] = context
        self._bytes += self._size(context)
        while len(self._items) > self._MAX_ITEMS or self._bytes > self._MAX_CACHE_BYTES:
            _, removed = self._items.popitem(last=False)
            self._bytes -= self._size(removed)

    def review(self, method: str, params: dict) -> dict:
        expected_type = self._METHOD_TYPES.get(method)
        context = self._base(params.get("itemId"), expected_type)
        key = self._key(params, params.get("itemId"))
        matched = self._items.get(key) if key else None
        if matched and matched["item_type"] == expected_type:
            self._items.move_to_end(key)
            return copy.deepcopy(matched)
        if matched or any(stored[2] == params.get("itemId") for stored in self._items):
            context["match_status"] = "MISMATCH"
            context["notes"].append("작업 항목의 대화·턴·유형이 승인 요청과 일치하지 않습니다.")
        else:
            context["notes"].append("동일 항목의 시작 이벤트를 확인하지 못했습니다. 대상 범위는 미확인입니다.")
        if expected_type == "commandExecution":
            context["command"] = self._command(params)
            if context["command"]:
                context["source"] = "approval_params"
                context["truncated"] = context["command"]["truncated"]
        return self._bounded(context)


class Runner:
    def __init__(self, engine: Engine, repo_root: Path, *, provider_factory=None, default_provider="codex"):
        self.engine = engine
        self.repo_root = repo_root.resolve()
        self.provider_factory = provider_factory
        self.default_provider = mvp_selection({"provider": "codex" if default_provider == "auto" else default_provider})["provider"]
        self._lock = threading.RLock()
        self._worker = None
        self._provider = None
        self._active = None
        self._discovery = {"status": "IDLE"}
        self._discovery_thread = None
        self._probes = set()
        self._closed = False
        self._login = BrowserLogin(finished=lambda: self.discover(refresh=True))
        self._capabilities = {"ready": None, "reason": "실행 전 연결 확인", "routes": {"main-svg-generation": "AVAILABLE", "beautify": "AVAILABLE", "template-fill": "BLOCKED_OWNER_ADAPTER", "native-enhance": "BLOCKED_OWNER_ADAPTER"}}
        self._provider_capabilities = {
            item["id"]: {**item, "ready": None, "reason": "설치와 로그인을 자동으로 확인합니다",
                         "auth_mode": "unknown", "models": [], "features": {}}
            for item in provider_metadata()
        }

    def _make_provider(self, provider_id):
        # The no-argument factory remains an explicit test injection surface.
        return self.provider_factory() if self.provider_factory else make_provider(provider_id)

    def _remember_capabilities(self, provider_id, result):
        self._provider_capabilities[provider_id].update(copy.deepcopy(result))
        self._provider_capabilities[provider_id]["id"] = provider_id
        if provider_id == self.default_provider:
            self._capabilities.update(result)

    def capabilities(self):
        from .verification import renderer_contract
        with self._lock:
            return {**copy.deepcopy(self._capabilities), "default_provider": self.default_provider,
                    "providers": copy.deepcopy([self._provider_capabilities[name] for name in MVP_PROVIDER_IDS]),
                    "discovery": copy.deepcopy(self._discovery),
                    "login": self._login.snapshot(),
                    "recommended_provider": next((name for name in MVP_PROVIDER_IDS if self._provider_capabilities[name].get('ready') is True and self._provider_capabilities[name].get('auto_connect') is True), None),
                    "platform": {"name": sys.platform, "label": {"darwin":"Mac", "win32":"Windows", "linux":"Linux"}.get(sys.platform, sys.platform)},
                    "rendering": renderer_contract(self.repo_root)}

    def discover(self, *, refresh=False):
        """Probe native metadata in the background, never submit a model job."""
        with self._lock:
            if self._closed:
                return self.capabilities()
            if self._active:
                result = self.capabilities()
                result['discovery'] = {'status':'DEFERRED', 'reason':'현재 AI 작업을 유지합니다. 작업 완료 후 다시 탐색할 수 있습니다.'}
                return result
            if self._discovery['status'] == 'RUNNING' or (not refresh and self._discovery['status'] == 'COMPLETE'):
                return self.capabilities()
            self._discovery = {'status':'RUNNING'}
            self._discovery_thread = threading.Thread(target=self._discover, name='intent-slide-discovery', daemon=True)
            self._discovery_thread.start()
        return self.capabilities()

    def _discover(self):
        # Independent native probes do not hold the engine lock while awaiting I/O.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def probe(provider_id):
            try:
                with self._make_provider(provider_id) as provider:
                    with self._lock:
                        if self._closed:
                            return {'ready': False, 'reason': '작업실이 종료되었습니다'}
                        self._probes.add(provider)
                    deadline = threading.Timer(30, provider.close)
                    deadline.daemon = True
                    deadline.start()
                    try:
                        return provider.preflight()
                    finally:
                        deadline.cancel()
                        with self._lock:
                            self._probes.discard(provider)
            except (ProviderError, OSError, RuntimeError, ValueError, ImportError) as exc:
                return {'ready':False, 'auth_mode':'unknown', 'reason': '설치 또는 공식 로그인을 확인해 주세요', 'code':getattr(exc, 'code', type(exc).__name__)}
        try:
            with ThreadPoolExecutor(max_workers=len(MVP_PROVIDER_IDS), thread_name_prefix='intent-ai-probe') as pool:
                pending = {pool.submit(probe, provider_id):provider_id for provider_id in MVP_PROVIDER_IDS}
                for future in as_completed(pending):
                    with self._lock:
                        self._remember_capabilities(pending[future], future.result())
            with self._lock:
                self._discovery = {'status':'COMPLETE'}
        except Exception:
            with self._lock:
                self._discovery = {'status':'COMPLETE', 'reason':'일부 연결을 확인하지 못했습니다. 다시 탐색할 수 있습니다.'}

    def login(self, provider_id):
        if provider_id != 'codex':
            raise ContractError('화면에서 로그인은 내장 Codex를 지원합니다')
        with self._lock:
            if self._active or any(job['status'] == 'QUEUED' for run in self.engine.store.list() for job in run['jobs']):
                raise Conflict('현재 AI 작업을 마친 뒤 로그인해 주세요')
            if self._provider_capabilities['codex'].get('ready') is True:
                return {'status': 'ALREADY_CONNECTED', 'provider': 'codex'}
            return self._login.start()

    def preflight(self, provider_id=None):
        provider_id = mvp_selection({"provider": provider_id or self.default_provider})["provider"]
        with self._lock:
            if self._active:
                raise Conflict("AI 실행을 마치거나 취소한 뒤 연결을 다시 확인해 주세요")
            try:
                with self._make_provider(provider_id) as provider:
                    result = provider.preflight()
            except ProviderError as exc:
                result = {"ready": False, "auth_mode": "unknown", "reason": str(exc), "code": exc.code}
            self._remember_capabilities(provider_id, result)
        return self.capabilities()

    def kick(self):
        with self._lock:
            if self._closed or self._login.snapshot()['status'] in {'STARTING', 'RUNNING'}:
                return
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._loop, name="slidemaster-worker", daemon=True)
            self._worker.start()

    def _loop(self):
        while True:
            job = None
            try:
                with self._lock:
                    if self._closed or self._login.snapshot()['status'] in {'STARTING', 'RUNNING'}:
                        self._worker = None
                        return
                    run = next((r for r in reversed(self.engine.store.list()) if any(j["status"] == "QUEUED" for j in r["jobs"])), None)
                    if not run:
                        self._worker = None
                        return
                    job = self.engine.claim_job(run["id"])
                    # Reserve the claimed attempt before preparation so login
                    # cannot start in the gap before a provider is registered.
                    self._active = {"run_id": run["id"], "job_id": job["id"],
                                    "provider_id": normalize_selection(job.get("provider_selection"))["provider"]}
                self._execute(run["id"], job)
            except Exception as exc:
                # Preserve this attempt and expose the failure; never generate a PASS fallback.
                try:
                    if job is not None:
                        with self._lock:
                            self._record_failure(run["id"], job["id"], exc)
                except ContractError:
                    pass
            finally:
                with self._lock:
                    if job is not None and self._active and self._active["job_id"] == job["id"]:
                        self._active = None
                        self._provider = None

    def _mutate_live_job(self, run_id, job_id, event, apply):
        """Check the terminal-state boundary inside the same SQLite transaction."""
        def guarded(body):
            job = next((j for j in body["jobs"] if j["id"] == job_id), None)
            if not job or job["status"] not in _LIVE_JOB_STATUSES:
                raise Conflict("실행이 종료되어 후속 이벤트를 채택하지 않았습니다")
            return apply(body, job)
        return self.engine.store.mutate(run_id, event, {}, guarded)

    @staticmethod
    def _end_wait(job):
        started = job.pop("wait_started_at", None)
        if started:
            job["user_wait_seconds"] = job.get("user_wait_seconds", 0) + max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())

    def _resume_if_answered(self, body, job):
        pending = any(q.get("job_id") == job["id"] and q["status"] in _PENDING_QUESTION_STATUSES for q in body["questions"])
        if pending:
            job["status"] = body["status"] = "WAITING_USER"
        else:
            self._end_wait(job)
            job["status"] = body["status"] = "RUNNING"

    def _record_failure(self, run_id, job_id, exc):
        from .verification import VerificationBlocked
        status = "BLOCKED" if isinstance(exc, (ProviderError, VerificationBlocked)) else "FAILED"
        def failed(body, job):
            self._end_wait(job)
            job.update(status=status, error=str(exc)[:2000], finished_at=now())
            body["status"] = status
            body["findings"].append({"code": "JOB_ERROR", "message": job["error"], "job_id": job_id})
            for question in body["questions"]:
                if question.get("job_id") == job_id and question["status"] in _PENDING_QUESTION_STATUSES:
                    question.update(status="INTERRUPTED", resolved_at=now())
            return "실행을 멈추고 원인을 기록했습니다"
        return self._mutate_live_job(run_id, job_id, "job.failed", failed)

    def _current_input(self, run_id, job):
        body = self.engine.store.read(run_id)
        current = next((j for j in body["jobs"] if j["id"] == job["id"]), None)
        if not current or current["status"] not in _LIVE_JOB_STATUSES or self.engine.input_hash(body) != job["input_hash"]:
            raise Conflict("실행이 종료되었거나 입력이 변경되어 결과를 채택하지 않았습니다")
        return body

    def _prepare(self, run_id, job):
        body = self._current_input(run_id, job)
        workspace = self.engine.root / "attempts" / job["id"]
        workspace.mkdir(parents=True, exist_ok=False)
        inputs = workspace / "inputs"
        inputs.mkdir()
        artifacts = []
        unavailable_checkpoints = set()
        for artifact in body["artifacts"]:
            if not artifact["valid"]:
                continue
            destination = inputs / f"{artifact['id']}--{artifact['name']}"
            try:
                path = self.engine.artifact_path(run_id, artifact["id"])
                if file_hash(path) != artifact["sha256"]:
                    raise ContractError("input artifact changed before worker start")
                shutil.copyfile(path, destination)
            except (ContractError, OSError):
                if not artifact.get("checkpoint_job_id"):
                    raise
                unavailable_checkpoints.add(artifact["id"])
                destination.unlink(missing_ok=True)
                continue
            artifacts.append({**artifact, "local_path": str(destination.relative_to(workspace)), "path": None})
        if unavailable_checkpoints:
            def omit_unavailable(current, active):
                if self.engine.input_hash(current) != job["input_hash"]:
                    raise Conflict("inputs changed while preparing the attempt")
                for artifact in current["artifacts"]:
                    if artifact["id"] in unavailable_checkpoints and artifact.get("checkpoint_job_id"):
                        artifact.update(valid=False, checkpoint_unavailable_at=now())
                return "유실·변조된 선택적 중간 원본을 제외하고 새 시도를 준비했습니다"
            self._mutate_live_job(run_id, job["id"], "checkpoint.unavailable", omit_unavailable)
        packet = {key: body[key] for key in ("id", "request", "source_mode", "intent", "research", "direction", "candidate", "messages", "approvals", "changes")}
        packet["design_preference"] = copy.deepcopy(body.get("design_preference"))
        packet["design_preset"] = (get_design_preset(packet["design_preference"]["preset_id"])
                                   if packet["design_preference"] else None)
        packet.update(phase=job["phase"], artifacts=artifacts, job_id=job["id"],
                      provider_selection=normalize_selection(job.get("provider_selection")))
        if packet["provider_selection"]["provider"] == "claude" and job["phase"] in {"design_direction", "design_build"}:
            # Claude restricted file tools read only this attempt. Supply an
            # ordinary-file snapshot, never widen access to the service store.
            reference = workspace / "reference"
            reference.mkdir()
            for relative in (".claude/skills", ".codex/skills", "docs/rules"):
                source = safe_path(self.repo_root, relative, exists=False)
                if not source.is_dir():
                    raise ContractError(f"required reference directory is missing: {relative}")
                for path in source.rglob("*"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    checked = safe_path(self.repo_root, str(path.relative_to(self.repo_root)))
                    target = reference / path.relative_to(self.repo_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(checked, target)
            packet["reference_root"] = "reference"
        if job["phase"] == "design_build":
            from .verification import renderer_contract
            packet["service_verification"] = renderer_contract(self.repo_root)
        previous_pages = next((item for item in reversed(body["jobs"])
                               if item["id"] != job["id"] and item.get("input_hash") == job["input_hash"]
                               and item.get("execution", {}).get("pages", {}).get("page_records")), None)
        if job["phase"] == "design_build" and previous_pages:
            checkpoint = previous_pages["execution"]["pages"]
            local_paths = {item["id"]: item["local_path"] for item in artifacts}
            complete_snapshot = set(checkpoint["artifact_ids"]) <= set(local_paths)
            packet["prior_page_checkpoint"] = {
                "job_id": previous_pages["id"], "job_status": previous_pages["status"],
                "accepted_for_current_attempt": False,
                "pages": [{**page, "local_path": local_paths.get(page["artifact_id"]),
                           "notes_local_path": local_paths.get(page.get("notes_artifact_id"))}
                          for page in checkpoint["page_records"] if complete_snapshot],
                "snapshot_available": complete_snapshot,
                "instruction": "Previous attempt lineage only. Recreate canonical files and submit a new checkpoint for this attempt.",
            }
        previous = next((j for j in reversed(body["jobs"]) if j["id"] != job["id"] and j["phase"] == job["phase"] and j.get("input_hash") == job["input_hash"] and j["status"] in {"FAILED", "BLOCKED", "INTERRUPTED"}), None)
        if previous:
            packet["previous_attempt"] = {k: previous.get(k) for k in ("id", "status", "error")}
            # Preserve the rejected envelope as an explicitly unaccepted draft.
            # Copy only its declared source files, never the service control DB.
            if previous.get("workspace"):
                previous_workspace = safe_path(self.engine.root, previous["workspace"], exists=False)
                candidate = safe_path(previous_workspace, "stage_result.json", exists=False)
                if candidate.is_file() and candidate.stat().st_size <= 4 * 1024 * 1024:
                    try:
                        draft = json.loads(candidate.read_text(encoding="utf-8"))
                    except (ValueError, UnicodeError):
                        draft = None
                    if isinstance(draft, dict):
                        destination = inputs / "previous-candidate.json"
                        destination.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
                        packet["previous_attempt"].update(candidate_path=str(destination.relative_to(workspace)), accepted=False, artifacts=[])
                        total = 0
                        descriptors = draft.get("artifacts", [])
                        for index, descriptor in enumerate(descriptors[:1000] if isinstance(descriptors, list) else []):
                            if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
                                continue
                            try:
                                path = safe_path(previous_workspace, descriptor["path"])
                            except ContractError:
                                continue
                            size = path.stat().st_size
                            if not path.is_file() or size > 100 * 1024 * 1024 or total + size > 256 * 1024 * 1024:
                                continue
                            total += size
                            target = inputs / f"retry-{index}--{path.name}"
                            shutil.copyfile(path, target)
                            packet["previous_attempt"]["artifacts"].append({"key": descriptor.get("key"), "kind": descriptor.get("kind", "source"), "local_path": str(target.relative_to(workspace)), "sha256": file_hash(target)})
        (workspace / "input.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        # A project-local worker contract refines the root boundary for this approved run.
        (workspace / "AGENTS.md").write_text(
            "# Intent-Slide isolated stage\n\n"
            f"This is a {job['phase']} worker, not an end-to-end deck request. "
            "Read input.json and execute only the phase named there. "
            "Intent, clarification and research workers do not author presentations and do not need presentation design skills. "
            "Only design_direction and design_build workers load the routing dispatcher and selected owner. "
            "The design_review worker inspects only the supplied current rendered images and content contracts. "
            "This attempt is the authorized project workspace. Write only here. "
            "User approval is owned by the external web service. Never read sibling attempts, service state, "
            "global credentials, or use supervisor commands.\n", encoding="utf-8")
        return workspace

    def _import_and_resolve(self, run_id, job, workspace, result, artifact_cache):
        descriptors = result.get("artifacts", [])
        if not isinstance(descriptors, list) or len(descriptors) > 1000 or any(not isinstance(d, dict) for d in descriptors):
            raise ContractError("worker artifacts must be a bounded descriptor list")
        fresh, fingerprints, keys = [], {}, set()
        for descriptor in descriptors:
            key = descriptor.get("key")
            if not isinstance(key, str) or not key or key in keys:
                raise ContractError("worker artifact keys must be unique nonempty strings")
            keys.add(key)
            path = safe_path(workspace, str(descriptor.get("path", "")))
            if path.stat().st_size > 100 * 1024 * 1024:
                raise ContractError("worker artifact exceeds 100 MB")
            fingerprint = (str(path.relative_to(workspace)), descriptor.get("kind", "source"), file_hash(path))
            if artifact_cache.get(key, {}).get("fingerprint") != fingerprint:
                fresh.append(descriptor)
                fingerprints[key] = fingerprint
        mappings = self.engine.import_worker_artifacts(run_id, workspace, fresh, job["stage"], job_id=job["id"], input_hash=job["input_hash"])
        for key, artifact_id in mappings.items():
            artifact_cache[key] = {"id": artifact_id, "fingerprint": fingerprints[key]}
        def resolve(value):
            if isinstance(value, str) and value.startswith("artifact:"):
                key = value[len("artifact:"):]
                if key not in artifact_cache:
                    raise ContractError(f"undefined worker artifact key: {key}")
                return artifact_cache[key]["id"]
            if isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            if isinstance(value, list):
                return [resolve(v) for v in value]
            return value
        return resolve(result["data"])

    def _check_single_svg(self, path, cancelled):
        """Run the existing file checker without minting a project-wide gate receipt."""
        from presentation_agents import pipeline as legacy
        script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
                  "from svg_quality_checker import SVGQualityChecker;"
                  "r=SVGQualityChecker().check_file(sys.argv[2]);"
                  "print(json.dumps({k:r[k] for k in ('passed','errors','warnings')},ensure_ascii=False))")
        try:
            result = legacy._run_bounded_subprocess(
                [sys.executable, "-c", script, str(self.repo_root / ".claude/skills/ppt-master/scripts"), str(path)],
                cwd=self.repo_root, timeout=60, cancelled=cancelled)
        except legacy.SubprocessCancelled as exc:
            raise Conflict("page checkpoint inspection was cancelled or paused") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError("single-page shared SVG checker is unavailable or timed out") from exc
        if result.returncode != 0:
            raise ContractError("single-page shared SVG checker could not complete")
        try:
            checked = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ContractError("single-page shared SVG checker returned no valid result") from exc
        if not isinstance(checked, dict):
            raise ContractError("single-page shared SVG checker returned a non-object result")
        if checked.get("passed") is not True or checked.get("errors"):
            raise ContractError("page SVG quality failed: " + str(checked.get("errors", []))[:1500])
        return checked

    def _verify_design_checkpoint(self, run_id, job, workspace, data):
        """Inspect cumulative source pages; this is deliberately not a G4 receipt."""
        from presentation_agents import pipeline as legacy
        from .verification import _check_references
        body = self._current_input(run_id, job)
        if (job["phase"] != "design_build" or body["active_phase"] != "design_build"
                or next(item for item in body["jobs"] if item["id"] == job["id"])["status"] != "RUNNING"):
            raise Conflict("design page checkpoint is not the active phase")
        for gate in ("G1", "G2", "G3"):
            self.engine._approved(body, gate)
        if body["direction"]["route"] not in {"main-svg-generation", "beautify"}:
            raise ContractError("this route has no source-page checkpoint adapter")
        relative = data.get("project_path")
        if not isinstance(relative, str) or not relative:
            raise ContractError("page checkpoint project_path is required")
        project = workspace if relative == "." else safe_path(workspace, relative, exists=False)
        if not project.is_dir() or legacy._path_has_symlink(project, workspace):
            raise ContractError("page checkpoint project is missing or symlinked")
        pages = data.get("pages")
        expected = [slide["uid"] for slide in body["intent"]["slides"]]
        if (not isinstance(pages, list) or not pages or len(pages) > len(expected)
                or any(not isinstance(page, dict) for page in pages)
                or [page.get("slide_uid") for page in pages] != expected[:len(pages)]):
            raise ContractError("page checkpoint must be the cumulative approved slide prefix")
        manifest, records, used_paths, sizes = {}, [], set(), {}
        checker_path = self.repo_root / ".claude/skills/ppt-master/scripts/svg_quality_checker.py"
        checker_hash = file_hash(checker_path)
        claims = {claim["id"] for claim in body["research"].get("claims", [])}
        evidence = {item["legacy_id"] for item in body["intent"].get("requirements", []) if item.get("legacy_id")}
        evidence |= {identifier for identifier in claims if identifier.startswith("EVID-")}
        needs_notes = bool(body["intent"]["fields"].get("speaker_notes", {}).get("value", False))

        def capture(path):
            relative_path = str(path.relative_to(workspace))
            safe_path(workspace, relative_path)
            sizes[relative_path] = path.stat().st_size
            if sizes[relative_path] > 100 * 1024 * 1024 or sum(sizes.values()) > 256 * 1024 * 1024:
                raise ContractError("page checkpoint file exceeds 100 MB")
            current_hash = file_hash(path)
            if relative_path in manifest and manifest[relative_path] != current_hash:
                raise ContractError("page checkpoint input changed during inspection")
            manifest[relative_path] = current_hash

        def read_bound_text(path):
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ContractError("page checkpoint text exceeds 16 MB")
            content = path.read_bytes()
            relative_path = str(path.relative_to(workspace))
            content_hash = hashlib.sha256(content).hexdigest()
            if manifest.get(relative_path) != content_hash:
                raise ContractError("page checkpoint changed between capture and text inspection")
            try:
                return content.decode("utf-8")
            except UnicodeError as exc:
                raise ContractError("page checkpoint text is not UTF-8") from exc

        for filename, key in (("design_spec.md", "design_spec"), ("spec_lock.md", "spec_lock")):
            path = safe_path(workspace, str((project / filename).relative_to(workspace)))
            capture(path)
            if read_bound_text(path) != body["direction"][key]:
                raise ContractError(f"{filename} differs from the exact G3-approved proposal")
        for filename in ("project_meta.json", "image_sources.json", "animations.json"):
            path = safe_path(workspace, str((project / filename).relative_to(workspace)), exists=False)
            if path.exists():
                capture(path)
        for page_number, page in enumerate(pages, 1):
            self._current_input(run_id, job)
            if not isinstance(page.get("path"), str):
                raise ContractError("page path must be workspace-relative text")
            path = safe_path(workspace, page["path"])
            if path.parent != project / "svg_output" or path.suffix != ".svg" or str(path) in used_paths:
                raise ContractError("checkpoint pages must be distinct canonical svg_output files")
            used_paths.add(str(path))
            capture(path)
            text = read_bound_text(path)
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ContractError("page SVG declarations and entities are forbidden")
            try:
                root = ET.fromstring(text)
                box = [float(value) for value in root.get("viewBox", "").replace(",", " ").split()]
            except (ET.ParseError, ValueError) as exc:
                raise ContractError("page SVG XML or viewBox is invalid") from exc
            if (root.tag != "{http://www.w3.org/2000/svg}svg" or len(box) != 4
                    or not all(math.isfinite(value) for value in box) or box[2] <= 0 or box[3] <= 0):
                raise ContractError("page SVG needs a valid positive viewBox")
            claim_ids = page.get("claim_ids")
            if (not isinstance(claim_ids, list) or any(not isinstance(value, str) for value in claim_ids)
                    or len(set(claim_ids)) != len(claim_ids) or set(claim_ids) - claims):
                raise ContractError("page checkpoint has unknown or duplicate claim references")
            _check_references(text, evidence, claims, path.name)
            combined = text
            record = {"slide_uid": page["slide_uid"], "path": page["path"],
                      "sha256": manifest[page["path"]], "claim_ids": claim_ids}
            if page.get("notes_path"):
                if not isinstance(page["notes_path"], str):
                    raise ContractError("page checkpoint notes_path must be text")
                notes = safe_path(workspace, page["notes_path"])
                indexed = re.search(r"slide[_]?(\d+)", notes.stem)
                if (notes.parent != project / "notes" or notes.suffix != ".md" or notes.name == "total.md"
                        or (notes.stem != path.stem and (not indexed or int(indexed[1]) != page_number))):
                    raise ContractError("page checkpoint notes do not map to the declared SVG page")
                capture(notes)
                notes_text = read_bound_text(notes)
                if not notes_text.strip():
                    raise ContractError("page checkpoint notes are empty")
                _check_references(notes_text, evidence, claims, notes.name)
                combined += "\n" + notes_text
                record.update(notes_path=page["notes_path"], notes_sha256=manifest[page["notes_path"]])
            elif needs_notes:
                raise ContractError("approved speaker notes are missing from the page checkpoint")
            for claim_id in claim_ids:
                if not re.search(r"(?<![A-Za-z0-9_.:-])" + re.escape(claim_id) + r"(?![A-Za-z0-9_.:-])", combined):
                    raise ContractError("declared claim reference is missing from page provenance or notes")
            for element in root.iter():
                for name, value in element.attrib.items():
                    if name.rsplit("}", 1)[-1] in {"claim-id", "claim_id", "data-claim-id"} and value not in claims:
                        raise ContractError("SVG contains an unknown claim reference")
                if element.tag.rsplit("}", 1)[-1] not in {"image", "use"}:
                    continue
                href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
                if not href or href.startswith("#") or href.startswith("data:image/"):
                    continue
                source = path.parent / href
                if (":" in href or "?" in href or "#" in href or legacy._path_has_symlink(source, workspace)
                        or not source.resolve().is_relative_to(project.resolve())):
                    raise ContractError("page image reference escapes its isolated project")
                capture(safe_path(workspace, str(source.resolve().relative_to(workspace))))
            record["quality"] = self._check_single_svg(path, lambda: next(
                (item["status"] != "RUNNING" for item in self.engine.store.read(run_id)["jobs"] if item["id"] == job["id"]), True))
            records.append(record)
        if len(manifest) > 1000:
            raise ContractError("too many page checkpoint input files")
        for relative, expected_hash in manifest.items():
            if file_hash(safe_path(workspace, relative)) != expected_hash:
                raise ContractError("page checkpoint input changed during inspection")
        self._current_input(run_id, job)
        if file_hash(checker_path) != checker_hash:
            raise ContractError("page checker changed during inspection")
        return {"schema_version": "design-pages-check.v1", "pages": records,
                "data_sha256": digest(data), "input_sha256": manifest,
                "checker_sha256": checker_hash}

    def _poll_checkpoints(self, run_id, job, workspace, accepted, rejected, artifact_cache):
        """Cumulative research results or inspected, unweighted page milestones."""
        if job["phase"] not in {"research", "design_build"}:
            return
        current = self._current_input(run_id, job)
        if next(j for j in current["jobs"] if j["id"] == job["id"])["status"] != "RUNNING":
            return
        folder = safe_path(workspace, "checkpoints", exists=False)
        paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
        single = workspace / "checkpoint.json"
        if single.exists():
            paths.append(single)
        if len(paths) > 512:
            raise ContractError("too many stage checkpoint files")
        for path in paths:
            path = safe_path(workspace, str(path.relative_to(workspace)))
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ContractError("stage checkpoint exceeds 4 MB")
            raw = path.read_bytes()
            fingerprint = hashlib.sha256(raw).hexdigest()
            if fingerprint in accepted:
                continue
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                # The provider may still be writing the file. A later poll reads
                # fresh bytes; this incomplete file never earns progress.
                continue
            try:
                if not isinstance(result, dict) or result.get("kind") != "checkpoint" or not isinstance(result.get("data"), dict):
                    raise ContractError("stage checkpoint envelope is invalid")
                if job["phase"] == "design_build":
                    receipt = self._verify_design_checkpoint(run_id, job, workspace, result["data"])
                    self.engine.accept_page_checkpoint(run_id, workspace, result["data"], receipt,
                                                       job_id=job["id"], input_hash=job["input_hash"])
                else:
                    data = self._import_and_resolve(run_id, job, workspace, result, artifact_cache)
                    self.engine.publish(run_id, "research_checkpoint", data, job_id=job["id"], input_hash=job["input_hash"])
            except Conflict:
                current = self._current_input(run_id, job)
                if next(j for j in current["jobs"] if j["id"] == job["id"])["status"] == "WAITING_USER":
                    return
                raise
            except ContractError as exc:
                if fingerprint not in rejected:
                    def rejected_checkpoint(body, active):
                        body["findings"].append({"code": "CHECKPOINT_REJECTED", "message": str(exc)[:1000], "job_id": job["id"]})
                        return "중간 결과가 검증을 통과하지 못해 진행 기록에 반영하지 않았습니다"
                    self._mutate_live_job(run_id, job["id"], "checkpoint.rejected", rejected_checkpoint)
                    rejected.add(fingerprint)
                continue
            accepted.add(fingerprint)

    def _execute(self, run_id, job):
        selection = normalize_selection(job.get("provider_selection"))
        if selection['provider'] not in MVP_PROVIDER_IDS:
            raise ProviderError('MVP_CODEX_ONLY', '현재 MVP는 Codex 전용입니다. 기존 실행 기록은 보존됩니다. 프로젝트 설정에서 Codex로 전환해 주세요.')
        workspace = self._prepare(run_id, job)
        completed = threading.Event()
        outcome = {}
        observations = {"images_viewed": [], "items": []}
        approval_context = _ApprovalContextCache(job["id"], workspace)
        partial_text = []
        checkpoints, rejected_checkpoints, artifact_cache = set(), set(), {}
        def on_event(event):
            method, params = event.get("method", ""), event.get("params", {})
            if method == "turn/completed":
                outcome.update(params.get("turn", {}))
                completed.set()
                return
            if method == "provider/error":
                outcome.update(status="failed", error=params)
                completed.set()
                return
            try:
                self._current_input(run_id, job)
            except Conflict:
                return
            activity = research_event(event, job)
            if activity:
                def record_activity(body, active):
                    append_research_activity(body, activity)
                    active["heartbeat_at"] = now()
                    return activity["message"]
                try:
                    self._mutate_live_job(run_id, job["id"], "research.activity", record_activity)
                except Conflict:
                    return
            if method == "item/started":
                approval_context.record(params)
            if event.get("id") is not None and method.startswith(("item/", "mcpServer/")):
                provider_params = {"method": method, "params": params}
                if method in _ApprovalContextCache._METHOD_TYPES:
                    provider_params["review_context"] = approval_context.review(method, params)
                def question(body, active):
                    if not any(q.get("job_id") == job["id"] and q.get("provider_request_id") == event["id"] for q in body["questions"]):
                        body["questions"].append({"id": uid("question"), "job_id": job["id"], "stage": job["stage"],
                            "provider_id": selection["provider"],
                            "question": "AI 실행에 사용자 답변이 필요합니다" if "requestUserInput" in method else "AI 실행 권한 요청을 확인해 주세요",
                            "impact": "응답할 때까지 해당 실행이 대기합니다", "status": "PENDING", "provider_request_id": event["id"],
                            "provider_params": provider_params, "created_at": now()})
                    if any(q.get("job_id") == job["id"] and q["status"] in _PENDING_QUESTION_STATUSES for q in body["questions"]):
                        active.setdefault("wait_started_at", now())
                        active["status"] = body["status"] = "WAITING_USER"
                    return "사용자 응답 대기"
                try:
                    self._mutate_live_job(run_id, job["id"], "question.raised", question)
                except Conflict:
                    return
            elif method == "item/agentMessage/delta":
                if sum(map(len, partial_text)) < 50000:
                    partial_text.append(str(params.get("delta", "")))
            elif method == "item/completed":
                item = params.get("item", {})
                item_type = item.get("type")
                observations["items"].append({"type": item_type, "status": item.get("status")})
                if item_type == "agentMessage" and item.get("text"):
                    self.engine.assistant_message(run_id, item["text"])
                    partial_text.clear()
                if item_type == "imageView" and item.get("path") and item.get("status") not in {"failed", "error", "cancelled"}:
                    observations["images_viewed"].append(item["path"])
                if len(observations["items"]) % 5 == 0:
                    def progress(body, active):
                        active.update(heartbeat_at=now(), items_completed=len(observations["items"]))
                        return "AI 작업을 진행 중입니다"
                    try:
                        self._mutate_live_job(run_id, job["id"], "job.progress", progress)
                    except Conflict:
                        return
            elif method == "serverRequest/resolved":
                request_id = params.get("requestId")
                def resolved(body, active):
                    for question in body["questions"]:
                        if (request_id is not None and question.get("job_id") == job["id"]
                                and question.get("provider_request_id") == request_id and question["status"] in {"PENDING", "DISPATCHING", "ANSWERED"}):
                            question.update(status="RESOLVED", resolved_at=now())
                    self._resume_if_answered(body, active)
                    return "실행 질문 처리를 확인했습니다"
                try:
                    self._mutate_live_job(run_id, job["id"], "provider.resolved", resolved)
                except Conflict:
                    return
        with self._lock:
            if self._closed:
                raise ProviderError("CLOSED", "Runner closed before provider startup")
            provider = self._make_provider(selection["provider"])
            self._provider = provider
        with provider:
            readiness = provider.preflight()
            self._remember_capabilities(selection["provider"], readiness)
            if not readiness.get("ready"):
                raise ProviderError("AUTH_REQUIRED", readiness.get("reason", "선택한 AI 도구에서 로그인해 주세요"))
            body = self._current_input(run_id, job)
            # Resume completed conversational threads only; new input versions start a fresh context.
            previous = next((j for j in reversed(body["jobs"][:-1])
                             if j.get("thread_id") and j["phase"] == job["phase"]
                             and j["status"] == "COMPLETED"
                             and j.get("content_revision") == body["content_revision"]
                             and normalize_selection(j.get("provider_selection")) == selection), None)
            worker_prompt = (f"Authorized attempt directory: {workspace}\n"
                             "Pass this exact directory as workdir for shell tools; do not rely on an inherited current directory. "
                             "Temporary files must also stay here. Use direct file writes when a shell here-document needs an unavailable system temp directory.\n"
                             + prompt_for(job["phase"], self.repo_root,
                                          reference_root=workspace / "reference" if (workspace / "reference").is_dir() else None,
                                          provider=selection["provider"]))
            ack = provider.start_turn(cwd=workspace, prompt=worker_prompt, on_event=on_event,
                                      thread_id=previous["thread_id"] if previous else None,
                                      model=selection["model"], effort=selection["effort"])
            self._active.update(ack)
            def connected(current, active):
                active.update(**ack, workspace=str(workspace.relative_to(self.engine.root)), content_revision=body["content_revision"])
                return "선택한 AI 도구의 대화와 실행을 연결했습니다"
            self._mutate_live_job(run_id, job["id"], "job.connected", connected)
            while not completed.wait(0.5):
                current = self.engine.store.read(run_id)
                active_job = next(j for j in current["jobs"] if j["id"] == job["id"])
                if active_job["status"] not in _LIVE_JOB_STATUSES:
                    provider.cancel(ack["thread_id"], ack["turn_id"])
                    return
                self._poll_checkpoints(run_id, job, workspace, checkpoints, rejected_checkpoints, artifact_cache)
            self._current_input(run_id, job)
            if partial_text:
                self.engine.assistant_message(run_id, "".join(partial_text))
            if outcome.get("status") != "completed":
                raise ContractError(f"AI turn did not complete: {str(outcome.get('error', outcome.get('status')))[:1200]}")
        with self._lock:
            self._provider = None
        result_path = safe_path(workspace, "stage_result.json")
        if result_path.stat().st_size > 4 * 1024 * 1024:
            raise ContractError("stage result exceeds 4 MB")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ContractError("stage_result.json is not valid UTF-8 JSON") from exc
        self._current_input(run_id, job)
        if not isinstance(result, dict):
            raise ContractError("stage result envelope must be an object")
        if result.get("kind") == "question":
            prompt = normalize_interview(result.get("question", "확인이 필요합니다"), result.get("impact", "다음 단계 입력"),
                                         questions=result.get("questions"), intent_summary=result.get("intent_summary"))
            def interview(body, active):
                body["questions"].append({"id": uid("question"), "job_id": job["id"], "stage": job["stage"], "status": "PENDING",
                    **prompt,
                    "provider_request_id": None, "provider_params": None, "created_at": now()})
                self._end_wait(active)
                active.update(status="COMPLETED", finished_at=now())
                body["status"] = "WAITING_USER"
                return "인터뷰 질문을 검토함에 등록했습니다"
            self._mutate_live_job(run_id, job["id"], "question.raised", interview)
            return
        if result.get("kind") == "blocked":
            raise ProviderError("WORKER_BLOCKED", result.get("reason", "필수 기능 확인 필요"))
        if result.get("kind") != "result" or not isinstance(result.get("data"), dict):
            raise ContractError("stage result envelope is invalid")
        if job["phase"] == "design_build":
            from .verification import verify_candidate
            body = self.engine.store.read(run_id)
            receipt = verify_candidate(self.repo_root, workspace, result["data"], body["intent"], body["research"], body["direction"],
                                       cancelled=lambda: next(j for j in self.engine.store.read(run_id)["jobs"] if j["id"] == job["id"])["status"] == "CANCELLED")
            self.engine.accept_candidate(run_id, workspace, result["data"], receipt, job_id=job["id"], input_hash=job["input_hash"])
            current = self.engine.snapshot(run_id)
            self.engine.command(run_id, "run", {}, f"review-{job['id']}", current["revision"])
            return
        if job["phase"] == "design_review":
            self.engine.accept_review(run_id, result["data"], observations, job_id=job["id"], input_hash=job["input_hash"])
            return
        data = self._import_and_resolve(run_id, job, workspace, result, artifact_cache)
        self.engine.publish(run_id, job["phase"], data, job_id=job["id"], input_hash=job["input_hash"])

    def respond(self, run_id: str, payload: dict, operation_id: str, revision: int):
        if not operation_id or not isinstance(revision, int):
            raise ContractError("operation_id and expected_revision are required")
        request_id = payload.get("request_id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)) or not isinstance(payload.get("response"), dict):
            raise ContractError("provider request_id and structured response are required")
        with self._lock:
            active = copy.copy(self._active)
            provider = self._provider
            def apply(body):
                # This closure runs only for a new operation. A replay may arrive
                # after its provider was closed and must not send a second reply.
                if not active or active["run_id"] != run_id or not provider:
                    raise Conflict("현재 실행에서 응답할 요청을 찾을 수 없습니다")
                job = next((j for j in body["jobs"] if j["id"] == active["job_id"]), None)
                if not job or job["status"] not in _LIVE_JOB_STATUSES:
                    raise Conflict("종료된 실행에는 응답을 전달할 수 없습니다")
                question = next((q for q in body["questions"] if q.get("job_id") == job["id"] and q.get("provider_request_id") == request_id and q["status"] == "PENDING"), None)
                if not question:
                    raise ContractError("현재 실행의 질문이 아닙니다")
                for key, actual in (("question_id", question["id"]), ("job_id", job["id"]),
                                    ("provider_id", normalize_selection(job.get("provider_selection"))["provider"])):
                    if key in payload and payload[key] != actual:
                        raise Conflict("다른 AI 도구 또는 이전 실행의 질문에는 응답할 수 없습니다")
                question.update(status="DISPATCHING", response=payload.get("response", {}), answered_at=now())
                return "사용자 실행 응답을 기록했습니다"
            result = self.engine.store.mutate(run_id, "provider.answer", payload, apply, operation_id=operation_id, expected_revision=revision)
            # Resolve through the original operation's question identity; JSON-
            # RPC ids can be reused by a later job or provider connection.
            original = next(q for q in reversed(result["questions"]) if q.get("provider_request_id") == request_id and q["status"] == "DISPATCHING")
            question = next(q for q in self.engine.store.read(run_id)["questions"] if q["id"] == original["id"])
            if question["status"] == "DISPATCHING":
                if not active or active["run_id"] != run_id or active["job_id"] != question["job_id"] or not provider:
                    raise Conflict("이전 연결의 응답 전달 여부를 다시 확인해야 합니다")
                try:
                    provider.respond(request_id, payload.get("response", {}))
                except (ProviderError, ValueError) as exc:
                    def uncertain(body):
                        q = next(q for q in body["questions"] if q["id"] == question["id"])
                        job = next(j for j in body["jobs"] if j["id"] == question["job_id"])
                        if q["status"] == "DISPATCHING":
                            q.update(status="CANCELLED" if job["status"] == "CANCELLED" else "INTERRUPTED", resolved_at=now(), delivery_error=str(exc)[:2000])
                        if job["status"] in _LIVE_JOB_STATUSES:
                            self._end_wait(job)
                            job.update(status="INTERRUPTED", error=str(exc)[:2000], finished_at=now())
                            body["status"] = "INTERRUPTED"
                        return "응답 전달 여부 확인이 필요합니다. 자동 재전송하지 않습니다"
                    self.engine.store.mutate(run_id, "provider.delivery_uncertain", {}, uncertain)
                    raise ContractError(str(exc)) from exc
                def acknowledged(body):
                    q = next(q for q in body["questions"] if q["id"] == question["id"])
                    if q["status"] == "DISPATCHING":
                        q["status"] = "ANSWERED"
                    q["delivery_completed_at"] = now()
                    job = next(j for j in body["jobs"] if j["id"] == question["job_id"])
                    if job["status"] in _LIVE_JOB_STATUSES:
                        self._resume_if_answered(body, job)
                    return "사용자 응답을 실행기로 전달했습니다"
                self.engine.store.mutate(run_id, "provider.answered", {}, acknowledged)
        return self.engine.snapshot(run_id)

    def cancel(self, run_id):
        with self._lock:
            active = self._active
            if active and active["run_id"] == run_id and self._provider and active.get("turn_id"):
                self._provider.cancel(active["thread_id"], active["turn_id"])

    def close(self):
        with self._lock:
            self._closed = True
            providers = list(self._probes) + ([self._provider] if self._provider else [])
        self._login.close()
        for provider in providers:
            provider.close()
