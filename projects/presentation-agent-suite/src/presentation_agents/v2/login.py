"""Explicit browser login through the bundled official CLI; no credential parsing."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import threading
import time

from .contracts import ContractError
from .stdio_transport import StdioTransport


class BrowserLogin:
    def __init__(self, *, finished, launch=None, timeout=300):
        self.finished = finished
        self.launch = launch or StdioTransport.launch
        self.timeout = timeout
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.owner = None
        self.state = {'status': 'IDLE', 'provider': 'codex'}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self):
        with self.lock:
            if self.stop.is_set():
                raise ContractError('작업실을 다시 실행해 주세요')
            if self.state['status'] in {'STARTING', 'RUNNING'}:
                return self.snapshot()
            binary = os.environ.get('INTENT_SLIDE_CODEX', '')
            if not binary or not Path(binary).is_absolute() or not Path(binary).is_file():
                raise ContractError('내장 Codex 실행 파일이 없습니다. 완전한 배포본을 사용해 주세요')
            self.state = {'status': 'STARTING', 'provider': 'codex'}
            threading.Thread(target=self._run, args=(binary,), daemon=True, name='intent-browser-login').start()
            return self.snapshot()

    def _run(self, binary):
        owner = None
        readers = []
        outcome = 'FAILED'
        excessive = threading.Event()
        try:
            owner = self.launch([binary, 'login'], startup_timeout=30)
            with self.lock:
                self.owner = owner
                self.state['status'] = 'RUNNING'
            owner.process.stdin.close()

            def discard(stream):
                # CLI owns OAuth URLs/tokens. Never return, log or persist output.
                total = 0
                try:
                    while data := stream.read(8192):
                        total += len(data)
                        if total > 1024 * 1024:
                            excessive.set()
                            owner.terminate()
                            return
                except (OSError, ValueError):
                    pass

            for stream in (owner.process.stdout, owner.process.stderr):
                reader = threading.Thread(target=discard, args=(stream,), daemon=True)
                reader.start()
                readers.append(reader)
            deadline = time.monotonic() + self.timeout
            while owner.process.poll() is None:
                if self.stop.wait(0.1) or excessive.is_set():
                    outcome = 'CANCELLED'
                    owner.terminate()
                    break
                if time.monotonic() >= deadline:
                    outcome = 'TIMEOUT'
                    owner.terminate()
                    break
            else:
                outcome = 'COMPLETE' if owner.process.returncode == 0 and not excessive.is_set() else 'FAILED'
        except (OSError, RuntimeError, ValueError):
            outcome = 'FAILED'
        finally:
            if owner:
                owner.close()
            for reader in readers:
                reader.join(timeout=1)
            with self.lock:
                self.owner = None
            try:
                if not self.stop.is_set():
                    self.finished()
            finally:
                with self.lock:
                    self.state['status'] = outcome

    def close(self):
        self.stop.set()
        with self.lock:
            owner = self.owner
        if owner:
            owner.terminate()
