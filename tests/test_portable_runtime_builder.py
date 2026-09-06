"""Offline maintainer-builder boundaries; never download or execute a component."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_builder', ROOT / 'scripts/build_portable_runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PortableBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for name, value in [('ROOT', self.root), ('DEST', self.root/'vendor/portable'), ('CACHE', self.root/'.runtime/build')]:
            p = patch.object(builder, name, value); p.start(); self.addCleanup(p.stop)

    def archive(self, entries):
        path = self.root/'fixture.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, value, link in entries:
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE; member.linkname = value
                    archive.addfile(member)
                else:
                    data = value.encode(); member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        return path

    def test_parts_reassemble_exact_original_without_repacking(self):
        source=self.root/'original'; source.write_bytes(b'abcdefghijk')
        with patch.object(builder, 'PART_BYTES', 4):
            result=builder.publish_archive(source, builder.DEST/'codex', 'test.tgz')
        self.assertEqual(result['path'], 'test.tgz')
        data=b''.join((self.root/p['path']).read_bytes() for p in result['parts'])
        self.assertEqual(data, source.read_bytes())
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual([p['bytes'] for p in result['parts']], [4,4,3])

    def test_modified_existing_part_is_never_silently_replaced(self):
        source=self.root/'original'; source.write_bytes(b'abcdefgh')
        with patch.object(builder, 'PART_BYTES', 4):
            result=builder.publish_archive(source, builder.DEST/'codex', 'test.tgz')
            first=self.root/result['parts'][0]['path']; first.write_bytes(b'evil')
            with self.assertRaises(ValueError):
                builder.publish_archive(source, builder.DEST/'codex', 'test.tgz')
        self.assertEqual(first.read_bytes(), b'evil')

    def test_cached_hash_mismatch_does_not_trigger_unreviewed_download(self):
        path=builder.CACHE/'downloads/file'; path.parent.mkdir(parents=True); path.write_bytes(b'wrong')
        with patch.object(builder.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            with self.assertRaises(ValueError):
                builder.checked_download('https://example.invalid/file', 'file', sha256='0'*64)

    def test_new_mismatched_download_does_not_poison_reusable_cache(self):
        with patch.object(builder.urllib.request,'urlopen',return_value=io.BytesIO(b'wrong')):
            with self.assertRaises(ValueError):
                builder.checked_download('https://example.invalid/file','file',sha256='0'*64)
        self.assertFalse((builder.CACHE/'downloads/file').exists())
        self.assertFalse((builder.CACHE/'downloads/file.partial').exists())

    def test_archive_links_cannot_escape_or_impersonate_entrypoint(self):
        for target in ['../../outside', '/outside', 'C:/outside']:
            with self.subTest(target=target):
                p=self.archive([('python/bin/python',target,True)])
                with self.assertRaises(ValueError):builder.archive_review(p, ['python/bin/python'])
        p=self.archive([('python/bin/python','python-real',True),('python/bin/python-real','payload',False)])
        with self.assertRaises(ValueError):builder.archive_review(p, ['python/bin/python'])

    def test_source_archive_keeps_original_build_and_license_bytes(self):
        p=self.archive([('demo/setup.py','raise RuntimeError("must not execute")',False),('demo/COPYING','original license',False)])
        definition={'demo':{'component':'demo','version':'1','filename':'demo.tar.gz','source_url':'https://example.invalid/demo',
                           'source_metadata_url':'https://example.invalid/meta','sha256':builder.digest(p),'bytes':p.stat().st_size,
                           'build_files':['demo/setup.py'],'license_members':['demo/COPYING']}}
        with patch.object(builder, 'SOURCE_ARCHIVES', definition), patch.object(builder, 'checked_download', return_value=p), \
                patch.object(builder.subprocess, 'run', side_effect=AssertionError('no component execution')):
            rows=builder.collect_sources()
        self.assertEqual(len(rows),1)
        self.assertEqual((self.root/rows[0]['path']).read_bytes(),p.read_bytes())
        self.assertEqual(rows[0]['build_files'],['demo/setup.py'])
        self.assertEqual((self.root/rows[0]['license_files'][0]['path']).read_text(),'original license')

    def test_repeated_source_directories_are_recorded_but_files_rejected(self):
        path=self.root/'directories.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for _ in range(2):
                member=tarfile.TarInfo('source/thirdparty'); member.type=tarfile.DIRTYPE; archive.addfile(member)
        result=builder.archive_review(path,[])
        self.assertEqual(result['repeated_directories'],['source/thirdparty'])
        path=self.archive([('source/COPYING','a',False),('source/COPYING','b',False)])
        with self.assertRaises(ValueError):builder.archive_review(path,[])


if __name__ == '__main__':
    unittest.main()
