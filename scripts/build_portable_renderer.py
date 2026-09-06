#!/usr/bin/env python3
"""Maintainer-only packaging of pinned official LibreOffice binaries.

The product launcher never downloads or runs this builder. All intermediate
files stay in .runtime/portable-renderer-build. No system installation is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.runtime/portable-renderer-build'
DEST = ROOT / 'vendor/portable/renderer'
MANIFEST = ROOT / 'vendor/portable/renderer-manifest.json'
VERSION = '26.8.0'
SOURCE_VERSION = '26.8.0.3'
PART_BYTES = 90 * 1024 * 1024
BASE = f'https://download.documentfoundation.org/libreoffice/stable/{VERSION}/'
SOURCES = {
    'darwin-arm64': {'relative': f'mac/aarch64/LibreOffice_{VERSION}_MacOS_aarch64.dmg',
                     'sha256': '8858d8058da4f862f47559486814e65efc27294da67c5e4bb56b006b1ee59f89', 'bytes': 298773447},
    'darwin-x64': {'relative': f'mac/x86_64/LibreOffice_{VERSION}_MacOS_x86-64.dmg',
                   'sha256': '2dcbce4894e01bc1ecd594658e2cbda70ff7bfcd0b310f35d38887797172d09e', 'bytes': 309257813},
    'win32-x64': {'relative': f'win/x86_64/LibreOffice_{VERSION}_Win_x86-64.msi',
                  'sha256': '4aa6c6e1895f4055104effcb556bd3362d20c6ad707c149543304f395ef9db95', 'bytes': 374906880},
}
WINDOWS_PACKAGING_REVISION = 'app-local-v1'
# Original x64 runtime payload from this exact official MSI administrative image.
# No DLL from the maintainer's Windows/system directories may be substituted.
WINDOWS_RUNTIME_DLLS = {
    'concrt140.dll': '2405355f0a58067b258f8df33c327e3a3d716eaac5a3a5aebb757842d85bd376',
    'msvcp140.dll': '0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9',
    'msvcp140_1.dll': 'bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d',
    'msvcp140_2.dll': '3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9',
    'msvcp140_atomic_wait.dll': '640b2aefced484d0368eea5bdd06addd0658a3a70a49256e560d6923b404a479',
    'msvcp140_codecvt_ids.dll': 'f2069a52880ec885ee7f0511186100eb7fada0411a2b4948fafea7735b878a18',
    'vccorlib140.dll': '19839407c3fdbc824e5bce189bf68ddf8097f12ec28b757797ffa0415c144ddd',
    'vcruntime140.dll': 'd5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066',
    'vcruntime140_1.dll': '1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f',
    'vcruntime140_threads.dll': '219915cf20822f34d5e7c1fdd4e21ae7f3396881096c51036225fb8f84b47afa',
}
SUPPLEMENTAL_FILES = tuple('vendor/portable/renderer/' + name for name in (
    'darwin-arm64/LICENSE', 'darwin-arm64/NOTICE',
    'darwin-x64/LICENSE', 'darwin-x64/NOTICE',
    'packaging-tests.log', 'packaging-verification.json', 'source-access.json',
    'win32-x64/NOTICE', 'win32-x64/ci-provenance.json',
    'win32-x64/dependency-review.json', 'win32-x64/license.txt',
))


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def record(path: Path) -> dict:
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': digest(path), 'bytes': path.stat().st_size}


def refresh_supplemental_files() -> list[dict]:
    """Bind final public metadata bytes without modifying any supplemental file."""
    records = []
    for relative in SUPPLEMENTAL_FILES:
        path = ROOT / relative
        if (path == MANIFEST or not path.is_file() or path.is_symlink()
                or not path.resolve(strict=True).is_relative_to(ROOT)):
            raise ValueError('Missing or linked supplemental file: ' + relative)
        records.append(record(path))
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    manifest['supplemental_files'] = records
    temporary = MANIFEST.with_suffix('.json.partial')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(MANIFEST)
    return records


def inside_cache(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(CACHE.resolve()):
        raise ValueError('Build inputs and outputs must stay inside the renderer build cache')
    return resolved


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing an insecure download redirect')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download_source(platform: str) -> Path:
    details = SOURCES[platform]
    url = BASE + details['relative']
    target = CACHE / 'downloads' / Path(details['relative']).name
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(SecureRedirect())
    with opener.open(url + '.sha256', timeout=30) as response:
        checksum_text = response.read(4096).decode('ascii').strip()
    fields = checksum_text.split()
    if len(fields) != 2 or fields[0] != details['sha256'] or fields[1].lstrip('*') != target.name:
        raise ValueError('Official checksum changed; a new component review is required')
    (target.parent / (target.name + '.sha256')).write_text(checksum_text + '\n', encoding='utf-8')
    if not target.exists():
        partial = target.with_name(target.name + '.partial')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Intent-Slide-renderer-maintainer/1'})
            with opener.open(request, timeout=60) as response, partial.open('wb') as output:
                size, next_report = 0, 64 * 1024 * 1024
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > details['bytes']:
                        raise ValueError('Download exceeds the reviewed artifact size')
                    output.write(block)
                    if size >= next_report:
                        print(json.dumps({'platform': platform, 'downloaded_bytes': size}), flush=True)
                        next_report += 64 * 1024 * 1024
            if partial.stat().st_size != details['bytes'] or digest(partial) != details['sha256']:
                raise ValueError('Official artifact size or SHA-256 mismatch')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    if target.stat().st_size != details['bytes'] or digest(target) != details['sha256']:
        raise ValueError('Cached official artifact differs from its reviewed SHA-256')
    print(json.dumps({'platform': platform, 'source': 'SHA256_VERIFIED', 'bytes': details['bytes']}), flush=True)
    return target


def safe_tree(root: Path) -> dict:
    """Allow regular files, directories and relative links confined to this tree."""
    root = root.resolve(strict=True)
    files, links, licenses = [], [], []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ValueError('Privileged permission bits in renderer tree: ' + relative)
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(root):
                raise ValueError('Archive link leaves its root: ' + relative)
            links.append({'path': relative, 'target': target})
        elif stat.S_ISREG(info.st_mode):
            files.append(relative)
            if any(term in path.name.lower() for term in ('license', 'notice', 'copying', 'copyright')):
                licenses.append({'path': relative, 'sha256': digest(path), 'bytes': info.st_size})
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError('Special file in renderer tree: ' + relative)
    if not licenses:
        raise ValueError('Original distribution license notices are missing')
    return {'file_count': len(files), 'links': links, 'license_files': licenses}


def codesign_review(app: Path, platform: str) -> dict:
    commands = {
        'verify': ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)],
        'display': ['/usr/bin/codesign', '--display', '--verbose=4', str(app)],
    }
    results = {}
    for name, command in commands.items():
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        text = result.stdout + result.stderr
        (CACHE / f'{platform}-codesign-{name}.log').write_text(text, encoding='utf-8')
        results[name] = {'returncode': result.returncode, 'output': text.replace(str(CACHE), '<renderer-build-cache>')}
    if results['verify']['returncode']:
        raise ValueError('The unmodified Mac application failed its code-signature verification')
    return results


def mac_tree(source: Path, platform: str) -> tuple[Path, dict]:
    if sys.platform != 'darwin':
        raise ValueError('Mac DMG extraction requires a Mac maintainer host')
    mount = CACHE / ('mount-' + platform)
    mount.mkdir(parents=True, exist_ok=True)
    if any(mount.iterdir()):
        raise ValueError('Mount directory is not empty; inspect the previous build first')
    destination = CACHE / platform / 'LibreOffice.app'
    if destination.exists():
        return destination, codesign_review(destination, platform)
    mounted = False
    try:
        result = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                                 '-mountpoint', str(mount), '-plist', str(source)],
                                capture_output=True, timeout=120)
        (CACHE / f'{platform}-mount.log').write_bytes(result.stderr)
        if result.returncode:
            raise ValueError('Read-only DMG attachment failed; inspect the private build log')
        mounted = True
        info = plistlib.loads(result.stdout)
        if not any(row.get('mount-point') == str(mount) for row in info.get('system-entities', [])):
            raise ValueError('DMG mounted outside the requested build path')
        original = mount / 'LibreOffice.app'
        safe_tree(original)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original, destination, symlinks=True)
        return destination, codesign_review(destination, platform)
    finally:
        if mounted:
            detached = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], capture_output=True, timeout=90)
            if detached.returncode:
                raise ValueError('DMG remains attached; detach the renderer build mount before continuing')


def windows_app_local_runtime(tree: Path, expected: dict[str, str] | None = None) -> list[dict]:
    """Copy only pinned original x64 DLL bytes next to the dependent executable."""
    expected = WINDOWS_RUNTIME_DLLS if expected is None else expected
    source_directory, destination = tree / 'System64', tree / 'program'
    if any(path.is_symlink() for path in (tree, source_directory, destination)):
        raise ValueError('Runtime directory links are not allowed')
    if not source_directory.is_dir() or not destination.is_dir():
        raise ValueError('Original runtime or executable directory is missing')
    checked = []
    for name, checksum in sorted(expected.items()):
        if Path(name).name != name or not name.endswith('.dll'):
            raise ValueError('Invalid runtime DLL name')
        source, target = source_directory / name, destination / name
        if source.is_symlink() or target.is_symlink():
            raise ValueError('Runtime DLL links are not allowed')
        if not source.is_file() or digest(source) != checksum:
            raise ValueError('Original runtime DLL hash does not match the reviewed MSI')
        data = source.read_bytes()
        if len(data) < 64 or data[:2] != b'MZ':
            raise ValueError('Runtime DLL is not a reviewed x64 PE')
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        if (offset + 6 > len(data) or data[offset:offset+4] != b'PE\0\0'
                or struct.unpack_from('<H', data, offset+4)[0] != 0x8664):
            raise ValueError('Runtime DLL is not a reviewed x64 PE')
        if target.exists() and (not target.is_file() or digest(target) != checksum):
            raise ValueError('Refusing to replace different application-local runtime bytes')
        checked.append((source, target, checksum))
    mappings = []
    for source, target, checksum in checked:
        if not target.exists():
            with source.open('rb') as input_stream, target.open('xb') as output:
                shutil.copyfileobj(input_stream, output)
            shutil.copystat(source, target)
        if digest(target) != checksum:
            raise ValueError('Application-local runtime copy hash mismatch')
        mappings.append({'source': 'LibreOffice/' + source.relative_to(tree).as_posix(),
                         'target': 'LibreOffice/' + target.relative_to(tree).as_posix(),
                         'source_sha256': checksum, 'target_sha256': checksum,
                         'bytes': source.stat().st_size, 'pe_machine': '0x8664'})
    return mappings


def pack_tree(tree: Path, platform: str, cache: Path = CACHE) -> tuple[Path, dict]:
    review = safe_tree(tree)
    entry = 'Contents/MacOS/soffice' if platform.startswith('darwin') else 'program/soffice.exe'
    if not (tree / entry).is_file():
        raise ValueError('The renderer entry point is absent from the extracted tree')
    revision = '-' + WINDOWS_PACKAGING_REVISION if platform == 'win32-x64' else ''
    archive = cache / f'libreoffice-{VERSION}-{platform}{revision}.tar.gz'
    partial = archive.with_suffix(archive.suffix + '.partial')
    root_name = 'LibreOffice.app' if platform.startswith('darwin') else 'LibreOffice'
    def normalize(member):
        member.uid = member.gid = 0
        member.uname = member.gname = ''
        member.mtime = 0
        member.pax_headers = {}
        return member
    try:
        with partial.open('wb') as output, gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT, dereference=False) as archive_stream:
                archive_stream.add(tree, arcname=root_name, filter=normalize)
        if archive.exists() and digest(archive) != digest(partial):
            raise ValueError('Refusing to replace an archive with different bytes')
        if not archive.exists():
            partial.replace(archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive, {**review, 'entry': root_name + '/' + entry}


def publish(archive: Path, platform: str, destination_root: Path = DEST) -> dict:
    destination = destination_root / platform
    destination.mkdir(parents=True, exist_ok=True)
    parts = []
    with archive.open('rb') as stream:
        number = 0
        while block := stream.read(PART_BYTES):
            number += 1
            path = destination / (archive.name + f'.part{number:03d}')
            checksum = hashlib.sha256(block).hexdigest()
            if path.exists() and digest(path) != checksum:
                raise ValueError('Refusing to overwrite a different committed archive part')
            if not path.exists():
                with path.open('xb') as output:
                    output.write(block)
            parts.append(record(path))
    return {'path': archive.name, 'format': 'tar.gz', 'sha256': digest(archive), 'bytes': archive.stat().st_size, 'parts': parts}


def verify_parts(bundle: dict) -> None:
    """Re-read published bytes, independently of the source archive stream."""
    combined, total = hashlib.sha256(), 0
    for part in bundle['parts']:
        relative = PurePosixPath(part['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Archive part path leaves the repository')
        path = ROOT / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(ROOT):
            raise ValueError('Archive part is not a repository-local regular file')
        if path.stat().st_size != part['bytes'] or not 0 < part['bytes'] <= PART_BYTES:
            raise ValueError('Archive part size differs from its manifest')
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(block)
                combined.update(block)
                total += len(block)
        if checksum.hexdigest() != part['sha256']:
            raise ValueError('Archive part hash differs from its manifest')
    if total != bundle['bytes'] or combined.hexdigest() != bundle['sha256']:
        raise ValueError('Reassembled archive differs from its manifest')


def build(platforms: list[str], windows_tree: Path | None, replace_platforms: list[str] | None = None) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'schema_version': 1, 'platforms': {}}
    manifest.update(version=VERSION, source_version=SOURCE_VERSION,
                    source_code_url=f'https://download.documentfoundation.org/libreoffice/src/{VERSION}/libreoffice-{SOURCE_VERSION}.tar.xz',
                    component_review='docs/local-mvp/PORTABLE_RENDERER_REVIEW.md')
    for platform in platforms:
        source = download_source(platform)
        packaging_provenance = None
        if platform.startswith('darwin'):
            tree, signature = mac_tree(source, platform)
        else:
            if windows_tree is None:
                raise ValueError('Windows requires a CI-extracted tree in --windows-tree inside the build cache')
            original_tree = inside_cache(windows_tree)
            safe_tree(original_tree)
            tree = CACHE / ('win32-x64-' + WINDOWS_PACKAGING_REVISION) / 'LibreOffice'
            if not tree.exists():
                tree.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(original_tree, tree, symlinks=True)
            mappings = windows_app_local_runtime(tree)
            packaging_provenance = {'revision': WINDOWS_PACKAGING_REVISION,
                                    'app_local_runtime_copies': mappings,
                                    'original_executable_bytes_modified': False,
                                    'runtime_source': 'Same pinned official LibreOffice MSI; System64 payload retained',
                                    'microsoft_notice': 'LibreOffice/license.txt, Microsoft Visual C++ Runtime Libraries section',
                                    'independent_redistribution_rights': 'UNVERIFIED'}
            signature = {'status': 'UNVERIFIED', 'note': 'This Mac packager does not perform Windows Authenticode validation; see separate Windows CI evidence'}
        archive, review = pack_tree(tree, platform)
        result = {**publish(archive, platform), 'entry': review['entry'], 'version': VERSION,
                  'source_url': BASE + SOURCES[platform]['relative'], 'source_sha256': SOURCES[platform]['sha256'],
                  'source_bytes': SOURCES[platform]['bytes'], 'source_sha256_url': BASE + SOURCES[platform]['relative'] + '.sha256',
                  'license': 'MPL-2.0 and bundled component licenses; original notices retained inside the archive',
                  'license_url': 'https://www.libreoffice.org/licenses/', 'tree_review': review, 'signature_review': signature}
        if packaging_provenance:
            result['packaging_provenance'] = packaging_provenance
        verify_parts(result)
        previous = manifest['platforms'].get(platform)
        if (previous and previous.get('sha256') != result['sha256']
                and platform not in (replace_platforms or [])):
            raise ValueError('Existing platform archive differs; use an explicitly reviewed new version')
        manifest['platforms'][platform] = result
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        temporary = MANIFEST.with_suffix('.json.partial')
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temporary.replace(MANIFEST)
        print(json.dumps({'platform': platform, 'archive_bytes': result['bytes'], 'parts': len(result['parts']), 'status': 'PACKAGED'}), flush=True)
    if all((ROOT / relative).is_file() for relative in SUPPLEMENTAL_FILES):
        refresh_supplemental_files()


def self_test() -> int:
    """Synthetic trees only; no download, mount, application or manifest writes."""
    import unittest

    class PackagingTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix='self-test-', dir=CACHE)
            self.base = Path(self.temp.name)
            self.tree = self.base / 'source' / 'LibreOffice'
            (self.tree / 'program').mkdir(parents=True)
            (self.tree / 'program/soffice.exe').write_bytes(b'synthetic executable fixture')
            (self.tree / 'LICENSE').write_text('Synthetic license fixture\n')

        def tearDown(self):
            self.temp.cleanup()

        def test_internal_link_and_original_notice(self):
            (self.tree / 'license-link').symlink_to('LICENSE')
            result = safe_tree(self.tree)
            self.assertEqual(result['links'], [{'path': 'license-link', 'target': 'LICENSE'}])
            self.assertEqual(result['license_files'][0]['sha256'], digest(self.tree / 'LICENSE'))

        def test_external_link_rejected(self):
            (self.tree / 'outside').symlink_to('../../')
            with self.assertRaisesRegex(ValueError, 'leaves its root'):
                safe_tree(self.tree)

        def test_missing_notices_rejected(self):
            (self.tree / 'LICENSE').unlink()
            with self.assertRaisesRegex(ValueError, 'license notices'):
                safe_tree(self.tree)

        def test_privileged_mode_rejected(self):
            (self.tree / 'program/soffice.exe').chmod(0o4755)
            with self.assertRaisesRegex(ValueError, 'Privileged'):
                safe_tree(self.tree)

        def test_missing_entry_rejected(self):
            (self.tree / 'program/soffice.exe').unlink()
            with self.assertRaisesRegex(ValueError, 'entry point'):
                pack_tree(self.tree, 'win32-x64', self.base)

        def test_deterministic_archive_preserves_bytes(self):
            archive, review = pack_tree(self.tree, 'win32-x64', self.base)
            before = digest(archive)
            os.utime(self.tree / 'LICENSE', (123, 123))
            pack_tree(self.tree, 'win32-x64', self.base)
            self.assertEqual(digest(archive), before)
            with tarfile.open(archive) as stream:
                self.assertEqual(stream.extractfile(review['entry']).read(), b'synthetic executable fixture')
                self.assertTrue(all(PurePosixPath(item.name).parts[0] == 'LibreOffice' for item in stream))

        def test_changed_archive_is_not_overwritten(self):
            archive, _ = pack_tree(self.tree, 'win32-x64', self.base)
            before = digest(archive)
            (self.tree / 'LICENSE').write_text('changed fixture')
            with self.assertRaisesRegex(ValueError, 'replace an archive'):
                pack_tree(self.tree, 'win32-x64', self.base)
            self.assertEqual(digest(archive), before)

        def test_parts_reassemble_and_tampering_fails(self):
            archive, _ = pack_tree(self.tree, 'win32-x64', self.base)
            bundle = publish(archive, 'win32-x64', self.base / 'parts')
            verify_parts(bundle)
            part = ROOT / bundle['parts'][0]['path']
            part.write_bytes(b'x' * part.stat().st_size)
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                verify_parts(bundle)
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                publish(archive, 'win32-x64', self.base / 'parts')

        def test_multiple_parts_preserve_order(self):
            from unittest.mock import patch
            archive, _ = pack_tree(self.tree, 'win32-x64', self.base)
            with patch(__name__ + '.PART_BYTES', 64):
                bundle = publish(archive, 'win32-x64', self.base / 'parts')
                self.assertGreater(len(bundle['parts']), 1)
                verify_parts(bundle)
                bundle['parts'].reverse()
                with self.assertRaisesRegex(ValueError, 'Reassembled archive'):
                    verify_parts(bundle)

        def _runtime_fixture(self, machine=0x8664):
            data = bytearray(128)
            data[:2] = b'MZ'
            struct.pack_into('<I', data, 0x3c, 64)
            data[64:68] = b'PE\0\0'
            struct.pack_into('<H', data, 68, machine)
            directory = self.tree / 'System64'
            directory.mkdir(exist_ok=True)
            source = directory / 'vcruntime140.dll'
            source.write_bytes(data)
            return {'vcruntime140.dll': digest(source)}

        def test_app_local_runtime_keeps_original_and_is_idempotent(self):
            expected = self._runtime_fixture()
            mapping = windows_app_local_runtime(self.tree, expected)
            self.assertEqual(mapping, windows_app_local_runtime(self.tree, expected))
            self.assertEqual(digest(self.tree / 'program/vcruntime140.dll'), expected['vcruntime140.dll'])
            self.assertEqual(digest(self.tree / 'System64/vcruntime140.dll'), expected['vcruntime140.dll'])
            self.assertEqual(mapping[0]['source_sha256'], mapping[0]['target_sha256'])

        def test_app_local_runtime_rejects_other_architecture(self):
            expected = self._runtime_fixture(machine=0x14c)
            with self.assertRaisesRegex(ValueError, 'x64'):
                windows_app_local_runtime(self.tree, expected)
            self.assertFalse((self.tree / 'program/vcruntime140.dll').exists())

        def test_app_local_runtime_rejects_hash_mismatch(self):
            expected = self._runtime_fixture()
            expected['vcruntime140.dll'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'hash'):
                windows_app_local_runtime(self.tree, expected)

        def test_app_local_runtime_rejects_existing_other_bytes(self):
            expected = self._runtime_fixture()
            target = self.tree / 'program/vcruntime140.dll'
            target.write_bytes(b'preserve this existing file')
            with self.assertRaisesRegex(ValueError, 'different'):
                windows_app_local_runtime(self.tree, expected)
            self.assertEqual(target.read_bytes(), b'preserve this existing file')

        def test_app_local_runtime_rejects_symlink(self):
            expected = self._runtime_fixture()
            (self.tree / 'program/vcruntime140.dll').symlink_to('../System64/vcruntime140.dll')
            with self.assertRaisesRegex(ValueError, 'link'):
                windows_app_local_runtime(self.tree, expected)

    CACHE.mkdir(parents=True, exist_ok=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PackagingTests))
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--download', action='store_true', help='Download and hash-check fixed official installers only')
    action.add_argument('--build', action='store_true', help='Explicitly construct reviewed portable archives')
    action.add_argument('--self-test', action='store_true', help='Run synthetic repository-local packaging checks')
    action.add_argument('--refresh-supplemental-files', action='store_true',
                        help='Finalize the eleven public metadata hashes after their last update; no archives are rebuilt')
    parser.add_argument('--platform', action='append', choices=tuple(SOURCES))
    parser.add_argument('--windows-tree', type=Path)
    parser.add_argument('--replace-platform', action='append', choices=tuple(SOURCES),
                        help='Explicitly adopt a reviewed new archive in the manifest; old archive parts are not overwritten')
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.refresh_supplemental_files:
        records = refresh_supplemental_files()
        print(json.dumps({'supplemental_files': len(records), 'status': 'MANIFEST_REFRESHED'}))
        return 0
    platforms = args.platform or list(SOURCES)
    if args.download:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(download_source, platforms))
    else:
        build(platforms, args.windows_tree, args.replace_platform)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
