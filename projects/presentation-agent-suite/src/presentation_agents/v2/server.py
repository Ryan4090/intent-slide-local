"""Authenticated loopback HTTP surface for the shared presentation engine."""
from __future__ import annotations

import json
import secrets
import time
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session

from .contracts import Conflict, ContractError
from .engine import Engine
from .provider_registry import mvp_selection


def create_app(engine: Engine, console_dir: Path, runner=None, *, bootstrap_token: str | None = None, instance_id: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=101 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_NAME="intent_slide_" + secrets.token_hex(12))
    bootstrap = bootstrap_token or secrets.token_urlsafe(32)
    app.extensions["bootstrap_token"] = bootstrap
    app.extensions["bootstrap_used"] = False
    bootstrap_lock = threading.Lock()

    @app.get('/healthz')
    def health():
        return jsonify(service='intent-slide', instance_id=instance_id)

    def json_object():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ContractError("JSON object required")
        return data

    @app.before_request
    def boundary():
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            return jsonify(error="loopback host required"), 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="cross-origin request rejected"), 403
        if request.path.startswith("/api/") and request.path != "/api/v2/session":
            if not session.get("authenticated"):
                return jsonify(error="실행 터미널의 접속 링크로 열어 주세요"), 401
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf_token", "missing")):
                    return jsonify(error="invalid CSRF token"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return response

    @app.errorhandler(Conflict)
    def conflict(exc):
        return jsonify(error=str(exc), code="CONFLICT"), 409

    @app.errorhandler(ContractError)
    def contract(exc):
        return jsonify(error=str(exc), code="CONTRACT_ERROR"), 422

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error="첨부 파일은 100 MB 이하만 지원합니다"), 413

    @app.route("/api/v2/session", methods=["GET", "POST"])
    def browser_session():
        if request.method == "POST":
            data = json_object()
            with bootstrap_lock:
                if app.extensions["bootstrap_used"] or not secrets.compare_digest(str(data.get("bootstrap_token", "")), bootstrap):
                    return jsonify(error="접속 링크가 만료되었습니다. 서버 실행 링크를 다시 확인해 주세요"), 401
                app.extensions["bootstrap_used"] = True
            session.clear()
            session.update(authenticated=True, csrf_token=secrets.token_urlsafe(32))
        if not session.get("authenticated"):
            return jsonify(error="실행 터미널의 접속 링크로 열어 주세요"), 401
        return jsonify(csrf_token=session["csrf_token"])

    @app.get("/api/v2/runs")
    def list_runs():
        return jsonify(runs=engine.list())

    @app.post("/api/v2/runs")
    def create_run():
        data = json_object()
        selection = mvp_selection(data.get("execution"))
        result = engine.create(data.get("title", "새 프레젠테이션"), data.get("request", ""), data.get("source_mode", "hybrid"), data.get("operation_id", ""), execution=selection)
        return jsonify(result), 201

    @app.get("/api/v2/runs/<run_id>")
    def snapshot(run_id):
        return jsonify(engine.snapshot(run_id))

    @app.post("/api/v2/runs/<run_id>/commands")
    def command(run_id):
        data = json_object()
        name = data.get("command")
        if name == "configure_provider":
            mvp_selection(data.get("payload", {}))
        elif name in {"run", "resume", "approve", "answer", "message"}:
            # These commands can queue work, including automatic follow-up jobs.
            # Preserve legacy records until the user explicitly switches to Codex.
            mvp_selection(engine.snapshot(run_id).get("execution"))
        if name == "provider_answer":
            if not runner:
                raise ContractError("실행기가 연결되지 않았습니다")
            payload = data.get("payload")
            if not isinstance(payload, dict) or any(not payload.get(key) for key in ("question_id", "job_id", "provider_id")):
                raise ContractError("question_id, job_id and provider_id are required")
            result = runner.respond(run_id, data.get("payload", {}), data.get("operation_id"), data.get("expected_revision"))
        else:
            result = engine.command(run_id, name, data.get("payload", {}), data.get("operation_id"), data.get("expected_revision"))
        if runner and name == "cancel":
            runner.cancel(run_id)
        if runner and name in {"run", "resume", "approve", "answer", "message"}:
            if name in {"approve", "answer", "message"} and (result["active_phase"] == "clarification" or not any(r["status"] == "PENDING" for r in result["reviews"])) and not any(q["status"] == "PENDING" for q in result["questions"]):
                if not any(j["status"] in {"RUNNING", "QUEUED", "WAITING_USER"} for j in result["jobs"]) and result["status"] != "COMPLETE":
                    result = engine.command(run_id, "run", {}, f"auto-{data['operation_id']}", result["revision"])
            runner.kick()
        return jsonify(result)

    @app.post("/api/v2/runs/<run_id>/attachments")
    def attachment(run_id):
        file = request.files.get("file")
        if not file or not file.filename:
            raise ContractError("첨부할 파일이 필요합니다")
        try:
            revision = int(request.form.get("expected_revision", ""))
        except ValueError:
            raise ContractError("expected_revision is required") from None
        return jsonify(engine.add_attachment(run_id, file.filename, file.read(), request.form.get("operation_id", ""), revision))

    @app.get("/api/v2/runs/<run_id>/artifacts/<artifact_id>")
    def artifact(run_id, artifact_id):
        path = engine.artifact_path(run_id, artifact_id)
        # Untrusted HTML/SVG must not execute in this authenticated origin.
        active = path.suffix.lower() in {".html", ".htm", ".svg", ".js", ".mjs"}
        response = send_file(path, as_attachment=request.args.get("download") == "1", mimetype="text/plain" if active else None)
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        return response

    @app.get("/api/v2/runs/<run_id>/events")
    def events(run_id):
        try:
            after = max(int(request.args.get("after", "0")), int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            raise ContractError("event sequence must be an integer") from None
        engine.store.read(run_id)
        def stream():
            cursor = after
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                rows = engine.events(run_id, cursor)
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if not rows:
                    yield ": keepalive\n\n"
                    time.sleep(0.5)
        return Response(stream(), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/v2/capabilities")
    def capabilities():
        return jsonify(runner.capabilities() if runner else {"ready": False, "reason": "runner not configured"})

    @app.post("/api/v2/capabilities/refresh")
    def refresh_capabilities():
        if not runner:
            raise ContractError("읽기 전용 작업실에서는 AI 연결을 확인할 수 없습니다")
        data = json_object()
        return jsonify(runner.preflight(data.get("provider")))

    @app.post('/api/v2/providers/login')
    def provider_login():
        if not runner:
            raise ContractError('읽기 전용 작업실입니다')
        data = json_object()
        if set(data) != {'provider'}:
            raise ContractError('provider만 지정하세요')
        return jsonify(runner.login(data['provider']))

    @app.post('/api/v2/capabilities/discover')
    def discover():
        if not runner:
            return jsonify(ready=False, providers=[], discovery={'status':'DEFERRED', 'reason':'읽기 전용 작업실입니다'})
        data = json_object()
        if set(data) - {'refresh'} or ('refresh' in data and type(data['refresh']) is not bool):
            raise ContractError('refresh must be a boolean')
        return jsonify(runner.discover(refresh=data.get('refresh', False)))

    @app.get("/")
    def index():
        return send_from_directory(console_dir, "index.html")

    @app.get("/<path:name>")
    def assets(name):
        if name.startswith(".") or any(p.startswith(".") for p in Path(name).parts) or Path(name).suffix not in {".css", ".mjs", ".js", ".svg", ".woff2", ".png"}:
            return jsonify(error="not found"), 404
        return send_from_directory(console_dir, name)

    return app
