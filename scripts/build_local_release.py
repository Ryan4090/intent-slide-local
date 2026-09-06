#!/usr/bin/env python3
"""Build a deterministic local product ZIP from a committed Git tree only."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024 - 1
MAX_MEMORY_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAX_FILES = 50_000
ALLOW = (
    "intent-slide", "intent-slide.cmd", "Start Intent-Slide.command", "Start Intent-Slide.cmd",
    ".gitignore", ".gitattributes", "README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "requirements-local.txt", "requirements-local.lock", "requirements-upstream.txt",
    "scripts/local_mvp.py", "scripts/build_local_release.py", "tests/test_local_mvp.py", "tests/test_local_release.py",
    "scripts/portable_bootstrap.py", "scripts/build_portable_runtime.py", "scripts/build_portable_renderer.py",
    "scripts/verify_portable_clone.py",
    "tests/portable", ".github/workflows",
    "vendor", "licenses", "docs/local-mvp", "docs/rules", ".claude/skills", ".codex/skills",
    "projects/presentation-agent-suite/src", "projects/presentation-agent-suite/scripts",
    "projects/presentation-agent-suite/console", "projects/presentation-agent-suite/agents",
    "projects/presentation-agent-suite/tests",
)
EXCLUDE = {
    '.claude/skills/ppt-master/templates/brands/naver': 'NAVER prohibits logo redistribution in templates or downloads.',
    '.claude/skills/ppt-master/templates/decks/naver_ir': 'NAVER logo-dependent deck; excluded with its logo assets.',
    '.claude/skills/ppt-master/templates/icons/simple-icons/naver.svg': 'NAVER prohibits logo redistribution in templates or downloads.',
    '.claude/skills/ppt-master/templates/brands/jangpm/images/jangpm-character.png': 'Separate character redistribution permission is unverified.',
    '.claude/skills/ppt-master/templates/decks/jangpm/images/jangpm-character.png': 'Separate character redistribution permission is unverified.',
}
REQUIRED = (
    ".gitattributes",
    "intent-slide", "scripts/local_mvp.py", "requirements-local.lock", "THIRD_PARTY_NOTICES.md",
    "licenses/slidemaster-MIT.txt", "vendor/slidemaster-manifest.json", "vendor/runtime-dependencies.json",
    "projects/presentation-agent-suite/scripts/presentation_console.py",
    "projects/presentation-agent-suite/src/presentation_agents/v2/provider_registry.py",
    "projects/presentation-agent-suite/src/presentation_agents/v2/claude_provider.py",
    ".claude/skills/ppt-master/SKILL.md", ".claude/skills/ppt-master/scripts/verify_deck.py",
    ".claude/skills/ppt-master/assets/fonts/Pretendard/LICENSE.txt",
    "LICENSE", "intent-slide.cmd", "Start Intent-Slide.command", "Start Intent-Slide.cmd",
    "scripts/portable_bootstrap.py", "scripts/build_portable_runtime.py", "scripts/build_portable_renderer.py",
    "scripts/verify_portable_clone.py", ".github/workflows/portable-clone.yml",
    "vendor/portable/runtime-manifest.json", "vendor/portable/renderer-manifest.json",
    ".github/workflows/portable-renderer-build.yml",
)


class ReleaseError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exclusion_reason(name: str) -> str | None:
    return next((reason for prefix, reason in EXCLUDE.items()
                 if name == prefix or name.startswith(prefix + '/')), None)


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if (not name or str(path) != name or path.is_absolute() or ".." in path.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in name) or "\\" in name or ':' in name
            or unicodedata.normalize('NFC', name) != name):
        return False
    if any(part.casefold() in {".git", ".gitmodules", ".venv", ".runtime", "node_modules", "__pycache__", ".ds_store",
                               "auth.json", "credentials.json", ".credentials.json", "credentials", "id_rsa", "id_ed25519"}
           or part.casefold().startswith(".env") or part.endswith((' ', '.')) for part in path.parts):
        return False
    if exclusion_reason(name):
        return False
    return bool(re.fullmatch(r'tests/test_portable[^/]*\.py', name)) or any(name == prefix or name.startswith(prefix + "/") for prefix in ALLOW)


def read_commit_files(root: Path, reference: str) -> tuple[str, dict]:
    resolved = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", reference + "^{commit}"],
                              cwd=root, capture_output=True, text=True, timeout=30)
    commit = resolved.stdout.strip()
    if resolved.returncode or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
        raise ReleaseError("배포 기준은 이 저장소의 실제 commit이어야 합니다.")
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", commit], cwd=root, timeout=30)
    selected, excluded = {}, set()
    for item in tree.split(b"\0"):
        if not item:
            continue
        metadata, raw_name = item.split(b"\t", 1)
        mode, kind, _ = metadata.split()
        name = raw_name.decode("utf-8")
        if exclusion_reason(name) and kind == b'blob' and mode in {b'100644', b'100755'}:
            excluded.add(name)
        if allowed_path(name):
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise ReleaseError("배포 파일은 일반 Git blob이어야 합니다: " + name)
            selected[name] = stat.S_IMODE(int(mode, 8))
    missing = sorted(set(REQUIRED) - set(selected))
    if missing:
        raise ReleaseError("commit에 제품 필수 파일이 없습니다. 작업 트리를 대신 복사하지 않습니다: " + ", ".join(missing))
    roots = [prefix for prefix in ALLOW if any(name == prefix or name.startswith(prefix + "/") for name in selected)]
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit, "--", *roots], cwd=root, timeout=120)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            if member.isdir():
                continue
            if member.name in excluded and member.isfile():
                continue
            if member.name not in selected or not member.isfile():
                raise ReleaseError("허용 목록과 다른 archive entry: " + member.name)
            # Git owns the executable bit. tar.umask can make archive modes
            # 0664/0775; never import those transport permissions into the ZIP.
            files[member.name] = (source.extractfile(member).read(), selected[member.name])
    if set(files) != set(selected):
        raise ReleaseError("Git archive 결과가 선택한 파일 집합과 다릅니다.")
    return commit, files


def bundle_bytes(commit: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
        raise ReleaseError("유효한 source commit이 필요합니다.")
    if not files or len(files) > MAX_FILES or sum(len(data) for data, _ in files.values()) > MAX_MEMORY_BYTES:
        raise ReleaseError("배포 파일 개수 또는 크기가 상한을 넘었습니다.")
    _validate_index({name: {'bytes':len(data),'mode':mode} for name,(data,mode) in files.items()})
    entries = []
    for name, (data, mode) in sorted(files.items()):
        if not allowed_path(name) or mode not in {0o644, 0o755}:
            raise ReleaseError("허용되지 않은 배포 경로 또는 mode: " + name)
        entries.append({"path": name, "sha256": _sha(data), "bytes": len(data), "mode": mode})
    manifest = {"schema_version": "intent-slide-release.v1", "source_commit": commit, "file_count": len(entries),
                "method": "allowlisted committed Git objects; deterministic stored ZIP; no runtime or personal projects",
                "files": entries}
    baseline = files.get("vendor/slidemaster-manifest.json")
    if baseline:
        upstream = json.loads(baseline[0])
        manifest["upstream"] = {"source_commit": upstream["source_commit"],
            "modified_files": sorted(entry["path"] for entry in upstream["files"]
                                     if entry["path"] in files and _sha(files[entry["path"]][0]) != entry["sha256"]),
            "excluded_files": [{"path": entry["path"], "sha256": entry["sha256"],
                                "reason": exclusion_reason(entry["path"])} for entry in upstream["files"]
                               if entry["path"] not in files and exclusion_reason(entry["path"])],
            "boundary": "Upstream baseline is provenance. Downstream product changes and distribution exclusions are listed."}
    prefix = "Intent-Slide-" + commit[:12] + "/"
    payload = {**files, "release-manifest.json": (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n", 0o644)}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, (data, mode) in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data)
    return output.getvalue()


def verify_bundle(data: bytes) -> dict:
    """Small-fixture compatibility API; the CLI uses verify_bundle_file."""
    if len(data)>MAX_MEMORY_BYTES+MAX_MANIFEST_BYTES:
        raise ReleaseError('Use verify_bundle_file for a large release')
    return verify_bundle_file(io.BytesIO(data))


def publish_exclusive(target: Path, data: bytes) -> str:
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise ReleaseError("배포 대상 경로는 symlink일 수 없습니다.")
    if target.exists():
        if target.is_file() and target.read_bytes() == data:
            return "UNCHANGED"
        raise ReleaseError("다른 내용의 기존 배포를 덮어쓰지 않습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".intent-slide-release-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != data:
                raise ReleaseError("발행 중 다른 배포가 생겼습니다. 덮어쓰지 않습니다.")
            return "UNCHANGED"
        return "CREATED"
    finally:
        temporary.unlink(missing_ok=True)


def _validate_index(index: dict) -> None:
    if not index or len(index)>MAX_FILES or sum(row['bytes'] for row in index.values())>MAX_BYTES:
        raise ReleaseError('배포 파일 개수 또는 전체 크기가 상한을 넘었습니다.')
    folded=set()
    for name,row in index.items():
        key=unicodedata.normalize('NFC',name).casefold()
        if (not allowed_path(name) or key in folded or type(row.get('bytes')) is not int
                or not 0<=row['bytes']<=MAX_FILE_BYTES or row.get('mode') not in (0o644,0o755)):
            raise ReleaseError('배포 경로, 크기, 대소문자 충돌 또는 mode가 잘못되었습니다: '+name)
        folded.add(key)


def read_commit_index(root: Path, reference: str, *, required=None) -> tuple[str,dict]:
    """Read bounded metadata, never Git-archive all allowlisted prefixes in RAM."""
    resolved=subprocess.run(['git','rev-parse','--verify','--end-of-options',reference+'^{commit}'],
                            cwd=root,capture_output=True,text=True,timeout=30)
    commit=resolved.stdout.strip()
    if resolved.returncode or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',commit):
        raise ReleaseError('배포 기준은 이 저장소의 실제 commit이어야 합니다.')
    # Only bounded tree metadata is buffered; file contents use cat-file below.
    tree=subprocess.check_output(['git','ls-tree','-r','-l','-z',commit],cwd=root,timeout=30)
    if len(tree)>32*1024*1024:raise ReleaseError('Git tree metadata exceeds its bound')
    index={}
    try:
        for item in tree.split(b'\0'):
            if not item:continue
            metadata,raw_name=item.split(b'\t',1)
            mode,kind,oid,size=metadata.split()
            name=raw_name.decode('utf-8')
            if allowed_path(name):
                if kind!=b'blob' or mode not in (b'100644',b'100755') or not re.fullmatch(rb'[a-f0-9]{40}(?:[a-f0-9]{24})?',oid):
                    raise ReleaseError('배포 파일은 일반 Git blob이어야 합니다: '+name)
                if name in index:raise ReleaseError('Duplicate committed tree path')
                index[name]={'oid':oid.decode('ascii'),'bytes':int(size),'mode':stat.S_IMODE(int(mode,8))}
    except (UnicodeError,ValueError) as exc:
        raise ReleaseError('유효한 committed Git tree가 아닙니다.') from exc
    missing=sorted(set(REQUIRED if required is None else required)-index.keys())
    if missing:raise ReleaseError('commit에 제품 필수 파일이 없습니다. 작업 트리를 대신 복사하지 않습니다: '+', '.join(missing))
    _validate_index(index)
    return commit,index


class GitBlobReader:
    """One local Git batch process; bounded frames and an owned timeout."""
    def __init__(self,root: Path):
        self.process=subprocess.Popen(['git','cat-file','--batch'],cwd=root,stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,shell=False)
        self.timer=threading.Timer(600,self.process.kill);self.timer.daemon=True;self.timer.start()

    def __enter__(self):return self

    def chunks(self,name: str,row: dict):
        request=(row['oid']+'\n').encode('ascii')
        if self.process.stdin.write(request)!=len(request):raise ReleaseError('Git request write failed')
        header=self.process.stdout.readline(256)
        expected=f"{row['oid']} blob {row['bytes']}\n".encode('ascii')
        if header!=expected:raise ReleaseError('Git blob header differs from committed tree: '+name)
        remaining=row['bytes']
        while remaining:
            block=self.process.stdout.read(min(CHUNK_BYTES,remaining))
            if not block:raise ReleaseError('Git blob ended before its committed size: '+name)
            remaining-=len(block);yield block
        if self.process.stdout.read(1)!=b'\n':raise ReleaseError('Git blob framing is invalid')

    def __exit__(self,*_):
        self.timer.cancel()
        try:
            self.process.stdin.close()
            code=self.process.wait(timeout=5)
            if not _[0] and code!=0:raise ReleaseError('Git batch process did not complete successfully')
        except (OSError,subprocess.TimeoutExpired):
            self.process.kill();self.process.wait(timeout=5)
            if not _[0]:raise ReleaseError('Git batch process did not terminate within its deadline')
        finally:
            self.process.stdout.close()


def _zip_info(name: str,mode: int,size: int) -> zipfile.ZipInfo:
    info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
    info.external_attr=(stat.S_IFREG|mode)<<16;info.file_size=size
    return info


def _release_manifest(commit: str,entries: list[dict],baseline: bytes | None) -> dict:
    manifest={'schema_version':'intent-slide-release.v1','source_commit':commit,'file_count':len(entries),
              'method':'allowlisted committed Git objects; deterministic stored ZIP; no runtime or personal projects','files':entries}
    if baseline is not None:
        upstream=json.loads(baseline);indexed={row['path']:row for row in entries}
        manifest['upstream']={'source_commit':upstream['source_commit'],
            'modified_files':sorted(row['path'] for row in upstream['files'] if row['path'] in indexed and indexed[row['path']]['sha256']!=row['sha256']),
            'excluded_files':[{'path':row['path'],'sha256':row['sha256'],'reason':exclusion_reason(row['path'])}
                              for row in upstream['files'] if row['path'] not in indexed and exclusion_reason(row['path'])],
            'boundary':'Upstream baseline is provenance. Downstream product changes and distribution exclusions are listed.'}
    return manifest


def write_bundle_stream(commit: str,index: dict,output,read_blob) -> dict:
    """Write each committed blob once in bounded chunks; retain only metadata."""
    if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',commit):raise ReleaseError('Invalid source commit')
    _validate_index(index);prefix='Intent-Slide-'+commit[:12]+'/'
    entries=[];baseline=None
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for name,row in sorted(index.items()):
            sha=hashlib.sha256();count=0;capture=bytearray() if name=='vendor/slidemaster-manifest.json' else None
            with archive.open(_zip_info(prefix+name,row['mode'],row['bytes']),'w') as member:
                for block in read_blob(name,row):
                    if not isinstance(block,bytes) or len(block)>CHUNK_BYTES:raise ReleaseError('Blob stream chunk exceeds its bound')
                    count+=len(block)
                    if count>row['bytes']:raise ReleaseError('Blob stream exceeds committed size')
                    if capture is not None:
                        if count>MAX_MANIFEST_BYTES:raise ReleaseError('Upstream manifest exceeds its bound')
                        capture.extend(block)
                    sha.update(block);member.write(block)
            if count!=row['bytes']:raise ReleaseError('Blob stream does not match committed size')
            entries.append({'path':name,'sha256':sha.hexdigest(),'bytes':count,'mode':row['mode']})
            if capture is not None:baseline=bytes(capture)
        manifest=_release_manifest(commit,entries,baseline)
        data=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2).encode()+b'\n'
        if len(data)>MAX_MANIFEST_BYTES:raise ReleaseError('Release manifest exceeds its bound')
        archive.writestr(_zip_info(prefix+'release-manifest.json',0o644,len(data)),data)
    return manifest


def _read_zip_small(archive: zipfile.ZipFile,name: str) -> bytes:
    info=archive.getinfo(name)
    if info.file_size>MAX_MANIFEST_BYTES:raise ReleaseError('JSON manifest exceeds its bound')
    with archive.open(info) as stream:
        data=stream.read(MAX_MANIFEST_BYTES+1)
    if len(data)!=info.file_size:raise ReleaseError('JSON manifest size mismatch')
    return data


def _portable_bindings(archive: zipfile.ZipFile,prefix: str,indexed: dict) -> None:
    manifests={'vendor/portable/runtime-manifest.json','vendor/portable/renderer-manifest.json'}&indexed.keys()
    paths={name for name in indexed if name.startswith('vendor/portable/')} - manifests
    if not paths and not manifests:return
    if 'vendor/portable/runtime-manifest.json' not in manifests:raise ReleaseError('Portable files need their committed runtime manifest')
    declared={};assemblies=[]
    def walk(node):
        if isinstance(node,list):
            for item in node:walk(item)
        elif isinstance(node,dict):
            name=node.get('path')
            if isinstance(name,str) and name.startswith('vendor/portable/'):
                if (not allowed_path(name) or not re.fullmatch(r'[a-f0-9]{64}',str(node.get('sha256','')))
                        or type(node.get('bytes')) is not int or not 0<=node['bytes']<=MAX_FILE_BYTES):
                    raise ReleaseError('Invalid portable file binding')
                value=(node['sha256'],node['bytes'])
                if name in declared and declared[name]!=value:raise ReleaseError('Conflicting portable file bindings')
                declared[name]=value
            if 'parts' in node:
                parts=node['parts']
                if (not isinstance(parts,list) or not 1<=len(parts)<=64 or not isinstance(name,str)
                        or PurePosixPath(name).name!=name or '\\' in name or name in ('.','..')
                        or not re.fullmatch(r'[a-f0-9]{64}',str(node.get('sha256','')))
                        or type(node.get('bytes')) is not int or not 0<node['bytes']<=MAX_BYTES):
                    raise ReleaseError('Invalid portable archive parts')
                assemblies.append(node)
            for value in node.values():walk(value)
    for name in manifests:
        doc=json.loads(_read_zip_small(archive,prefix+name))
        if doc.get('schema_version')!=1 or set(doc.get('platforms',{}))!={'darwin-arm64','darwin-x64','win32-x64'}:
            raise ReleaseError('Portable manifest needs all three supported platforms')
        walk(doc)
    if set(declared)!=paths:raise ReleaseError('Portable file set differs from committed component manifests')
    for name,(sha,size) in declared.items():
        row=indexed[name]
        if (row['sha256'],row['bytes'])!=(sha,size):raise ReleaseError('Portable component hash/size mismatch: '+name)
    for assembly in assemblies:
        sha=hashlib.sha256();size=0;seen=set()
        for part in assembly['parts']:
            name=part.get('path') if isinstance(part,dict) else None
            if name not in declared or name in seen:raise ReleaseError('Missing or duplicate portable part')
            seen.add(name)
            with archive.open(prefix+name) as stream:
                for block in iter(lambda:stream.read(CHUNK_BYTES),b''):sha.update(block);size+=len(block)
        if (sha.hexdigest(),size)!=(assembly['sha256'],assembly['bytes']):raise ReleaseError('Reassembled portable archive hash mismatch')


def verify_bundle_file(source) -> dict:
    """Verify a seekable ZIP/path with bounded content reads, including parts."""
    try:
        if hasattr(source,'seek'):
            position=source.tell();source.seek(0,os.SEEK_END);physical_size=source.tell();source.seek(position)
        else:physical_size=Path(source).stat().st_size
        if physical_size>MAX_BYTES+64*1024*1024:raise ReleaseError('ZIP physical size exceeds its bound')
        with zipfile.ZipFile(source) as archive:
            infos=archive.infolist();names=[info.filename for info in infos]
            if (len(infos)>MAX_FILES+1 or len(set(names))!=len(names)
                    or len({unicodedata.normalize('NFC',n).casefold() for n in names})!=len(names)
                    or sum(info.file_size for info in infos)>MAX_BYTES+MAX_MANIFEST_BYTES):
                raise ReleaseError('ZIP file set or size is invalid')
            manifests=[n for n in names if n.endswith('/release-manifest.json')]
            if len(manifests)!=1:raise ReleaseError('Release manifest is missing or ambiguous')
            manifest=json.loads(_read_zip_small(archive,manifests[0]));commit=manifest.get('source_commit')
            if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}(?:[a-f0-9]{24})?',commit):raise ReleaseError('Invalid source commit')
            prefix='Intent-Slide-'+commit[:12]+'/';entries=manifest['files'];indexed={row['path']:row for row in entries}
            if (manifest.get('schema_version')!='intent-slide-release.v1' or type(manifest.get('file_count')) is not int
                    or manifest['file_count']!=len(entries) or len(indexed)!=len(entries)
                    or set(names)!={prefix+name for name in [*indexed,'release-manifest.json']}):
                raise ReleaseError('Release manifest differs from ZIP file set')
            _validate_index(indexed)
            for info in infos:
                path=info.filename[len(prefix):];mode=info.external_attr>>16
                expected=0o644 if path=='release-manifest.json' else indexed[path]['mode']
                if (not stat.S_ISREG(mode) or stat.S_IMODE(mode)!=expected or info.is_dir()
                        or info.flag_bits&1 or info.compress_type!=zipfile.ZIP_STORED):
                    raise ReleaseError('Invalid ZIP member mode/type/compression')
                if path=='release-manifest.json':continue
                row=indexed[path]
                if info.file_size!=row['bytes']:raise ReleaseError('ZIP size differs from manifest: '+path)
                sha=hashlib.sha256();count=0
                with archive.open(info) as stream:
                    for block in iter(lambda:stream.read(CHUNK_BYTES),b''):sha.update(block);count+=len(block)
                if count!=row['bytes'] or sha.hexdigest()!=row['sha256']:raise ReleaseError('ZIP bytes differ from manifest: '+path)
            if 'vendor/slidemaster-manifest.json' in indexed:
                baseline=_read_zip_small(archive,prefix+'vendor/slidemaster-manifest.json')
                expected=_release_manifest(commit,entries,baseline)['upstream']
                if manifest.get('upstream')!=expected:raise ReleaseError('Upstream provenance audit differs from bundled baseline')
            _portable_bindings(archive,prefix,indexed)
            return manifest
    except (ValueError,KeyError,TypeError,AttributeError,UnicodeError,zipfile.BadZipFile,RuntimeError,EOFError) as exc:
        if isinstance(exc,ReleaseError):raise
        raise ReleaseError('유효한 Intent-Slide 배포 ZIP이 아닙니다.') from exc


def _file_digest(path: Path) -> tuple[str,int]:
    sha=hashlib.sha256();size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(CHUNK_BYTES),b''):sha.update(block);size+=len(block)
    return sha.hexdigest(),size


def publish_file_exclusive(target: Path,source: Path, *, verified_digest=None) -> str:
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):raise ReleaseError('배포 대상 경로는 symlink일 수 없습니다.')
    expected=verified_digest if verified_digest is not None else _file_digest(source)
    if target.exists():
        if target.is_file() and _file_digest(target)==expected:return 'UNCHANGED'
        raise ReleaseError('다른 내용의 기존 배포를 덮어쓰지 않습니다.')
    target.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.intent-slide-publish-',dir=target.parent)
    temporary=Path(name)
    try:
        sha=hashlib.sha256();size=0
        with os.fdopen(descriptor,'wb') as output,source.open('rb') as content:
            for block in iter(lambda:content.read(CHUNK_BYTES),b''):
                output.write(block);sha.update(block);size+=len(block)
            output.flush();os.fsync(output.fileno())
        if (sha.hexdigest(),size)!=expected:raise ReleaseError('Source release changed during publication')
        try:
            os.link(temporary,target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or _file_digest(target)!=expected:
                raise ReleaseError('발행 중 다른 배포가 생겼습니다. 덮어쓰지 않습니다.')
            return 'UNCHANGED'
        return 'CREATED'
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="커밋에서만 Windows/Mac 로컬 배포 ZIP 생성")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path, help="기존 ZIP 검증만 수행")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify_bundle_file(args.verify)
            print(json.dumps({"status": "VERIFIED", "source_commit": manifest["source_commit"], "file_count": manifest["file_count"]}))
            return 0
        commit, files = read_commit_index(ROOT, args.ref)
        target = args.output or Path(".runtime/dist") / ("Intent-Slide-" + commit[:12] + ".zip")
        if not target.is_absolute():
            target = ROOT / target
        if not target.resolve().is_relative_to(ROOT):
            raise ReleaseError("배포 출력은 이 저장소 안에 지정하세요.")
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise ReleaseError('배포 대상 경로는 symlink일 수 없습니다.')
        target.parent.mkdir(parents=True,exist_ok=True)
        descriptor,name=tempfile.mkstemp(prefix='.intent-slide-release-',dir=target.parent)
        temporary=Path(name)
        try:
            with os.fdopen(descriptor,'w+b') as output,GitBlobReader(ROOT) as reader:
                write_bundle_stream(commit,files,output,reader.chunks)
                output.flush();os.fsync(output.fileno())
            verified_digest=_file_digest(temporary)
            manifest=verify_bundle_file(temporary)
            digest=verified_digest[0]
            status=publish_file_exclusive(target,temporary,verified_digest=verified_digest)
        finally:
            temporary.unlink(missing_ok=True)
        print(json.dumps({"status": status, "source_commit": commit, "file_count": manifest["file_count"],
                          "path": str(target.relative_to(ROOT)), "sha256": digest}, ensure_ascii=False, indent=2))
        return 0
    except (ReleaseError, OSError, subprocess.SubprocessError) as exc:
        print("배포 생성 실패: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
