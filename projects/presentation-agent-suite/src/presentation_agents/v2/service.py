"""One service owns job recovery/execution; CLI processes only enqueue commands."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .contracts import ContractError


class ServiceLease:
    """Process-scoped flock released automatically on exit; no stale PID deletion."""
    def __init__(self, root: Path):
        self.root = root
        self.file = None

    def __enter__(self):
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = (self.root / "service.lock").open("a+")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            self.file = None
            raise ContractError("이 저장소를 사용하는 SlideMaster 서비스가 이미 실행 중입니다") from exc
        self.file.seek(0)
        self.file.truncate()
        self.file.write(str(os.getpid()))
        self.file.flush()
        return self

    def __exit__(self, *exc):
        if self.file:
            self.file.close()


class QueueWatch:
    """Wake the runner for HTTP and CLI jobs; no long-lived worker when idle."""
    def __init__(self, runner, interval=1.0):
        self.runner = runner
        self.interval = interval
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="slidemaster-queue-watch")

    def _loop(self):
        while not self.stop.is_set():
            self.runner.kick()
            self.stop.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=2)
