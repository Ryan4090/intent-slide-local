#!/usr/bin/env python3
"""Maintainer-only, explicit online build of reviewed portable runtime payloads.

Never called by the product launcher. It downloads fixed official artifacts and
locked wheels into this repository, without installing or executing those files.
End-user startup verifies and extracts these committed bytes entirely offline.
"""
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.runtime/portable-build'
DEST = ROOT / 'vendor/portable'
PART_BYTES = 90 * 1024 * 1024
PYTHON_VERSION = '3.12.14'
PBS_RELEASE = '20260901'
CODEX_VERSION = '0.153.4'
PLATFORMS = {
 'darwin-arm64': {'python_target':'aarch64-apple-darwin','codex_target':'aarch64-apple-darwin',
  'python_sha256':'81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b','python_bytes':24981445,
  'codex_sri':'sha512-B1qhN3fa1ay0R0wGziXqgwSkB5icpYChNKHhtBHff/0UtSTC7z+l8aTtvMlGjH3E8HEvY3+njIJelM9CAAoVWg==',
  'pip_platform':'macosx_11_0_arm64'},
 'darwin-x64': {'python_target':'x86_64-apple-darwin','codex_target':'x86_64-apple-darwin',
  'python_sha256':'65b195c9cedc1fef6767f044f9822069adbd1bd9204d424ece4628776fdc04bb','python_bytes':24686153,
  'codex_sri':'sha512-vnSbbPzfoDZmmyzsxswsDDXQ06IVFBzkQU7/hroB3ji93Ok2utcsq8Psfk2tjF5r9mEx8RWFJhzuTGHG26/NDA==',
  'pip_platform':'macosx_11_0_x86_64'},
 'win32-x64': {'python_target':'x86_64-pc-windows-msvc','codex_target':'x86_64-pc-windows-msvc',
  'python_sha256':'7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14','python_bytes':21980728,
  'codex_sri':'sha512-lMkB43kJZH0VFr+hoXc11qqR7QtQIbkr07ALgj4urKL1osNyUyuy1iXd3Vzz2iCYvBUCSw7I0l/W1cEPGx9euQ==',
  'pip_platform':'win_amd64'},
}
SOURCE_ARCHIVES = {
 'pymupdf': {'component':'PyMuPDF','version':'1.28.2','filename':'pymupdf-1.28.2.tar.gz',
  'source_url':'https://files.pythonhosted.org/packages/a3/fb/b6761fa2d5266f2cdb24c3b91f4023070ab7848381417678e7a289a1d52a/pymupdf-1.28.2.tar.gz',
  'source_metadata_url':'https://pypi.org/pypi/PyMuPDF/1.28.2/json',
  'sha256':'5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249','bytes':87903557,
  'build_files':['pymupdf-1.28.2/setup.py','pymupdf-1.28.2/pyproject.toml','pymupdf-1.28.2/scripts/gh_release.py'],
  'license_members':['pymupdf-1.28.2/COPYING']},
 'mupdf': {'component':'MuPDF','version':'1.28.2','filename':'mupdf-1.28.2-source.tar.gz',
  'source_url':'https://github.com/ArtifexSoftware/mupdf-downloads/releases/download/1.28.2/mupdf-1.28.2-source.tar.gz',
  'source_metadata_url':'https://api.github.com/repos/ArtifexSoftware/mupdf-downloads/releases/tags/1.28.2',
  'sha256':'44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156','bytes':68898646,
  'build_files':['mupdf-1.28.2-source/Makefile','mupdf-1.28.2-source/Makerules','mupdf-1.28.2-source/Makethird',
                 'mupdf-1.28.2-source/scripts/mupdfwrap.py','mupdf-1.28.2-source/setup.py','mupdf-1.28.2-source/pyproject.toml'],
  'license_members':['mupdf-1.28.2-source/COPYING']},
}


