#!/usr/bin/env python3
"""Prepare only this clone's hash-bound runtime; never download or change globals."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 4 * 1024**3


def platform_id() -> str:
    machine = platform.machine().lower()
    arch = 'arm64' if machine in {'arm64', 'aarch64'} else 'x64' if machine in {'x86_64', 'amd64'} else None
    if (sys.platform, arch) not in {('darwin', 'arm64'), ('darwin', 'x64'), ('win32', 'x64')}:
        raise RuntimeError('이 배포본은 Windows x64 및 Mac Intel/Apple Silicon용입니다. Linux와 Windows ARM은 지원하지 않습니다.')
    return f'{sys.platform}-{arch}'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def inside(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
            or '\0' in relative or PurePosixPath(relative).is_absolute()
            or '..' in PurePosixPath(relative).parts):
        raise RuntimeError('잘못된 내장 파일 경로입니다.')
    if _link(root):
        raise RuntimeError('내장 실행 폴더는 실제 디렉터리여야 합니다.')
    root = root.absolute()
    path = root / relative
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if _link(current):
            raise RuntimeError('내장 실행 경로의 symlink/reparse point는 허용하지 않습니다.')
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('내장 파일 경로가 저장소 밖을 가리킵니다.')
    return path


def _spec(spec: dict) -> None:
    if (not isinstance(spec, dict) or not re.fullmatch('[0-9a-f]{64}', str(spec.get('sha256', '')))
            or not isinstance(spec.get('bytes'), int) or isinstance(spec['bytes'], bool)
            or not 0 < spec['bytes'] <= MAX_ARCHIVE):
        raise RuntimeError('내장 파일 해시 또는 크기 계약이 올바르지 않습니다.')


def verify(path: Path, spec: dict) -> None:
    _spec(spec)
    if not path.is_file() or _link(path) or path.stat().st_size != spec['bytes'] or sha256(path) != spec['sha256']:
        raise RuntimeError(f'내장 파일 검증 실패: {path.name}. 완전한 저장소를 사용하세요.')


def _preserve(path: Path) -> None:
    """Recover only owned portable caches; retain interrupted bytes for inspection."""
    if _link(path):
        raise RuntimeError('기존 symlink 실행 폴더를 자동 변경하지 않습니다.')
    path.rename(path.with_name(path.name + '.recovered-' + uuid.uuid4().hex))


def materialize(spec: dict, cache: Path, *, root: Path = ROOT) -> Path:
    _spec(spec)
    if not spec.get('parts'):
        result = inside(root, spec['path'])
        verify(result, spec)
        return result
    if not isinstance(spec['parts'], list) or not 1 <= len(spec['parts']) <= 128:
        raise RuntimeError('잘못된 내장 분할 파일 목록입니다.')
    result = inside(cache, PurePosixPath(spec['path']).name)
    parts = [inside(root, part['path']) for part in spec['parts']]
    if len(set(parts)) != len(parts):
        raise RuntimeError('분할 파일이 중복되었습니다.')
    for path, part in zip(parts, spec['parts']):
        verify(path, part)
    if sum(part['bytes'] for part in spec['parts']) != spec['bytes']:
        raise RuntimeError('분할 파일 전체 크기가 일치하지 않습니다.')
    if result.exists():
        try:
            verify(result, spec)
            return result
        except RuntimeError:
            _preserve(result)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name)
            for path in parts:
                with path.open('rb') as data:
                    shutil.copyfileobj(data, stream, 1024*1024)
            stream.flush()
            os.fsync(stream.fileno())
        verify(temporary, spec)
        temporary.replace(result)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def _members(bundle: tarfile.TarFile) -> list:
    members = bundle.getmembers()
    if len(members) > 100000 or sum(member.size for member in members) > MAX_ARCHIVE:
        raise RuntimeError('내장 압축 파일 크기가 제한을 넘었습니다.')
    found = {}
    result = []
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
            raise RuntimeError('내장 압축 파일 경로 또는 형식이 올바르지 않습니다.')
        name = str(path)
        if name in found:
            if member.isdir() and found[name].isdir():
                continue
            raise RuntimeError('내장 압축 파일 경로가 중복되었습니다.')
        found[name] = member
        result.append(member)
    return result


def _tree_matches(bundle: tarfile.TarFile, members: list, destination: Path) -> bool:
    allowed = {'.intent-archive-sha256'}
    try:
        for member in members:
            name = str(PurePosixPath(member.name))
            path = destination / name
            allowed.add(name)
            allowed.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
            if not path.resolve().is_relative_to(destination.resolve()):
                return False
            if member.issym():
                if not path.is_symlink() or os.readlink(path) != member.linkname:
                    return False
            elif member.isdir():
                if not path.is_dir() or _link(path):
                    return False
            else:
                if not path.is_file() or _link(path):
                    return False
                with bundle.extractfile(member) as stream:
                    expected = hashlib.file_digest(stream, 'sha256').hexdigest()
                if sha256(path) != expected:
                    return False
                if os.name != 'nt' and (path.stat().st_mode & 0o111) != (member.mode & 0o111):
                    return False
        for parent, directories, files in os.walk(destination, followlinks=False):
            for name in directories + files:
                if (Path(parent) / name).relative_to(destination).as_posix() not in allowed:
                    return False
        return True
    except (OSError, ValueError, tarfile.TarError):
        return False


def extract(spec: dict, destination: Path, cache: Path, *, root: Path = ROOT) -> Path:
    archive = materialize(spec, cache, root=root)
    if _link(destination):
        raise RuntimeError('내장 실행 폴더는 실제 디렉터리여야 합니다.')
    marker = destination / '.intent-archive-sha256'
    with tarfile.open(archive, 'r:*') as bundle:
        members = _members(bundle)
        if destination.exists():
            if (marker.is_file() and not _link(marker)
                    and marker.read_text(encoding='ascii').strip() == spec['sha256']
                    and _tree_matches(bundle, members, destination)):
                return destination
            _preserve(destination)
        with tempfile.TemporaryDirectory(dir=cache, prefix='extract-') as work:
            staging = Path(work) / 'content'
            staging.mkdir()
            bundle.extractall(staging, members=members, filter='data')
            if not _tree_matches(bundle, members, staging):
                raise RuntimeError('압축 해제 결과가 원본 파일 목록과 다릅니다.')
            (staging / '.intent-archive-sha256').write_text(spec['sha256']+'\n', encoding='ascii')
            staging.replace(destination)
    return destination


@contextmanager
def setup_lock(runtime: Path, *, timeout: float = 360):
    lock = inside(runtime, 'setup.lock')
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'r+b') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError('설치 잠금 파일이 올바르지 않습니다.')
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'\0'); stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('다른 초기 설치가 진행 중입니다. 잠시 후 다시 실행하세요.') from None
                time.sleep(.1)
        yield


def clean_environment(runtime: Path) -> dict:
    # Native provider authentication variables are retained. Python/pip startup
    # hooks, foreign venv activation and dynamic-library injection are not.
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('PYTHON', 'PIP_', 'DYLD_', 'LD_'))
           and k.upper() not in {'VIRTUAL_ENV', '__PYVENV_LAUNCHER__', 'INTENT_SLIDE_CODEX',
                                 'INTENT_SLIDE_RUNTIME_PYTHON', 'INTENT_SLIDE_SOFFICE'}}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1',
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               TMPDIR=str(runtime), TMP=str(runtime), TEMP=str(runtime))
    return env


def _venv_matches(environment: Path, executable: Path, python: Path, python_dir: Path) -> bool:
    try:
        config = environment/'pyvenv.cfg'
        if (not executable.is_file() or not config.is_file() or _link(config)
                or _link(executable.parent) or config.stat().st_size > 65536):
            return False
        values = dict(line.split('=', 1) for line in config.read_text().splitlines() if '=' in line)
        values = {key.strip(): value.strip() for key, value in values.items()}
        if (Path(values.get('home', '')).resolve() != python.parent.resolve()
                or values.get('include-system-site-packages', '').lower() != 'false'):
            return False
        for parent, directories, files in os.walk(environment, followlinks=False):
            for name in directories + files:
                path = Path(parent)/name
                if _link(path) and not (path.resolve().is_relative_to(environment.resolve())
                                        or path.resolve().is_relative_to(python_dir.resolve())):
                    return False
            # None of the reviewed runtime wheels requires Python startup
            # hooks. A foreign .pth/sitecustomize must not run before checking
            # installed versions or repairing an interrupted environment.
            if Path(parent).name == 'site-packages' and any(
                    name.lower().endswith('.pth') or name.lower().split('.', 1)[0] in {'sitecustomize', 'usercustomize'}
                    for name in directories + files):
                return False
        if sys.platform == 'win32':
            launchers = [python, python_dir/'python/Lib/venv/scripts/nt/python.exe']
            return not _link(executable) and sha256(executable) in {sha256(p) for p in launchers if p.is_file()}
        return executable.resolve() == python.resolve()
    except (OSError, ValueError):
        return False


def _prepare(root: Path, *, run=subprocess.run) -> tuple[Path, dict]:
    manifest_path = inside(root, 'vendor/portable/runtime-manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    target = platform_id()
    spec = manifest['platforms'][target]
    runtime = inside(root, '.runtime/portable/'+target)
    cache = inside(runtime, 'archives'); cache.mkdir(exist_ok=True)
    lock = inside(root, 'requirements-local.lock')
    verify(lock, manifest['requirements_lock'])
    python_dir = extract(spec['python'], inside(runtime, 'base'), cache, root=root)
    python = inside(python_dir, spec['python']['entry'])
    environment = inside(runtime, 'environment')
    executable = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    wheelhouse = inside(runtime, 'wheelhouse'); wheelhouse.mkdir(exist_ok=True)
    for wheel in spec['wheels']:
        source = inside(root, wheel['path']); verify(source, wheel)
        destination = inside(wheelhouse, source.name)
        if destination.exists():
            try: verify(destination, wheel)
            except RuntimeError: _preserve(destination)
        if not destination.exists():
            temporary = destination.with_name(destination.name+'.partial-'+uuid.uuid4().hex)
            try:
                shutil.copyfile(source, temporary); verify(temporary, wheel); temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    env = clean_environment(runtime)
    # An incomplete venv is an owned disposable cache, not a user project.
    # Preserve it and recreate at the final path so entrypoint shebangs stay valid.
    if environment.exists() and not _venv_matches(environment, executable, python, python_dir):
        _preserve(environment)
    if not environment.exists():
        run([str(python), '-I', '-B', '-m', 'venv', str(environment)], check=True, timeout=120, env=env, cwd=root)
    if not _venv_matches(environment, executable, python, python_dir):
        raise RuntimeError('내장 Python 환경 생성이 완료되지 않았습니다. 다시 실행하면 복구합니다.')
    expected = dict(re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', lock.read_text(), re.MULTILINE))
    if not expected:
        raise RuntimeError('잠금 의존성 목록이 비어 있습니다.')
    check = ('import importlib,importlib.metadata as m,json,sys; from pathlib import Path; e=json.loads(sys.argv[2]); '
             'modules={"flask":"flask","pymupdf":"pymupdf","pillow":"PIL.Image","python-pptx":"pptx",'
             '"xlsxwriter":"xlsxwriter","requests":"requests","beautifulsoup4":"bs4","openpyxl":"openpyxl","numpy":"numpy"}; '
             '[importlib.import_module(modules[k.lower()]) for k in e if k.lower() in modules]; '
             'sys.exit(0 if Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve() and all(m.version(k)==v for k,v in e.items()) else 1)')
    desired = hashlib.sha256(json.dumps({'lock':sha256(lock), 'python':spec['python']['sha256'],
                                        'wheels':[w['sha256'] for w in spec['wheels']]}, sort_keys=True).encode()).hexdigest()
    receipt = inside(environment, '.intent-dependencies-sha256')
    command = [str(executable), '-I', '-B', '-c', check, str(environment), json.dumps(expected)]
    valid = run(command, env=env, cwd=root, timeout=30, capture_output=True).returncode == 0
    if not receipt.is_file() or receipt.read_text(encoding='ascii').strip() != desired or not valid:
        receipt.unlink(missing_ok=True)
        print('내장 실행 환경을 준비하고 있습니다. 외부 다운로드는 하지 않습니다.', flush=True)
        run([str(executable), '-I', '-B', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-input',
             '--no-cache-dir', '--force-reinstall', '--require-hashes', '--only-binary=:all:', '--find-links', str(wheelhouse),
             '-r', str(lock)], check=True, timeout=300, env=env, cwd=root)
        run(command, check=True, env=env, cwd=root, timeout=30, capture_output=True)
        run([str(executable), '-I', '-B', '-m', 'pip', '--isolated', 'check'], check=True, timeout=30, env=env, cwd=root)
        temporary = receipt.with_name(receipt.name+'.partial-'+uuid.uuid4().hex)
        try:
            temporary.write_text(desired+'\n', encoding='ascii'); temporary.replace(receipt)
        finally:
            temporary.unlink(missing_ok=True)
    codex = extract(spec['codex'], inside(runtime, 'codex'), cache, root=root)
    codex_binary = inside(codex, spec['codex']['entry'])
    bin_dir = inside(codex, spec['codex']['bin_dir'])
    renderer_path = inside(root, 'vendor/portable/renderer-manifest.json')
    renderer = json.loads(renderer_path.read_text(encoding='utf-8'))['platforms'][target]
    renderer_dir = extract(renderer, inside(runtime, 'renderer'), cache, root=root)
    soffice = inside(renderer_dir, renderer['entry'])
    if not all(p.is_file() for p in (codex_binary, soffice)):
        raise RuntimeError('내장 모델 또는 렌더 실행 파일이 없습니다.')
    env.update(INTENT_SLIDE_CODEX=str(codex_binary), INTENT_SLIDE_RUNTIME_PYTHON=str(executable),
               INTENT_SLIDE_SOFFICE=str(soffice),
               PATH=str(bin_dir)+os.pathsep+str(executable.parent)+os.pathsep+env.get('PATH',''))
    return executable, env


def prepare(*, root: Path = ROOT, run=subprocess.run, lock_timeout: float = 360) -> tuple[Path, dict]:
    root = Path(root).absolute()
    if _link(root):
        raise RuntimeError('저장소는 실제 디렉터리여야 합니다.')
    runtime = inside(root, '.runtime/portable/'+platform_id())
    runtime.mkdir(parents=True, exist_ok=True)
    with setup_lock(runtime, timeout=lock_timeout):
        return _prepare(root, run=run)


def main(argv=None, *, root: Path = ROOT) -> int:
    try:
        python, environment = prepare(root=root)
        args = (sys.argv[1:] if argv is None else argv) or ['start', '--open']
        return subprocess.call([str(python), '-I', '-B', str(root/'scripts/local_mvp.py'), *args], cwd=root, env=environment)
    except (OSError, RuntimeError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print('Intent-Slide: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
