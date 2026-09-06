"""Deterministic local bundle checks; no commits, remote calls or credentials."""
import hashlib
import importlib.util
import io
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_local_release", ROOT / "scripts/build_local_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class LocalReleaseTests(unittest.TestCase):
    def test_restricted_brand_assets_are_not_public_payload(self):
        for name in (
            '.claude/skills/ppt-master/templates/brands/naver/images/naver-icon.svg',
            '.claude/skills/ppt-master/templates/decks/naver_ir/templates/01_cover.svg',
            '.claude/skills/ppt-master/templates/icons/simple-icons/naver.svg',
            '.claude/skills/ppt-master/templates/brands/jangpm/images/jangpm-character.png',
            '.claude/skills/ppt-master/templates/decks/jangpm/images/jangpm-character.png',
        ):
            with self.subTest(path=name), self.assertRaises(release.ReleaseError):
                release.bundle_bytes('a' * 40, {name: (b'not licensed for this distribution', 0o644)})
        self.assertTrue(release.allowed_path('.claude/skills/ppt-master/templates/decks/jangpm/templates/01_cover.svg'))

    def test_commit_archive_skips_only_explicit_brand_exclusions(self):
        commit = 'a' * 40
        excluded = '.claude/skills/ppt-master/templates/brands/naver/images/naver-icon.svg'
        tree = b''.join(b'100644 blob ' + b'b' * 40 + b'\t' + name.encode() + b'\0'
                        for name in ('README.md', excluded))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:') as archive:
            for name in ('README.md', excluded):
                entry = tarfile.TarInfo(name)
                entry.size = 4
                archive.addfile(entry, io.BytesIO(b'data'))
        resolved = subprocess.CompletedProcess([], 0, stdout=commit + '\n', stderr='')
        with patch.object(release, 'REQUIRED', ('README.md',)), \
                patch.object(release.subprocess, 'run', return_value=resolved), \
                patch.object(release.subprocess, 'check_output', side_effect=[tree, buffer.getvalue()]):
            _, files = release.read_commit_files(ROOT, commit)
        self.assertEqual(set(files), {'README.md'})

    def test_exclusion_audit_cannot_disagree_with_bundled_provenance(self):
        import json
        source = {'source_commit': 'b' * 40, 'files': [{
            'path': '.claude/skills/ppt-master/templates/icons/simple-icons/naver.svg', 'sha256': 'c' * 64}]}
        data = release.bundle_bytes('a' * 40, {'vendor/slidemaster-manifest.json': (json.dumps(source).encode(), 0o644)})
        self.assertEqual(len(release.verify_bundle(data)['upstream']['excluded_files']), 1)
        for change in ('remove', 'hash'):
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(output, 'w') as new:
                for info in old.infolist():
                    content = old.read(info)
                    if info.filename.endswith('/release-manifest.json'):
                        manifest = json.loads(content)
                        if change == 'remove':
                            manifest['upstream']['excluded_files'] = []
                        else:
                            manifest['upstream']['excluded_files'][0]['sha256'] = 'd' * 64
                        content = json.dumps(manifest).encode()
                    new.writestr(info, content)
            with self.subTest(change=change), self.assertRaises(release.ReleaseError):
                release.verify_bundle(output.getvalue())

    def test_untrusted_manifest_cannot_weaken_commit_or_mode_checks(self):
        import json
        data = release.bundle_bytes('a' * 40, {'README.md': (b'fixture', 0o644)})
        for change in ('commit', 'mode'):
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(output, 'w') as new:
                for info in old.infolist():
                    content = old.read(info)
                    if info.filename.endswith('/release-manifest.json'):
                        manifest = json.loads(content)
                        if change == 'commit':
                            manifest['source_commit'] = '../outside'
                        else:
                            manifest['files'][0]['mode'] = 0o777
                        content = json.dumps(manifest).encode()
                    if change == 'commit':
                        info.filename = info.filename.replace('Intent-Slide-' + 'a' * 12 + '/', 'Intent-Slide-../outside/')
                    elif info.filename.endswith('/README.md'):
                        info.external_attr = (stat.S_IFREG | 0o777) << 16
                    new.writestr(info, content)
            with self.subTest(change=change), self.assertRaises(release.ReleaseError):
                release.verify_bundle(output.getvalue())

    def test_tar_permissions_never_override_the_committed_git_modes(self):
        commit = "a" * 40
        tree = (b"100644 blob " + b"b" * 40 + b"\tREADME.md\0"
                b"100755 blob " + b"c" * 40 + b"\tintent-slide\0")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive:
            for name, mode in (("README.md", 0o664), ("intent-slide", 0o775)):
                entry = tarfile.TarInfo(name)
                entry.mode = mode
                entry.size = len(b"committed bytes\n")
                archive.addfile(entry, io.BytesIO(b"committed bytes\n"))
        resolved = subprocess.CompletedProcess([], 0, stdout=commit + "\n", stderr="")
        with patch.object(release, "REQUIRED", ("README.md", "intent-slide")), \
                patch.object(release.subprocess, "run", return_value=resolved), \
                patch.object(release.subprocess, "check_output", side_effect=[tree, buffer.getvalue()]):
            actual, files = release.read_commit_files(ROOT, commit)
        self.assertEqual({name: mode for name, (_, mode) in files.items()}, {"README.md": 0o644, "intent-slide": 0o755})
        manifest = release.verify_bundle(release.bundle_bytes(actual, files))
        self.assertEqual({item["mode"] for item in manifest["files"]}, {0o644, 0o755})

    def test_existing_product_git_commit_exports_to_zip_with_exact_tree_modes(self):
        # Read the actual checkout's committed tree. No index, commit or config
        # writes; source ZIP users without this product Git checkout skip it.
        if not shutil.which("git"):
            self.skipTest("Git is unavailable; real committed-tree integration requires a checkout")
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        if top.returncode or Path(top.stdout.strip()).resolve() != ROOT.resolve():
            self.skipTest("No product Git checkout; fixture archive-mode coverage still runs")
        commit, files = release.read_commit_files(ROOT, "HEAD")
        raw_tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", commit], cwd=ROOT, timeout=30)
        expected = {}
        for item in raw_tree.split(b"\0"):
            if not item:
                continue
            metadata, raw_name = item.split(b"\t", 1)
            name = raw_name.decode("utf-8")
            if name in files:
                expected[name] = stat.S_IMODE(int(metadata.split()[0], 8))
        mismatches = [name for name, (_, mode) in files.items() if mode != expected[name]]
        self.assertEqual(len(mismatches), 0, f"Git/archive mode mismatches; first: {mismatches[:3]}")
        data = release.bundle_bytes(commit, files)
        manifest = release.verify_bundle(data)
        self.assertEqual({item["path"]: item["mode"] for item in manifest["files"]}, expected)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            prefix = "Intent-Slide-" + commit[:12] + "/"
            self.assertTrue(all(stat.S_IMODE(archive.getinfo(prefix + name).external_attr >> 16) == mode
                                for name, mode in expected.items()))

    def test_zip_permission_guard_still_rejects_noncanonical_modes(self):
        for mode in (0o664, 0o775, 0o777, 0o4755, 0o600):
            with self.subTest(mode=oct(mode)), self.assertRaises(release.ReleaseError):
                release.bundle_bytes("a" * 40, {"README.md": (b"fixture", mode)})

    def test_same_commit_and_files_produce_exact_same_zip_bytes(self):
        files = {"intent-slide": (b"#!/bin/sh\n", 0o755), "licenses/upstream.txt": (b"license\n", 0o644)}
        first = release.bundle_bytes("a" * 40, files)
        second = release.bundle_bytes("a" * 40, dict(reversed(list(files.items()))))
        self.assertEqual(first, second)
        manifest = release.verify_bundle(first)
        self.assertEqual(manifest["source_commit"], "a" * 40)
        self.assertEqual(manifest["file_count"], 2)

    def test_private_roots_and_untrusted_archive_paths_are_rejected(self):
        for name in ("../outside", "/absolute", ".env", ".runtime/control.sqlite", "data/private.json", "reports/personal.md",
                     ".git/objects/private", "src/mvp/app.js", "web/index.html", "output/result.pptx", "artifacts/private.pdf",
                     "docs/program/private.md", "vendor/.env.local", "projects/presentation-agent-suite/.runtime/live/state.sqlite"):
            with self.subTest(path=name), self.assertRaises(release.ReleaseError):
                release.bundle_bytes("a" * 40, {name: (b"fixture", 0o644)})

    def test_public_bundle_preserves_ignore_rules_without_private_git_history(self):
        data = release.bundle_bytes("a" * 40, {".gitignore": (b".venv/\n.runtime/\n", 0o644)})
        self.assertEqual(release.verify_bundle(data)["files"][0]["path"], ".gitignore")

    def test_changed_file_cannot_pass_its_original_manifest(self):
        data = release.bundle_bytes("a" * 40, {"intent-slide": (b"original", 0o755)})
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(output, "w") as new:
            for info in old.infolist():
                new.writestr(info, b"changed" if info.filename.endswith("/intent-slide") else old.read(info))
        with self.assertRaises(release.ReleaseError):
            release.verify_bundle(output.getvalue())

    def test_publish_is_idempotent_and_never_overwrites_different_bytes(self):
        runtime = ROOT / ".runtime"
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime, prefix="release-test-") as temporary:
            target = Path(temporary) / "release.zip"
            self.assertEqual(release.publish_exclusive(target, b"first"), "CREATED")
            self.assertEqual(release.publish_exclusive(target, b"first"), "UNCHANGED")
            with self.assertRaises(release.ReleaseError):
                release.publish_exclusive(target, b"different")
            self.assertEqual(target.read_bytes(), b"first")
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_baseline_changes_are_downstream_provenance_not_a_validation_failure(self):
        upstream = {"source_commit": "b" * 40, "files": [{"path": "intent-slide", "sha256": "0" * 64}]}
        import json
        data = release.bundle_bytes("a" * 40, {"intent-slide": (b"changed product", 0o755),
                                             "vendor/slidemaster-manifest.json": (json.dumps(upstream).encode(), 0o644)})
        manifest = release.verify_bundle(data)
        self.assertEqual(manifest["upstream"]["source_commit"], "b" * 40)
        self.assertEqual(manifest["upstream"]["modified_files"], ["intent-slide"])


if __name__ == "__main__":
    unittest.main()
