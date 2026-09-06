#!/usr/bin/env python3
"""Project-local setup, diagnostic and launcher. Never installs provider CLIs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path("projects/presentation-agent-suite")
PROVIDERS = {"codex", "claude"}


class SetupError(RuntimeError):
    pass


def environment_python(root: Path) -> Path:
    return root / ".venv/bin/python"


def _local_path(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise SetupError(f"{name}은 저장소 안의 실제 경로여야 합니다. 기존 경로를 자동 변경하지 않습니다.")
    return path


def _locked_versions(root: Path) -> dict[str, str]:
    lock = _local_path(root, "requirements-local.lock")
    if not lock.is_file():
        raise SetupError("requirements-local.lock이 없습니다. 완전한 배포본을 사용하세요.")
    versions = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock.read_text(), re.MULTILINE))
    if not versions:
        raise SetupError("잠금 의존성 목록이 비어 있습니다.")
    return versions


@contextmanager
def _setup_lock(runtime: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise SetupError("현재 설치형 MVP는 macOS용입니다.") from exc
    lock = runtime / "setup.lock"
    if lock.is_symlink():
        raise SetupError("설치 잠금 경로는 symlink일 수 없습니다.")
    with lock.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SetupError("다른 설치가 진행 중입니다. 완료 후 다시 실행하세요.") from exc
        yield


def setup_environment(root: Path, *, run=subprocess.run) -> dict:
    """Install only into root/.venv using exact versions and wheel hashes."""
    root = root.resolve()
    if sys.version_info[:2] != (3, 12):
        raise SetupError("Python 3.12가 필요합니다. Python 설치 후 ./intent-slide setup을 실행하세요.")
    venv = _local_path(root, ".venv")
    runtime = _local_path(root, ".runtime")
    lockfile = _local_path(root, "requirements-local.lock")
    expected_versions = _locked_versions(root)
    runtime.mkdir(exist_ok=True)
    python = environment_python(root)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(runtime)
    fingerprint = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    state_path = _local_path(root, ".runtime/setup-state.json")
    with _setup_lock(runtime):
        if not venv.exists():
            result = run([sys.executable, "-m", "venv", str(venv)], cwd=root, env=environment, timeout=120)
            if result.returncode:
                raise SetupError("저장소 내부 Python 환경을 만들지 못했습니다. 위 오류를 확인하세요.")
        if not venv.is_dir() or not python.is_file() or not (venv / "pyvenv.cfg").is_file():
            raise SetupError(".venv가 불완전합니다. 기존 경로를 자동 삭제하지 않습니다.")
        prefix_check = "import sys; from pathlib import Path; sys.exit(0 if Path(sys.prefix).resolve() == Path(sys.argv[1]).resolve() and sys.prefix != sys.base_prefix else 1)"
        result = run([str(python), "-c", prefix_check, str(venv)], cwd=root, env=environment, timeout=30)
        if result.returncode:
            raise SetupError("선택된 Python이 저장소 .venv 소속이 아닙니다. 설치를 중단합니다.")
        try:
            previous = json.loads(state_path.read_text()) if state_path.is_file() else {}
        except (ValueError, OSError):
            previous = {}
        desired = {"schema_version": "local-setup.v1", "lock_sha256": fingerprint, "python": "3.12"}
        versions_check = "import importlib.metadata as m,json,sys; expected=json.loads(sys.argv[1]); sys.exit(0 if all(m.version(k)==v for k,v in expected.items()) else 1)"
        version_command = [str(python), "-c", versions_check, json.dumps(expected_versions)]
        installed = run(version_command, cwd=root, env=environment, timeout=30, capture_output=True)
        if previous != desired or installed.returncode:
            state_path.unlink(missing_ok=True)
            result = run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                          "--no-cache-dir", "--require-hashes", "--only-binary=:all:", "-r", str(lockfile)],
                         cwd=root, env=environment, timeout=600)
            if result.returncode:
                raise SetupError("잠금 의존성 설치에 실패했습니다. 네트워크·Python 버전과 위 오류를 확인하세요.")
        result = run([str(python), "-m", "pip", "check"], cwd=root, env=environment, timeout=60)
        if result.returncode:
            state_path.unlink(missing_ok=True)
            raise SetupError("설치된 의존성 조합이 일치하지 않습니다. setup을 다시 실행하세요.")
        exact = run(version_command, cwd=root, env=environment, timeout=30, capture_output=True)
        if exact.returncode:
            state_path.unlink(missing_ok=True)
            raise SetupError("설치 결과가 잠금 버전과 다릅니다. 준비 완료로 기록하지 않습니다.")
        descriptor, name = tempfile.mkstemp(prefix="setup-state-", suffix=".json", dir=runtime)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(desired, stream, sort_keys=True)
                stream.write("\n")
            temporary.replace(state_path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"status": "READY", "environment": ".venv", "lock_sha256": fingerprint,
            "scope": "Python 의존성 설치 완료; 모델 로그인·실제 렌더 성공과는 별개"}


def inspect_environment(root: Path, *, provider: str, which=shutil.which, system=None,
                        python_version=None, package_versions=None) -> dict:
    """Read-only presence/version checks. Never reads credential files."""
    if provider not in PROVIDERS:
        raise SetupError("provider는 codex 또는 claude여야 합니다.")
    system = sys.platform if system is None else system
    python_version = sys.version_info[:2] if python_version is None else python_version
    versions = {}
    expected = _locked_versions(root)
    if package_versions is None:
        for name in expected:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                pass
    else:
        versions = package_versions
    missing = [name for name in expected if name not in versions]
    mismatched = {name: {"expected": version, "actual": versions[name]} for name, version in expected.items()
                  if name in versions and versions[name] != version}
    officecli = which("officecli")
    swift, quicklook = which("swift"), which("qlmanage")
    renderer = "officecli" if officecli else ("macos-quicklook-webkit" if system == "darwin" and swift and quicklook else None)
    installed = bool(which(provider))
    python_ok = tuple(python_version) == (3, 12)
    base_ready = system == "darwin" and python_ok and not missing and not mismatched
    return {"ready": base_ready and installed and bool(renderer),
            "scope": "도구 존재·의존성만 확인. 실제 로그인과 파일 렌더는 별도 검사합니다.",
            "platform": {"name": system, "supported": system == "darwin"},
            "python": {"version": ".".join(map(str, python_version)), "supported": python_ok},
            "dependencies": {"missing": missing, "mismatched": mismatched},
            "provider": {"id": provider, "installed": installed, "auth_status": "UNVERIFIED",
                         "next_action": f"{provider}의 공식 로컬 로그인 절차를 완료한 뒤 doctor --connect로 확인하세요."},
            "renderer": {"name": renderer, "available": bool(renderer), "verification_status": "UNVERIFIED",
                         "next_action": "실제 후보의 G4 렌더가 성공해야 합니다. QuickLook에는 Swift가 필요합니다."},
            "fonts": {"bundled_pretendard": (root / ".claude/skills/ppt-master/assets/fonts/Pretendard/LICENSE.txt").is_file(),
                      "system_installation": "UNVERIFIED", "next_action": "글꼴을 자동으로 전역 설치하지 않습니다. 다른 컴퓨터의 PPTX 표시는 별도 확인하세요."}}


def connected_provider_check(root: Path, provider: str) -> dict:
    sys.path.insert(0, str(root / SUITE / "src"))
    try:
        from presentation_agents.v2.provider_registry import provider_factory
        with provider_factory(provider) as adapter:
            result = adapter.preflight()
        # Do not copy account objects, raw transport frames or credentials into diagnostics.
        return {key: result.get(key) for key in ("provider", "ready", "auth_mode", "reason", "features")}
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return {"provider": provider, "ready": False,
                "reason": f"선택한 CLI 연결 확인 실패 ({type(exc).__name__}). 공식 로그인 절차와 CLI 설치를 확인하세요."}


def server_command(root: Path, *, provider: str, port: int, no_runner: bool) -> list[str]:
    if provider not in PROVIDERS or not 1024 <= port <= 65535:
        raise SetupError("provider 또는 포트가 올바르지 않습니다. 포트 범위: 1024~65535")
    command = [str(environment_python(root)), str(root / SUITE / "scripts/presentation_console.py"),
               "--provider", provider, "--port", str(port), "--data-dir", str(root / SUITE / ".runtime/live")]
    command.append("--no-runner" if no_runner else "--preflight")
    return command


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Intent-Slide macOS 로컬 설치·진단·실행")
    parser.add_argument("command", choices=["setup", "doctor", "start"], nargs="?", default="start")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="codex")
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--connect", action="store_true", help="doctor에서 선택한 CLI의 공식 로그인 준비 상태 확인")
    parser.add_argument("--no-runner", action="store_true", help="AI 실행 없이 로컬 화면과 기록만 열기")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = inspect_environment(ROOT, provider=args.provider)
            if args.connect:
                result["connection"] = connected_provider_check(ROOT, args.provider)
                result["ready"] = result["ready"] and result["connection"].get("ready") is True
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 1
        if sys.platform != "darwin":
            raise SetupError("현재 설치형 제품은 macOS 지원입니다. Windows/Linux 실사용 검증은 완료되지 않았습니다.")
        setup = setup_environment(ROOT)
        if args.command == "setup":
            print(json.dumps(setup, ensure_ascii=False, indent=2))
            return 0
        command = server_command(ROOT, provider=args.provider, port=args.port, no_runner=args.no_runner)
        os.execv(command[0], command)
    except (SetupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Intent-Slide: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
