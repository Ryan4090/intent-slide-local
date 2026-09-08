"""Sanitized research operations and a projection of current accepted evidence.

Provider tool completion describes an operation, never a successful extraction.
Evidence quantities come exclusively from the engine's integrity-checked view.
The operation ledger is persisted with the run in the existing SQLite store.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .contracts import ContractError, digest, now, safe_path
from .evidence import normalized_excerpt

_HISTORY_LIMIT = 128
_TERMINAL = {"COMPLETED", "FAILED", "INTERRUPTED"}
_LIVE = {"RUNNING", "WAITING_USER"}


def safe_source_url(value: Any) -> str | None:
    """Display HTTP(S) locations without credentials, query strings or fragments."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or "\\" in parsed.netloc:
            return None
        host = parsed.hostname.lower()
        port = parsed.port
        if ":" in host:
            host = f"[{host}]"
        if port is not None and (parsed.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", "", ""))
    except (ValueError, TypeError):
        return None


def _message(action: str, status: str, site: str | None) -> str:
    target = f"{site}에서 " if site else ""
    verb = {"search": "웹 검색", "open": "자료 열람", "find": "자료 내 근거 탐색"}[action]
    if status == "RUNNING":
        return f"{target}{verb}을 진행하고 있습니다"
    if status == "FAILED":
        return f"{target}{verb} 동작이 실패했습니다. 확보한 자료 수에는 포함하지 않습니다"
    if status == "INTERRUPTED":
        return f"{target}{verb} 동작의 완료를 확인하지 못했습니다"
    return f"{target}{verb} 동작을 마쳤습니다. 자료 확보량은 저장·검증 결과로 집계합니다"


def research_event(event: dict, job: dict) -> dict | None:
    """Allowlisted Codex webSearch lifecycle, without raw queries or tool output.

    Protocol: https://learn.chatgpt.com/docs/app-server, Items / webSearch.action.
    Unknown adapters remain visible through their accepted research checkpoints.
    """
    if job.get("phase") != "research" or not isinstance(event, dict):
        return None
    method, params = event.get("method"), event.get("params")
    if method not in {"item/started", "item/completed"} or not isinstance(params, dict):
        return None
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "webSearch":
        return None
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
        return None
    action_data = item.get("action")
    action_data = action_data if isinstance(action_data, dict) else {}
    action_type = action_data.get("type", "search")
    if not isinstance(action_type, str):
        return None
    action = {"search": "search", "openPage": "open", "findInPage": "find"}.get(action_type)
    if action is None:
        return None
    url = safe_source_url(action_data.get("url"))
    # Only the publisher host is needed for live operations. Original URLs stay
    # in the worker's source evidence; secret-bearing paths never enter this log.
    site = urlsplit(url).hostname if url else None
    status = "RUNNING" if method == "item/started" else "COMPLETED"
    reported_status = item.get("status")
    if isinstance(reported_status, str) and reported_status in {"failed", "error", "declined"}:
        status = "FAILED"
    elif isinstance(reported_status, str) and reported_status in {"cancelled", "interrupted"}:
        status = "INTERRUPTED"
    timestamp = now()
    return {"id": digest([job["id"], params.get("turnId"), identifier])[:32],
            "job_id": job["id"], "action": action, "status": status, "site": site,
            "message": _message(action, status, site), "created_at": timestamp,
            "updated_at": timestamp, "evidence_status": "OBSERVED"}


def append_research_activity(body: dict, event: dict) -> bool:
    """Upsert one operation; preserve start time and bound aggregate storage."""
    ledger = body.setdefault("research_activity", {"schema_version": "research-operations.v1", "events": [], "omitted_events": 0})
    events = ledger["events"]
    existing = next((entry for entry in events if entry["id"] == event["id"]), None)
    if existing:
        if existing["status"] in _TERMINAL or existing["status"] == event["status"]:
            return False
        existing.update({**copy.deepcopy(event), "created_at": existing["created_at"]})
    else:
        events.append(copy.deepcopy(event))
    if len(events) > _HISTORY_LIMIT:
        omitted = len(events) - _HISTORY_LIMIT
        del events[:omitted]
        ledger["omitted_events"] += omitted
    return True


def _saved_path(root: Path, artifact: dict | None) -> str | None:
    if not artifact or artifact.get("valid") is not True or not isinstance(artifact.get("path"), str):
        return None
    try:
        path = safe_path(root, artifact["path"])
        return str(path) if path.is_file() else None
    except (ContractError, OSError, ValueError):
        return None


def project_research_activity(body: dict, root: Path) -> dict:
    """Project an Engine snapshot (including current artifact.valid integrity).

    No source bytes are re-read here: Engine._view has already checked hashes and
    dependency validity. Rejected/stale research never earns extraction counts.
    Counts of bytes refer to saved source files, not downloaded network traffic.
    """
    root = Path(root).resolve()
    artifacts = {item["id"]: item for item in body.get("artifacts", [])}
    bundle = next((item for item in reversed(list(artifacts.values()))
                   if item.get("kind") == "research" and item.get("valid") is True), None)
    bundle_path = _saved_path(root, bundle)
    research = (body.get("research") or {}) if bundle_path else {}
    sources = []
    current_sources = {}
    unique_files = {}
    for source in research.get("sources", []):
        artifact = artifacts.get(source.get("artifact_id"))
        saved = _saved_path(root, artifact)
        if (source.get("status", "SNAPSHOT") != "SNAPSHOT" or not saved
                or artifact.get("sha256") != source.get("sha256")):
            continue
        current_sources[source["id"]] = source
        unique_files[artifact["sha256"]] = artifact.get("bytes", 0)
        url = safe_source_url(source.get("url"))
        sources.append({"id": source["id"], "title": str(source.get("title", "자료"))[:500],
                        "site": urlsplit(url).hostname if url else "제공 자료",
                        "url": url, "origin": source["origin"], "artifact_id": artifact["id"],
                        "saved_path": saved, "download_url": artifact.get("download_url"),
                        "saved_bytes": artifact.get("bytes", 0), "excerpt_count": 0,
                        "claim_count": 0, "evidence_status": "VERIFIED"})
    excerpts = {source_id: set() for source_id in current_sources}
    claim_ids = {source_id: set() for source_id in current_sources}
    all_excerpts, accepted_claims = set(), set()
    for claim in research.get("claims", []):
        supports = claim.get("supports", [])
        if any(support.get("source_id") not in current_sources for support in supports):
            continue
        accepted_claims.add(claim["id"])
        for support in supports:
            source_id = support["source_id"]
            source = current_sources[source_id]
            receipt = support.get("verification", {})
            claim_ids[source_id].add(claim["id"])
            if (receipt.get("status") != "VERIFIED" or receipt.get("sha256") != source["sha256"]
                    or receipt.get("artifact_id") != source["artifact_id"]
                    or receipt.get("locator") != support.get("locator")):
                continue
            key = (source["sha256"], support["locator"], normalized_excerpt(support["excerpt"]))
            excerpts[source_id].add(key)
            all_excerpts.add(key)
    for source in sources:
        source.update(excerpt_count=len(excerpts[source["id"]]), claim_count=len(claim_ids[source["id"]]))
    jobs = {job["id"]: job for job in body.get("jobs", [])}
    latest = next((job for job in reversed(body.get("jobs", [])) if job.get("phase") == "research"), None)
    ledger = body.get("research_activity") or {}
    events = copy.deepcopy(ledger.get("events", [])[-_HISTORY_LIMIT:])
    for event in events:
        job = jobs.get(event["job_id"])
        event["historical"] = bool(not job or not latest or job["id"] != latest["id"]
                                   or job.get("status") in {"FAILED", "BLOCKED", "CANCELLED", "INTERRUPTED"}
                                   or body.get("active_stage") != "research")
        if event["status"] == "RUNNING" and (not job or job["status"] not in _LIVE):
            event["status"] = "INTERRUPTED"
            event["message"] = _message(event["action"], event["status"], event["site"])
    current_events = [event for event in events if not event["historical"]]
    if latest and latest.get("status") in _LIVE and body.get("active_stage") == "research":
        current_action = current_events[-1]["message"] if current_events else "조사를 진행 중입니다. 새 검색 동작 또는 검증된 자료를 기다리고 있습니다"
    elif research:
        current_action = "저장된 원문과 검증된 발췌·주장 연결을 확인할 수 있습니다"
    elif latest and latest.get("status") in {"FAILED", "BLOCKED", "CANCELLED", "INTERRUPTED"}:
        current_action = "조사가 중단되었습니다. 활동 이력은 저장되어 있으며, 현재 유효한 조사 결과는 아직 없습니다"
    elif body.get("active_stage") == "research":
        current_action = "확정된 의도를 바탕으로 조사를 준비합니다. 검증된 자료가 저장되면 여기에 표시됩니다"
    else:
        current_action = "의도를 확정하면 조사 활동과 저장된 근거가 여기에 표시됩니다"
    return {"schema_version": "research-activity.v1", "current_action": current_action,
            "summary": {"source_files": len(unique_files),
                        "sites": len({source["site"] for source in sources if source["url"]}),
                        "claims": len(accepted_claims), "verified_excerpts": len(all_excerpts),
                        "saved_bytes": sum(unique_files.values())},
            "sources": sources, "events": events,
            "omitted_events": ledger.get("omitted_events", 0),
            "storage": {"database": str(root / "control.sqlite"), "directory": str(root / "runs" / body["id"] / "artifacts"),
                        "bundle": bundle_path, "bundle_artifact_id": bundle["id"] if bundle_path else None,
                        "bundle_download_url": bundle.get("download_url") if bundle_path else None},
            "evidence_note": "자료 수·용량은 현재 유효한 저장 원문 기준입니다. 발췌 수는 원문 위치가 검증된 중복 없는 인용이며, 사실의 의미 검증이나 전체 웹 추출량을 뜻하지 않습니다. 활동 이력은 검색·열람 동작의 관찰 기록입니다."}
