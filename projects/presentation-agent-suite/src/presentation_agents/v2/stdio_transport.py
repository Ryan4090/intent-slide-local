"""Bounded binary stdio and owned process trees on POSIX and Windows.

Windows anonymous pipes do not support select(). One serialized writer thread
handles short writes while callers enforce deadlines. Windows CLI launch waits
for Job Object assignment, so no provider/tool process starts outside that job.
The job is process cleanup, not a replacement for each provider's sandbox.

Sources: https://docs.python.org/3/library/select.html
https://docs.python.org/3/library/subprocess.html
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_windows() -> bool:
    return os.name == 'nt'


def resolve_command(command: list[str]) -> list[str]:
    """Never hand a Windows .cmd/.bat to an implicit command shell.

    Only installed official npm package layouts are translated to node + JS.
    Read bounded public package metadata, never shim contents or auth files.
    """
    if not command or not all(isinstance(part, str) and '\0' not in part for part in command):
        raise TransportError('UNAVAILABLE', 'A valid local executable command is required')
    if not is_windows():
        return command[:]
    executable = shutil.which(command[0]) or command[0]
    path = Path(executable).absolute()
    if path.suffix.lower() not in {'.cmd', '.bat'}:
        return [str(path), *command[1:]]
    cli = path.stem.lower()
    # Official npm metadata: Claude 2.1.0 used cli.js; 2.1.263 exposes
    # bin/claude.exe. Both are installed native-flow entrypoints, not installers.
    layouts = {'codex': ('@openai/codex', ('bin/codex.js',)),
               'claude': ('@anthropic-ai/claude-code', ('cli.js', 'bin/claude.exe'))}
    if cli not in layouts:
        raise TransportError('UNSUPPORTED_LAUNCHER', 'Use a native executable; batch launchers are not executed')
    package, entries = layouts[cli]
    package_root = path.parent / 'node_modules' / package
    try:
        with (package_root/'package.json').open('rb') as stream:
            raw = stream.read(65537)
        metadata = json.loads(raw) if len(raw) <= 65536 else None
        entry = metadata.get('bin', {}).get(cli) if isinstance(metadata, dict) and isinstance(metadata.get('bin'), dict) else None
        if (not isinstance(metadata, dict) or metadata.get('name') != package
                or entry not in entries or metadata.get('bin') != {cli: entry}
                or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', str(metadata.get('version', '')))
                or not (package_root/entry).is_file()):
            raise ValueError()
    except (OSError, ValueError, RecursionError):
        raise TransportError('UNSUPPORTED_LAUNCHER', 'Official npm package metadata is missing or invalid; restore the native CLI') from None
    if entry.endswith('.exe'):
        return [str((package_root/entry).resolve()), *command[1:]]
    node = path.parent/'node.exe'
    if not node.is_file():
        found = shutil.which('node.exe')
        if not found:
            raise TransportError('UNAVAILABLE', 'The installed npm CLI requires its existing Node.js executable')
        node = Path(found)
    return [str(node.resolve()), str((package_root/entry).resolve()), *command[1:]]


class _WindowsJob:
    """Minimal documented Win32 Job API; only invoked on a native Windows host."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [('per_process_time', c.c_int64), ('per_job_time', c.c_int64),
                        ('flags', w.DWORD), ('min_working_set', c.c_size_t), ('max_working_set', c.c_size_t),
                        ('active_process_limit', w.DWORD), ('affinity', c.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Counters(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', Counters), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        self.api = c.WinDLL('kernel32', use_last_error=True)
        definitions = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.api, name); function.argtypes = args; function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise TransportError('PROCESS_ISOLATION', 'Windows could not create the owned process job')
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags.
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close()
            raise TransportError('PROCESS_ISOLATION', 'Windows could not enforce process cleanup')

    def assign(self, pid: int) -> None:
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise TransportError('PROCESS_ISOLATION', 'Windows could not attach the waiting launcher')
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise TransportError('PROCESS_ISOLATION', 'Windows process policy rejected job assignment; no CLI started')
        finally:
            self.api.CloseHandle(handle)

    def close(self) -> None:
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


class StdioTransport:
    @classmethod
    def launch(cls, command: list[str], *, cwd=None, env=None, max_frame_bytes=4*1024*1024,
               startup_timeout=30.0):
        if max_frame_bytes <= 0 or startup_timeout <= 0:
            raise ValueError('Positive transport bounds are required')
        command = resolve_command(command)
        job, process = None, None
        try:
            if is_windows():
                job = _WindowsJob()
                # The isolated interpreter starts only this local module, waits
                # for a gate byte, then launches the unmodified provider CLI.
                launch = [sys.executable, '-I', '-u', str(Path(__file__).resolve()), '--bridge', json.dumps(command)]
            else:
                launch = command
            process = subprocess.Popen(launch, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, shell=False,
                start_new_session=not is_windows(), close_fds=True)
            if job:
                job.assign(process.pid)
            owner = cls(process, job, max_frame_bytes)
            if job:
                owner.write_frame(b'\0', timeout=startup_timeout)
            return owner
        except (OSError, TransportError):
            if job:
                job.close()
            if process:
                try:
                    process.kill(); process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream: stream.close()
            raise

    def __init__(self, process, job, max_frame_bytes):
        self.process, self._job, self.max_frame_bytes = process, job, max_frame_bytes
        self._input = process.stdin
        self._queue = queue.Queue(maxsize=1)
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._terminate_lock = threading.Lock()
        self._writer = threading.Thread(target=self._write_loop, daemon=True, name='provider-stdio-writer')
        self._writer.start()

    def _write_loop(self):
        while not self._stopped.is_set():
            try: request = self._queue.get(timeout=.05)
            except queue.Empty: continue
            try:
                view = memoryview(request['data'])
                while view and not self._stopped.is_set():
                    written = self._input.write(view)
                    if not isinstance(written, int) or written <= 0:
                        raise OSError('short input write')
                    view = view[written:]
                if view:
                    raise OSError('transport stopped')
            except (OSError, ValueError) as error:
                request['error'] = error
            finally:
                request['data'] = b''
                request['done'].set()

    def write_frame(self, data: bytes, *, timeout: float):
        if len(data) > self.max_frame_bytes:
            raise TransportError('INPUT_LIMIT', 'Request exceeds the bounded input frame')
        deadline = time.monotonic()+timeout
        if not self._lock.acquire(timeout=max(0, timeout)):
            self.terminate()
            raise TransportError('REQUEST_TIMEOUT', 'Provider input deadline expired')
        try:
            if self._stopped.is_set():
                raise TransportError('CLOSED', 'Provider transport is closed')
            request = {'data':data, 'done':threading.Event(), 'error':None}
            self._queue.put_nowait(request)
            while not request['done'].wait(min(.05, max(0, deadline-time.monotonic()))):
                if self._stopped.is_set():
                    raise TransportError('CLOSED', 'Provider transport is closed')
                if time.monotonic() >= deadline:
                    self.terminate()
                    raise TransportError('REQUEST_TIMEOUT', 'Provider stopped reading input; reconnect before retrying')
            if request['error']:
                raise TransportError('PROCESS_EXIT', 'Provider input stream closed')
        finally:
            self._lock.release()

    def terminate(self):
        with self._terminate_lock:
            if self._stopped.is_set(): return
            self._stopped.set()
            if self._job:
                self._job.close()
            else:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try: os.killpg(self.process.pid, sig)
                    except (ProcessLookupError, OSError): pass
                    if sig == signal.SIGTERM:
                        try: self.process.wait(timeout=.5)
                        except subprocess.TimeoutExpired: pass
            try: self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try: self.process.kill(); self.process.wait(timeout=.5)
                except (OSError, subprocess.TimeoutExpired): pass

    def close(self):
        self.terminate()
        if self._writer is not threading.current_thread(): self._writer.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                try: stream.close()
                except (OSError, ValueError): pass


def probe_command(command: list[str], *, timeout: float, limit=65536, env=None) -> tuple[int, bytes]:
    """Read bounded public CLI metadata; stderr consumes the same output budget."""
    owner = StdioTransport.launch(command, env=env, startup_timeout=timeout)
    messages = queue.Queue(maxsize=32)
    stop = threading.Event()
    readers = []
    def read(stream, label):
        try:
            while not stop.is_set():
                chunk = stream.read(4096)
                while not stop.is_set():
                    try: messages.put((label, chunk), timeout=.05); break
                    except queue.Full: pass
                if not chunk: return
        except (OSError, ValueError):
            return
    try:
        owner.process.stdin.close()  # Native metadata commands receive EOF.
        for label, stream in [('stdout',owner.process.stdout),('stderr',owner.process.stderr)]:
            worker = threading.Thread(target=read,args=(stream,label),daemon=True)
            readers.append(worker); worker.start()
        deadline, size, active, output = time.monotonic()+timeout, 0, 2, bytearray()
        while active:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TransportError('REQUEST_TIMEOUT', 'Native metadata probe timed out')
            try: label, chunk = messages.get(timeout=min(.05,remaining))
            except queue.Empty: continue
            if not chunk: active -= 1; continue
            size += len(chunk)
            if size > limit:
                raise TransportError('OUTPUT_LIMIT', 'Native metadata exceeded its output limit')
            if label == 'stdout': output.extend(chunk)
        try: code = owner.process.wait(timeout=max(.01,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise TransportError('REQUEST_TIMEOUT', 'Native metadata probe timed out') from None
        return code, bytes(output)
    finally:
        stop.set(); owner.terminate()
        for worker in readers: worker.join(timeout=.5)
        owner.close()


def _bridge(command):
    # os.read avoids buffered read-ahead consuming the first JSON frame.
    if os.read(sys.stdin.fileno(), 1) != b'\0': return 1
    try:
        process = subprocess.Popen(command, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                                   stderr=sys.stderr.buffer, shell=False, close_fds=True)
        return process.wait()
    except OSError:
        return 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--bridge':
        sys.exit(_bridge(json.loads(sys.argv[2])))
    sys.exit(2)
