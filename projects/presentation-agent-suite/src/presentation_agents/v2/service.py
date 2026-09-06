"""One service owns job recovery/execution; CLI processes only enqueue commands."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .contracts import ContractError


class ServiceLease:
    """Process-scoped file lock released on exit, including native Windows."""
    def __init__(self, root: Path):
        self.root = root
        self.file = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / "service.lock"
        if lock.is_symlink():
            raise ContractError("서비스 잠금은 실제 파일이어야 합니다")
        self.file = lock.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise ContractError("이 저장소를 사용하는 SlideMaster 서비스가 이미 실행 중입니다") from exc
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
