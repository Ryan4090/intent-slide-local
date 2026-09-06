"""SQLite aggregate store with transactional commands and replayable events."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .contracts import Conflict, ContractError, canonical, digest, now
from .provider_registry import normalize_selection


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "control.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(
                    run_id TEXT NOT NULL, id TEXT NOT NULL, payload_sha TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id));
                CREATE INDEX IF NOT EXISTS event_run ON events(run_id,seq);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def read(self, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ContractError("작업을 찾을 수 없습니다")
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM runs ORDER BY rowid DESC")]

    def create(self, body: dict, operation_id: str) -> dict:
        def creation_payload(value):
            return {**{k: value[k] for k in ("title", "request", "source_mode")},
                    "execution": normalize_selection(value.get("execution"))}
        payload_sha = digest(creation_payload(body))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload_sha,result FROM operations WHERE run_id='' AND id=?", (operation_id,)).fetchone()
            if previous:
                prior_body = json.loads(previous[1])
                prior_sha = digest(creation_payload(prior_body))
                legacy_sha = digest({k: prior_body[k] for k in ("title", "request", "source_mode")})
                # Old receipts omitted execution. Compare the immutable initial
                # result with normalized Codex defaults; never reuse it for a
                # different provider, and never rewrite historical receipts.
                if previous[0] not in {prior_sha, legacy_sha} or prior_sha != payload_sha:
                    raise Conflict("creation operation ID was reused with different input")
                return prior_body
            db.execute("INSERT INTO runs VALUES(?,?)", (body["id"], canonical(body)))
            self._event(db, body, "run.created", "작업을 만들었습니다")
            db.execute("INSERT INTO operations VALUES('',?,?,?)", (operation_id, payload_sha, canonical(body)))
            db.commit()
        return body

    def mutate(self, run_id: str, kind: str, payload: dict, apply: Callable, *, operation_id: str | None = None, expected_revision: int | None = None) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            payload_sha = digest({"kind": kind, "payload": payload})
            if operation_id:
                previous = db.execute("SELECT payload_sha,result FROM operations WHERE run_id=? AND id=?", (run_id, operation_id)).fetchone()
                if previous:
                    if previous[0] != payload_sha:
                        raise Conflict("operation ID was reused with a different payload")
                    return json.loads(previous[1])
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ContractError("작업을 찾을 수 없습니다")
            body = json.loads(row[0])
            if expected_revision is not None and expected_revision != body["revision"]:
                raise Conflict("화면이 갱신되었습니다. 현재 결과를 다시 확인해 주세요")
            message = apply(body)
            body["revision"] += 1
            body["updated_at"] = now()
            db.execute("UPDATE runs SET body=? WHERE id=?", (canonical(body), run_id))
            self._event(db, body, kind, message or kind)
            if operation_id:
                db.execute("INSERT INTO operations VALUES(?,?,?,?)", (run_id, operation_id, payload_sha, canonical(body)))
            db.commit()
        return body

    @staticmethod
    def _event(db, body, kind, message):
        value = {"kind": kind, "message": message, "created_at": now(), "revision": body["revision"], "run_id": body["id"]}
        db.execute("INSERT INTO events(run_id,revision,body) VALUES(?,?,?)", (body["id"], body["revision"], canonical(value)))

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        self.read(run_id)
        with self.connect() as db:
            rows = db.execute("SELECT seq,body FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500", (run_id, after))
            return [{**json.loads(body), "seq": seq} for seq, body in rows]

    def recent_events(self, run_id: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = list(db.execute("SELECT seq,body FROM events WHERE run_id=? ORDER BY seq DESC LIMIT ?", (run_id, limit)))
        return [{**json.loads(body), "seq": seq} for seq, body in reversed(rows)]
