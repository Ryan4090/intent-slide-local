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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 50_000
ALLOW = (
    "intent-slide", ".gitignore", "README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "requirements-local.txt", "requirements-local.lock", "requirements-upstream.txt",
    "scripts/local_mvp.py", "scripts/build_local_release.py", "tests/test_local_mvp.py", "tests/test_local_release.py",
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
    "intent-slide", "scripts/local_mvp.py", "requirements-local.lock", "THIRD_PARTY_NOTICES.md",
    "licenses/slidemaster-MIT.txt", "vendor/slidemaster-manifest.json", "vendor/runtime-dependencies.json",
    "projects/presentation-agent-suite/scripts/presentation_console.py",
    "projects/presentation-agent-suite/src/presentation_agents/v2/provider_registry.py",
    "projects/presentation-agent-suite/src/presentation_agents/v2/claude_provider.py",
    ".claude/skills/ppt-master/SKILL.md", ".claude/skills/ppt-master/scripts/verify_deck.py",
    ".claude/skills/ppt-master/assets/fonts/Pretendard/LICENSE.txt",
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
            or any(ord(c) < 32 for c in name) or "\\" in name):
        return False
    if any(part in {".git", ".venv", ".runtime", "node_modules", "__pycache__"}
           or part.startswith(".env") or part in {"auth.json", "credentials.json"} for part in path.parts):
        return False
    if exclusion_reason(name):
        return False
    return any(name == prefix or name.startswith(prefix + "/") for prefix in ALLOW)


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
    if not files or len(files) > MAX_FILES or sum(len(data) for data, _ in files.values()) > MAX_BYTES:
        raise ReleaseError("배포 파일 개수 또는 크기가 상한을 넘었습니다.")
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
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if (len(infos) > MAX_FILES + 1 or len({i.filename for i in infos}) != len(infos)
                    or sum(i.file_size for i in infos) > MAX_BYTES + 16 * 1024 * 1024):
                raise ReleaseError("ZIP file set or size is invalid")
            manifests = [i for i in infos if i.filename.endswith("/release-manifest.json")]
            if len(manifests) != 1 or manifests[0].file_size > 16 * 1024 * 1024:
                raise ReleaseError("배포 manifest가 없거나 모호합니다.")
            manifest = json.loads(archive.read(manifests[0]))
            if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', str(manifest.get('source_commit', ''))):
                raise ReleaseError('배포 manifest의 source commit이 올바르지 않습니다.')
            prefix = "Intent-Slide-" + manifest["source_commit"][:12] + "/"
            entries = manifest["files"]
            names = [entry["path"] for entry in entries]
            if (manifest.get("schema_version") != "intent-slide-release.v1" or len(names) != manifest["file_count"]
                    or len(names) != len(set(names))
                    or set(archive.namelist()) != {prefix + name for name in names + ["release-manifest.json"]}):
                raise ReleaseError("배포 manifest와 파일 집합이 다릅니다.")
            for entry in entries:
                if not allowed_path(entry["path"]) or entry.get('mode') not in {0o644, 0o755}:
                    raise ReleaseError("배포에 허용되지 않은 경로가 있습니다.")
                info = archive.getinfo(prefix + entry["path"])
                mode = info.external_attr >> 16
                content = archive.read(info)
                if (not stat.S_ISREG(mode) or stat.S_IMODE(mode) != entry["mode"]
                        or len(content) != entry["bytes"] or _sha(content) != entry["sha256"]):
                    raise ReleaseError("배포 파일 바이트 또는 mode가 변경됐습니다: " + entry["path"])
            if 'vendor/slidemaster-manifest.json' in names:
                upstream = json.loads(archive.read(prefix + 'vendor/slidemaster-manifest.json'))
                expected = [{"path": entry["path"], "sha256": entry["sha256"],
                             "reason": exclusion_reason(entry["path"])} for entry in upstream["files"]
                            if exclusion_reason(entry["path"]) and entry["path"] not in names]
                audit = manifest.get('upstream', {})
                if audit.get('source_commit') != upstream['source_commit'] or audit.get('excluded_files') != expected:
                    raise ReleaseError('제외 감사 기록이 포함된 upstream 출처와 다릅니다.')
            return manifest
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError("유효한 Intent-Slide 배포 ZIP이 아닙니다.") from exc


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="커밋에서만 macOS 로컬 배포 ZIP 생성")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path, help="기존 ZIP 검증만 수행")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify_bundle(args.verify.read_bytes())
            print(json.dumps({"status": "VERIFIED", "source_commit": manifest["source_commit"], "file_count": manifest["file_count"]}))
            return 0
        commit, files = read_commit_files(ROOT, args.ref)
        data = bundle_bytes(commit, files)
        manifest = verify_bundle(data)
        target = args.output or Path(".runtime/dist") / ("Intent-Slide-" + commit[:12] + ".zip")
        if not target.is_absolute():
            target = ROOT / target
        if not target.resolve().is_relative_to(ROOT):
            raise ReleaseError("배포 출력은 이 저장소 안에 지정하세요.")
        status = publish_exclusive(target, data)
        print(json.dumps({"status": status, "source_commit": commit, "file_count": manifest["file_count"],
                          "path": str(target.relative_to(ROOT)), "sha256": _sha(data)}, ensure_ascii=False, indent=2))
        return 0
    except (ReleaseError, OSError, subprocess.SubprocessError) as exc:
        print("배포 생성 실패: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