def digest(path, algorithm='sha256'):
    h=hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def checked_download(url, filename, *, sha256=None, sri=None, size=None):
    """No lifecycle hooks; verify public source metadata before payload use."""
    def verify(candidate):
        if size is not None and candidate.stat().st_size!=size:raise ValueError('Artifact size mismatch: '+filename)
        if sha256 and digest(candidate)!=sha256:raise ValueError('Artifact SHA-256 mismatch: '+filename)
        if sri:
            algorithm,encoded=sri.split('-',1)
            actual=base64.b64encode(bytes.fromhex(digest(candidate,algorithm))).decode()
            if actual!=encoded:raise ValueError('Artifact npm SRI mismatch: '+filename)
    target=CACHE/'downloads'/filename
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        temporary=target.with_suffix(target.suffix+'.partial')
        request=urllib.request.Request(url,headers={'User-Agent':'Intent-Slide-portable-maintainer/1'})
        try:
            with urllib.request.urlopen(request,timeout=60) as response, temporary.open('wb') as output:
                count=0
                while block:=response.read(1024*1024):
                    count+=len(block)
                    if count>512*1024*1024:raise ValueError('Artifact exceeds bounded download limit')
                    output.write(block)
            verify(temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    verify(target)
    return target


def file_record(path):
    return {'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':digest(path),'bytes':path.stat().st_size}


def publish_archive(source, directory, name):
    directory.mkdir(parents=True,exist_ok=True)
    info={'sha256':digest(source),'bytes':source.stat().st_size,'format':'tar.gz'}
    if info['bytes']<=PART_BYTES:
        target=directory/name
        if target.exists() and digest(target)!=info['sha256']:raise ValueError('Refusing to replace different artifact: '+str(target))
        if not target.exists():shutil.copyfile(source,target)
        return {**file_record(target),'format':'tar.gz'}
    parts=[]
    with source.open('rb') as stream:
        number=0
        while block:=stream.read(PART_BYTES):
            number+=1;target=directory/(name+f'.part{number:03d}')
            expected=hashlib.sha256(block).hexdigest()
            if target.exists() and digest(target)!=expected:raise ValueError('Refusing to replace changed archive part')
            if not target.exists():target.write_bytes(block)
            parts.append(file_record(target))
    return {**info,'path':name,'parts':parts}


def archive_review(path, required):
    """Inspect members and license text without running or extracting binaries."""
    licenses=[];executables=[];members={};repeated_directories=[]
    with tarfile.open(path,'r:gz') as archive:
        for item in archive:
            p=PurePosixPath(item.name)
            if p.is_absolute() or '..' in p.parts or '\\' in item.name or ':' in item.name:raise ValueError('Unsafe archive path')
            if not (item.isfile() or item.isdir() or item.issym() or item.islnk()):raise ValueError('Unsupported archive member')
            if str(p) in members:
                if item.isdir() and members[str(p)].isdir() and item.size==members[str(p)].size==0:
                    repeated_directories.append(str(p));continue
                raise ValueError('Duplicate archive member')
            if item.issym() or item.islnk():
                target=item.linkname
                resolved=posixpath.normpath(posixpath.join(str(p.parent) if item.issym() else '',target))
                if (not target or '\\' in target or ':' in target or target.startswith('/')
                        or resolved=='..' or resolved.startswith('../')):raise ValueError('Unsafe archive link')
            members[str(p)]=item
            if item.isfile() and item.mode&0o111:executables.append(item.name)
            lower=p.name.lower()
            if item.isfile() and item.size<2*1024*1024 and ('license' in lower or 'copying' in lower or 'notice' in lower):
                data=archive.extractfile(item).read()
                licenses.append({'member':item.name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
        if not all(name in members and members[name].isfile() for name in required):raise ValueError('Official archive lacks a required regular runtime entrypoint')
    return {'member_count':len(members),'license_members':licenses,'executable_members':executables,
            'repeated_directories':repeated_directories}


def build_python(platform, details):
    name=f'cpython-{PYTHON_VERSION}+{PBS_RELEASE}-{details["python_target"]}-install_only_stripped.tar.gz'
    url=f'https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_RELEASE}/'+name.replace('+','%2B')
    source=checked_download(url,name,sha256=details['python_sha256'],size=details['python_bytes'])
    entry='python/python.exe' if platform.startswith('win32') else 'python/bin/python3.12'
    review=archive_review(source,[entry])
    result=publish_archive(source,DEST/'python',platform+'.tar.gz')
    return {**result,'version':PYTHON_VERSION,'source_url':url,'source_metadata_url':f'https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{PBS_RELEASE}',
            'entry':entry,'license':'PSF-2.0 and licenses of bundled components; see archive license members','review':review}


def build_codex(platform, details):
    name=f'codex-{CODEX_VERSION}-{platform}.tgz'
    url='https://registry.npmjs.org/@openai/codex/-/'+name
    source=checked_download(url,name,sri=details['codex_sri'])
    directory='package/vendor/'+details['codex_target']+'/bin'
    suffix='.exe' if platform.startswith('win32') else ''
    entry=directory+'/codex'+suffix
    helper=directory+'/codex-code-mode-host'+suffix
    review=archive_review(source,[entry,helper])
    result=publish_archive(source,DEST/'codex',platform+'.tgz')
    return {**result,'version':CODEX_VERSION,'source_url':url,'source_metadata_url':f'https://registry.npmjs.org/@openai/codex/{CODEX_VERSION}-{platform}',
            'integrity':details['codex_sri'],'entry':entry,'bin_dir':directory,'helper':helper,
            'license':'Apache-2.0; bundled component notices retained separately','review':review}


def build_wheels(platform, details):
    download=CACHE/'wheels'/platform
    download.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','pip','--isolated','--disable-pip-version-check','download',
             '--index-url','https://pypi.org/simple','--no-cache-dir','--require-hashes','--only-binary=:all:',
             '--platform',details['pip_platform'],'--python-version','312','--implementation','cp',
             '--abi','cp312','--abi','abi3','--abi','none','--dest',str(download),'-r',str(ROOT/'requirements-local.lock')]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=600)
    (CACHE/(platform+'-pip-download.log')).write_bytes(result.stdout+result.stderr)
    if result.returncode:raise ValueError('Locked wheel download failed for '+platform+'; inspect private build log')
    packages=json.loads((ROOT/'vendor/runtime-dependencies.json').read_text())['packages']
    permitted={h:entry for entry in packages for h in entry['wheel_sha256']}
    wheels=[]
    for source in sorted(download.glob('*.whl')):
        sha=digest(source)
        if sha not in permitted:raise ValueError('Wheel is absent from original locked metadata: '+source.name)
        package=permitted[sha]
        target=DEST/'wheels'/platform/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and digest(target)!=sha:raise ValueError('Existing wheel changed')
        if not target.exists():shutil.copyfile(source,target)
        license_members=[];startup_hooks=[]
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.filename.endswith('.pth') or '.data/scripts/' in info.filename:startup_hooks.append(info.filename)
                if any(term in PurePosixPath(info.filename).name.lower() for term in ['license','copying','notice']) and info.file_size<2*1024*1024:
                    data=archive.read(info)
                    license_members.append({'member':info.filename,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
        wheels.append({**file_record(target),'package':package['name'],'version':package['version'],
                       'source_url':package['metadata_url'],'license_files':package['license_files'],
                       'archive_license_members':license_members,'startup_hooks':startup_hooks})
    if len(wheels)!=len(packages) or {row['package'] for row in wheels}!={row['name'] for row in packages}:
        raise ValueError('Wheel set differs from locked package set')
    return wheels


def collect_licenses():
    sources={
      'codex-APACHE-2.0.txt':(f'https://raw.githubusercontent.com/openai/codex/rust-v{CODEX_VERSION}/LICENSE','d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc'),
      'codex-NOTICE.txt':(f'https://raw.githubusercontent.com/openai/codex/rust-v{CODEX_VERSION}/NOTICE','9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915'),
      'python-build-standalone-LICENSE.txt':(f'https://raw.githubusercontent.com/astral-sh/python-build-standalone/{PBS_RELEASE}/LICENSE','1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5'),
      'ripgrep-15.2.0-UNLICENSE.txt':('https://raw.githubusercontent.com/BurntSushi/ripgrep/15.2.0/UNLICENSE','7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c'),
      'ripgrep-15.2.0-LICENSE-MIT.txt':('https://raw.githubusercontent.com/BurntSushi/ripgrep/15.2.0/LICENSE-MIT','0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f'),
      'zsh-LICENCE.txt':('https://raw.githubusercontent.com/zsh-users/zsh/77045ef899e53b9598bebc5a41db93a548a40ca6/LICENCE','d06fdf3ef9b1ec69d6b9e170b0a9516fbad3523261ff1668bde3bfea6e0ef5f5'),
    }
    result=[]
    for name,(url,sha) in sources.items():
        path=checked_download(url,name,sha256=sha)
        target=DEST/'licenses'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        result.append({**file_record(target),'source_url':url})
    return result


def collect_sources():
    """Ship exact upstream source and build instructions, without executing them."""
    result=[]
    for key, definition in SOURCE_ARCHIVES.items():
        source=checked_download(definition['source_url'],definition['filename'],
                                sha256=definition['sha256'],size=definition['bytes'])
        required=definition['build_files']+definition['license_members']
        review=archive_review(source,required)
        license_files=[]
        with tarfile.open(source,'r:gz') as archive:
            for name in definition['license_members']:
                member=archive.getmember(name)
                if member.size>2*1024*1024:raise ValueError('Source license exceeds bound')
                data=archive.extractfile(member).read()
                target=DEST/'licenses'/(key+'-'+PurePosixPath(name).name+'.txt')
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
                license_files.append({**file_record(target),'archive_member':name})
        result.append({**publish_archive(source,DEST/'sources',definition['filename']),
                       **{field:definition[field] for field in ['component','version','source_url','source_metadata_url','build_files']},
                       'license_files':license_files,'review':review,'modified':False,'build_executed':False})
    return result


def build(platforms):
    CACHE.mkdir(parents=True,exist_ok=True)
    manifest_path=DEST/'runtime-manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schema_version':1,'platforms':{}}
    manifest.update(python_version=PYTHON_VERSION,codex_version=CODEX_VERSION,
        requirements_lock=file_record(ROOT/'requirements-local.lock'),
        maintainer_only_builder='scripts/build_portable_runtime.py',
        component_review='docs/local-mvp/PORTABLE_COMPONENT_REVIEW.md')
    manifest['licenses']=collect_licenses()
    sources=collect_sources()
    # Preserve independently maintained renderer source records.
    names={row['component'] for row in sources}
    manifest['sources']=[row for row in manifest.get('sources',[]) if row.get('component') not in names]+sources
    for platform in platforms:
        details=PLATFORMS[platform]
        with ThreadPoolExecutor(max_workers=3) as pool:
            python=pool.submit(build_python,platform,details)
            codex=pool.submit(build_codex,platform,details)
            wheels=pool.submit(build_wheels,platform,details)
            completed={'python':python.result(),'codex':codex.result(),'wheels':wheels.result()}
        manifest['platforms'].setdefault(platform,{}).update(completed)
        manifest_path.parent.mkdir(parents=True,exist_ok=True)
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        print(json.dumps({'platform':platform,'status':'ARTIFACTS_VERIFIED','wheels':len(completed['wheels']),
                          'python_bytes':completed['python']['bytes'],'codex_bytes':completed['codex']['bytes']},ensure_ascii=False),flush=True)
    print(json.dumps({'manifest':str(manifest_path.relative_to(ROOT)),'sha256':digest(manifest_path)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true',help='Explicitly fetch reviewed official payloads; maintainer use only')
    parser.add_argument('--platform',choices=tuple(PLATFORMS),action='append')
    args=parser.parse_args()
    if not args.build:parser.error('No action taken. --build is required for this maintainer network operation.')
    try:build(args.platform or list(PLATFORMS))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Portable build failed: '+str(error),file=sys.stderr);sys.exit(1)
