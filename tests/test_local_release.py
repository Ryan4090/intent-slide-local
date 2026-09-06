"""Deterministic local bundle checks; no commits, remote calls or credentials."""
import hashlib
import importlib.util
import io
import json
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_local_release", ROOT / "scripts/build_local_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class LocalReleaseTests(unittest.TestCase):
    def test_git_attributes_is_an_explicit_required_release_input(self):
        self.assertIn('.gitattributes', release.ALLOW)
        self.assertIn('.gitattributes', release.REQUIRED)
        self.assertTrue(release.allowed_path('.gitattributes'))

    def test_supplemental_metadata_requires_exact_manifest_binding(self):
        path='vendor/portable/renderer/packaging-tests.log'
        data=b'public build verification\n'
        record={'path':path,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        platforms={key:{} for key in ('darwin-arm64','darwin-x64','win32-x64')}
        for change in ('unbound','bound','changed'):
            doc={'schema_version':1,'platforms':platforms}
            if change!='unbound':doc['supplemental_files']=[record]
            files={path:(b'changed metadata\n' if change=='changed' else data,0o644),
                   'vendor/portable/runtime-manifest.json':(json.dumps({'schema_version':1,'platforms':platforms}).encode(),0o644),
                   'vendor/portable/renderer-manifest.json':(json.dumps(doc).encode(),0o644)}
            index={n:{'bytes':len(d),'mode':m} for n,(d,m) in files.items()}
            out=io.BytesIO();release.write_bundle_stream('a'*40,index,out,lambda n,_:iter([files[n][0]]));out.seek(0)
            if change=='bound':self.assertEqual(release.verify_bundle_file(out)['file_count'],3)
            else:
                with self.subTest(change=change),self.assertRaises(release.ReleaseError):release.verify_bundle_file(out)

    def test_stream_writer_is_deterministic_and_never_needs_whole_blob(self):
        payload={'README.md':b'hello', 'licenses/fixture.txt':b'x'*(3*release.CHUNK_BYTES+17)}
        index={name:{'bytes':len(data),'mode':0o644,'oid':'b'*40} for name,data in payload.items()}
        calls=[]
        def chunks(name, _):
            calls.append(name)
            for start in range(0,len(payload[name]),65536):yield payload[name][start:start+65536]
        first,second=io.BytesIO(),io.BytesIO()
        release.write_bundle_stream('a'*40,index,first,chunks)
        release.write_bundle_stream('a'*40,dict(reversed(list(index.items()))),second,chunks)
        self.assertEqual(first.getvalue(),second.getvalue())
        first.seek(0)
        with patch.object(zipfile.ZipFile,'read',side_effect=AssertionError('full ZIP entry read forbidden')):
            result=release.verify_bundle_file(first)
        self.assertEqual(result['file_count'],2)
        self.assertEqual(len(calls),4)

    def test_stream_bounds_collisions_and_partial_blob_fail_closed(self):
        for index in ({'README.md':{'bytes':release.MAX_FILE_BYTES+1,'mode':0o644}},
                      {'licenses/A.txt':{'bytes':1,'mode':0o644},'licenses/a.txt':{'bytes':1,'mode':0o644}},
                      {'vendor/.RUNTIME/private':{'bytes':1,'mode':0o644}}):
            with self.subTest(index=index),self.assertRaises(release.ReleaseError):
                release.write_bundle_stream('a'*40,index,io.BytesIO(),lambda *_:iter([b'x']))
        with self.assertRaises(release.ReleaseError):
            release.write_bundle_stream('a'*40,{'README.md':{'bytes':2,'mode':0o644}},io.BytesIO(),lambda *_:iter([b'x']))

    def test_150_mib_bundle_streams_under_16_mib_python_heap(self):
        # Cross the old 128 MiB total limit with no oversized individual file.
        size=75*1024*1024;block=b'x'*release.CHUNK_BYTES
        index={name:{'bytes':size,'mode':0o644} for name in ('licenses/large-a.txt','licenses/large-b.txt')}
        def chunks(*_):
            for _ in range(size//len(block)):yield block
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            path=Path(folder)/'large.zip';tracemalloc.start()
            try:
                with path.open('w+b') as output:release.write_bundle_stream('a'*40,index,output,chunks)
                result=release.verify_bundle_file(path)
                peak=tracemalloc.get_traced_memory()[1]
            finally:tracemalloc.stop()
        self.assertEqual(result['file_count'],2)
        self.assertLess(peak,16*1024*1024)

    def test_committed_tree_requires_new_launchers_and_has_no_archive_fallback(self):
        commit='a'*40;name='README.md'
        tree=b'100644 blob '+b'b'*40+b' 4\tREADME.md\0'
        resolved=subprocess.CompletedProcess([],0,stdout=commit+'\n',stderr='')
        with patch.object(release.subprocess,'run',return_value=resolved),patch.object(release.subprocess,'check_output',return_value=tree) as call:
            with self.assertRaises(release.ReleaseError):release.read_commit_index(ROOT,commit)
            actual,index=release.read_commit_index(ROOT,commit,required=(name,))
        self.assertEqual(actual,commit);self.assertEqual(index[name]['bytes'],4)
        self.assertTrue(all('archive' not in args.args[0] for args in call.call_args_list))

    def test_git_batch_nonzero_exit_is_a_build_failure(self):
        for failure in ('exit','timeout'):
            with self.subTest(failure=failure),patch.object(release.subprocess,'Popen') as popen,patch.object(release.threading,'Timer'):
                if failure=='exit':popen.return_value.wait.return_value=1
                else:popen.return_value.wait.side_effect=[subprocess.TimeoutExpired('git',5),0]
                with self.assertRaises(release.ReleaseError):
                    with release.GitBlobReader(ROOT):pass
                popen.return_value.stdout.close.assert_called_once()

    def test_portable_archive_order_is_bound_to_original_hash(self):
        values=[b'one',b'two'];parts=[{'path':f'vendor/portable/codex/demo.part{i}',
                    'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for i,v in enumerate(values)]
        archive={'path':'demo.tgz','bytes':6,'sha256':hashlib.sha256(b'onetwo').hexdigest(),'parts':parts}
        doc={'schema_version':1,'platforms':{key:{'codex':archive} for key in ('darwin-arm64','darwin-x64','win32-x64')}}
        files={p['path']:(v,0o644) for p,v in zip(parts,values)}
        for reverse in (False,True):
            if reverse:archive['parts']=list(reversed(parts))
            files['vendor/portable/runtime-manifest.json']=(json.dumps(doc).encode(),0o644)
            index={n:{'bytes':len(d),'mode':m} for n,(d,m) in files.items()}
            out=io.BytesIO();release.write_bundle_stream('a'*40,index,out,lambda n,_:iter([files[n][0]]));out.seek(0)
            if reverse:
                with self.assertRaises(release.ReleaseError):release.verify_bundle_file(out)
            else:self.assertEqual(release.verify_bundle_file(out)['file_count'],3)

    def test_portable_payload_requires_manifest_hash_and_complete_file_set(self):
        record={'path':'vendor/portable/python/test.tar.gz','bytes':4,'sha256':hashlib.sha256(b'data').hexdigest()}
        portable={'schema_version':1,'platforms':{key:{'python':record,'codex':record,'wheels':[record]}
                    for key in ('darwin-arm64','darwin-x64','win32-x64')}}
        files={record['path']:(b'data',0o644),'vendor/portable/runtime-manifest.json':(json.dumps(portable).encode(),0o644)}
        for change in ('valid','hash','extra','missing'):
            changed=dict(files)
            if change=='hash':changed[record['path']]=(b'evil',0o644)
            if change=='extra':changed['vendor/portable/unknown.bin']=(b'unknown',0o644)
            if change=='missing':del changed[record['path']]
            index={n:{'bytes':len(d),'mode':m} for n,(d,m) in changed.items()}
            out=io.BytesIO();release.write_bundle_stream('a'*40,index,out,lambda n,_:iter([changed[n][0]]));out.seek(0)
            if change=='valid':self.assertEqual(release.verify_bundle_file(out)['file_count'],2)
            else:
                with self.subTest(change=change),self.assertRaises(release.ReleaseError):release.verify_bundle_file(out)

    def test_file_publish_streams_and_never_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as name:
            source,target=Path(name).resolve()/'source.zip',Path(name).resolve()/'target.zip';source.write_bytes(b'payload')
            with patch.object(Path,'read_bytes',side_effect=AssertionError('whole file read forbidden')):
                self.assertEqual(release.publish_file_exclusive(target,source),'CREATED')
                self.assertEqual(release.publish_file_exclusive(target,source),'UNCHANGED')
            source.write_bytes(b'changed')
            with self.assertRaises(release.ReleaseError):release.publish_file_exclusive(target,source)
            self.assertEqual(target.read_bytes(),b'payload')

    def test_main_streams_and_never_publishes_a_post_verification_swap(self):
        index={'README.md':{'bytes':4,'mode':0o644,'oid':'b'*40}}
        real_verify=release.verify_bundle_file
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            for tamper in (False,True):
                target=root/('tampered.zip' if tamper else 'valid.zip')
                def verify(path):
                    result=real_verify(path)
                    if tamper:path.write_bytes(b'swapped after validation')
                    return result
                with patch.object(release,'ROOT',root),patch.object(release,'read_commit_index',return_value=('a'*40,index)), \
                     patch.object(release,'GitBlobReader') as reader,patch.object(release,'verify_bundle_file',side_effect=verify), \
                     patch.object(release,'read_commit_files',side_effect=AssertionError('no legacy whole archive')), \
                     patch.object(release,'bundle_bytes',side_effect=AssertionError('no whole ZIP buffer')), \
                     patch('sys.stdout',io.StringIO()),patch('sys.stderr',io.StringIO()):
                    reader.return_value.__enter__.return_value.chunks=lambda *_:iter([b'data'])
                    code=release.main(['--output',str(target)])
                self.assertEqual(code,2 if tamper else 0)
                self.assertEqual(target.exists(),not tamper)
            self.assertEqual(sorted(p.name for p in root.iterdir()),['valid.zip'])

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
        # This old HEAD may predate the portable launchers. This slice checks
        # real Git object/mode fidelity; main's full required-file gate is
        # exercised separately with complete/missing portable fixtures.
        commit, files = release.read_commit_index(ROOT, "HEAD", required=('README.md','intent-slide'))
        raw_tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", commit], cwd=ROOT, timeout=30)
        expected = {}
        for item in raw_tree.split(b"\0"):
            if not item:
                continue
            metadata, raw_name = item.split(b"\t", 1)
            name = raw_name.decode("utf-8")
            if name in files:
                expected[name] = stat.S_IMODE(int(metadata.split()[0], 8))
        mismatches = [name for name, row in files.items() if row['mode'] != expected[name]]
        self.assertEqual(len(mismatches), 0, f"Git/archive mode mismatches; first: {mismatches[:3]}")
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime') as folder:
            path=Path(folder)/'committed.zip'
            with path.open('w+b') as output,release.GitBlobReader(ROOT) as reader:
                release.write_bundle_stream(commit,files,output,reader.chunks)
            manifest=release.verify_bundle_file(path)
            self.assertEqual({item['path']:item['mode'] for item in manifest['files']},expected)
            with zipfile.ZipFile(path) as archive:
                prefix='Intent-Slide-'+commit[:12]+'/'
                self.assertTrue(all(stat.S_IMODE(archive.getinfo(prefix+name).external_attr>>16)==mode for name,mode in expected.items()))

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
