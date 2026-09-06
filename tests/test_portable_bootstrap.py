"""Offline bootstrap: real tiny archives/process locks; pip/model processes mocked."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
definition=importlib.util.spec_from_file_location('portable_bootstrap',ROOT/'scripts/portable_bootstrap.py')
boot=importlib.util.module_from_spec(definition);definition.loader.exec_module(boot)


class PortableBootstrapTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.runtime').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='bootstrap space & test ',dir=ROOT/'.runtime')
        self.root=Path(self.temp.name);self.calls=[]
        (self.root/'vendor/portable').mkdir(parents=True)
        lock=self.root/'requirements-local.lock';lock.write_text('Flask==3.1.3 --hash=sha256:'+'0'*64+'\n')
        self.py=self.archive('python.tgz',{'python/bin/python3.12':b'fixture interpreter'})
        self.py['entry']='python/bin/python3.12'
        self.codex=self.archive('codex.tgz',{'bin/codex':b'fixture codex','bin/codex-code-mode-host':b'fixture helper'})
        self.codex.update(entry='bin/codex',bin_dir='bin')
        self.renderer=self.archive('renderer.tgz',{'office/soffice':b'fixture renderer'})
        self.renderer['entry']='office/soffice'
        wheel=self.root/'vendor/portable/fixture.whl';wheel.write_bytes(b'fixture locked wheel, mocked installer')
        manifest={'schema_version':1,'requirements_lock':self.record(lock),'platforms':{'darwin-arm64':{'python':self.py,'codex':self.codex,'wheels':[self.record(wheel)]}}}
        (self.root/'vendor/portable/runtime-manifest.json').write_text(json.dumps(manifest))
        (self.root/'vendor/portable/renderer-manifest.json').write_text(json.dumps({'platforms':{'darwin-arm64':self.renderer}}))
        self.platform=patch.object(boot,'platform_id',return_value='darwin-arm64');self.platform.start()
        self.system=patch.object(boot.sys,'platform','darwin');self.system.start()

    def tearDown(self):
        self.system.stop();self.platform.stop();self.temp.cleanup()

    def record(self,path):
        return {'path':path.relative_to(self.root).as_posix(),'bytes':path.stat().st_size,'sha256':boot.sha256(path)}

    def archive(self,name,files):
        path=self.root/'vendor/portable'/name
        with tarfile.open(path,'w:gz') as bundle:
            for name,data in files.items():
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o755
                bundle.addfile(item,io.BytesIO(data))
        return self.record(path)

    def fake_run(self,command,**kwargs):
        self.calls.append((command,kwargs))
        if 'venv' in command:
            environment=Path(command[-1]);(environment/'bin').mkdir(parents=True)
            (environment/'bin/python').symlink_to(Path(command[0]))
            (environment/'pyvenv.cfg').write_text('home = '+str(Path(command[0]).parent)+'\ninclude-system-site-packages = false\n')
        return subprocess.CompletedProcess(command,0)

    def test_custom_root_preserved_and_only_hash_locked_offline_install(self):
        executable,env=boot.prepare(root=self.root,run=self.fake_run)
        self.assertTrue(executable.is_relative_to(self.root))
        self.assertTrue(Path(env['INTENT_SLIDE_CODEX']).is_relative_to(self.root))
        self.assertTrue(Path(env['INTENT_SLIDE_SOFFICE']).is_relative_to(self.root))
        install=next(c for c,_ in self.calls if 'install' in c)
        for arg in ('--no-index','--require-hashes','--only-binary=:all:','--isolated','--no-input'):
            self.assertIn(arg,install)
        self.assertFalse(any(kwargs.get('shell') for _,kwargs in self.calls))
        self.assertTrue(all(kwargs['cwd']==self.root for _,kwargs in self.calls))
        self.assertEqual(env['PIP_NO_INDEX'],'1')

    def test_marker_does_not_hide_deleted_or_modified_archive_files(self):
        cache=self.root/'cache';cache.mkdir();dest=self.root/'extracted'
        boot.extract(self.codex,dest,cache,root=self.root)
        (dest/'bin/codex').write_bytes(b'changed')
        boot.extract(self.codex,dest,cache,root=self.root)
        self.assertEqual((dest/'bin/codex').read_bytes(),b'fixture codex')
        self.assertEqual(len(list(self.root.glob('extracted.recovered-*'))),1)
        (dest/'bin/codex').unlink()
        boot.extract(self.codex,dest,cache,root=self.root)
        self.assertTrue((dest/'bin/codex').is_file())

    def test_partial_directory_recovered_and_original_bytes_preserved(self):
        cache=self.root/'cache';cache.mkdir();dest=self.root/'extracted';dest.mkdir()
        (dest/'partial').write_text('interrupted owned fixture')
        boot.extract(self.codex,dest,cache,root=self.root)
        prior=next(self.root.glob('extracted.recovered-*'))
        self.assertEqual((prior/'partial').read_text(),'interrupted owned fixture')
        self.assertTrue((dest/'.intent-archive-sha256').is_file())

    def test_symlink_parents_and_unsafe_manifest_paths_fail_before_process(self):
        for path in ('../escape','/absolute','C:/escape','a\\b'):
            with self.subTest(path=path),self.assertRaises(RuntimeError):boot.inside(self.root,path)
        outside=self.root/'outside';outside.mkdir()
        (self.root/'.runtime').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(RuntimeError):boot.prepare(root=self.root,run=self.fake_run)
        self.assertEqual(self.calls,[]);self.assertEqual(list(outside.iterdir()),[])

    def test_incomplete_venv_recovers_and_failed_install_has_no_receipt(self):
        env=self.root/'.runtime/portable/darwin-arm64/environment';env.mkdir(parents=True)
        (env/'partial').write_text('preserve')
        def failure(command,**kwargs):
            result=self.fake_run(command,**kwargs)
            if 'install' in command:raise subprocess.CalledProcessError(1,command)
            return result
        with self.assertRaises(subprocess.CalledProcessError):boot.prepare(root=self.root,run=failure)
        self.assertFalse((env/'.intent-dependencies-sha256').exists())
        self.assertEqual(len(list(env.parent.glob('environment.recovered-*'))),1)
        boot.prepare(root=self.root,run=self.fake_run)
        self.assertTrue((env/'.intent-dependencies-sha256').exists())

    def test_python_pip_and_loader_pollution_removed_without_touching_native_auth(self):
        dirty={'PYTHONPATH':'/foreign','PYTHONHOME':'/foreign','PIP_TARGET':'/foreign','PIP_CONFIG_FILE':'/foreign',
               'DYLD_INSERT_LIBRARIES':'/foreign','LD_PRELOAD':'/foreign','VIRTUAL_ENV':'/foreign',
               'INTENT_SLIDE_CODEX':'/foreign','FIXTURE_NATIVE_AUTH':'unchanged non-secret fixture'}
        with patch.dict(os.environ,dirty):clean=boot.clean_environment(self.root)
        for key in ('PYTHONPATH','PYTHONHOME','PIP_TARGET','DYLD_INSERT_LIBRARIES','LD_PRELOAD','VIRTUAL_ENV','INTENT_SLIDE_CODEX'):
            self.assertNotIn(key,clean)
        self.assertEqual(clean['PIP_CONFIG_FILE'],os.devnull)
        self.assertEqual(clean['FIXTURE_NATIVE_AUTH'],dirty['FIXTURE_NATIVE_AUTH'])

    def test_foreign_venv_home_is_preserved_and_rebuilt_before_execution(self):
        executable,_=boot.prepare(root=self.root,run=self.fake_run)
        environment=executable.parent.parent
        (environment/'pyvenv.cfg').write_text('home = /foreign/python\ninclude-system-site-packages = true\n')
        self.calls=[]
        boot.prepare(root=self.root,run=self.fake_run)
        self.assertTrue(any('venv' in command for command,_ in self.calls))
        self.assertEqual(len(list(environment.parent.glob('environment.recovered-*'))),1)

    def test_receipt_does_not_skip_dependency_failure_and_repair_reinstalls(self):
        boot.prepare(root=self.root,run=self.fake_run);self.calls=[];first=True
        def broken(command,**kwargs):
            nonlocal first
            result=self.fake_run(command,**kwargs)
            if '-c' in command and first:
                first=False;return subprocess.CompletedProcess(command,1)
            return result
        boot.prepare(root=self.root,run=broken)
        install=next(command for command,_ in self.calls if 'install' in command)
        self.assertIn('--force-reinstall',install)

    def test_foreign_site_packages_link_is_not_executed_under_valid_marker(self):
        executable,_=boot.prepare(root=self.root,run=self.fake_run)
        environment=executable.parent.parent
        outside=self.root/'foreign-packages';outside.mkdir()
        (environment/'site-packages').symlink_to(outside,target_is_directory=True)
        self.calls=[]
        boot.prepare(root=self.root,run=self.fake_run)
        self.assertTrue(any('venv' in command for command,_ in self.calls))
        self.assertEqual(list(outside.iterdir()),[])

    def test_all_sitecustomize_module_forms_are_rejected_before_environment_python(self):
        executable,_=boot.prepare(root=self.root,run=self.fake_run)
        environment=executable.parent.parent
        site=environment/'site-packages';site.mkdir()
        python_dir=environment.parent/'base';python=python_dir/self.py['entry']
        for name in ('sitecustomize.pyc','usercustomize.pyc','sitecustomize.abi3.so','sitecustomize','USERCUSTOMIZE'):
            with self.subTest(name=name):
                path=site/name
                if '.' not in name:path.mkdir()
                else:path.write_bytes(b'fixture startup module; never executed')
                self.assertFalse(boot._venv_matches(environment,executable,python,python_dir))
                if path.is_dir():path.rmdir()
                else:path.unlink()

    def test_parts_require_order_whole_sha_and_restore_corrupt_owned_cache(self):
        source=self.root/self.codex['path'];data=source.read_bytes();parts=[]
        for n,chunk in enumerate((data[:31],data[31:])):
            path=self.root/f'part{n}';path.write_bytes(chunk);parts.append(self.record(path))
        spec={**self.codex,'parts':parts,'path':'joined.tgz'};cache=self.root/'cache';cache.mkdir()
        result=boot.materialize(spec,cache,root=self.root);self.assertEqual(result.read_bytes(),data)
        result.write_bytes(b'partial corrupt cache')
        result=boot.materialize(spec,cache,root=self.root);self.assertEqual(result.read_bytes(),data)
        bad={**spec,'parts':parts[::-1],'path':'wrong.tgz'}
        with self.assertRaises(RuntimeError):boot.materialize(bad,cache,root=self.root)
        self.assertFalse((cache/'wrong.tgz').exists())

    def test_archive_traversal_and_escaping_symlink_never_publish(self):
        cache=self.root/'cache';cache.mkdir()
        for kind in ('traversal','symlink'):
            path=self.root/'vendor/portable'/f'{kind}.tgz'
            with tarfile.open(path,'w:gz') as bundle:
                item=tarfile.TarInfo('../outside' if kind=='traversal' else 'link')
                if kind=='symlink':item.type=tarfile.SYMTYPE;item.linkname='/outside'
                bundle.addfile(item)
            with self.assertRaises((RuntimeError,tarfile.TarError)):boot.extract(self.record(path),self.root/kind,cache,root=self.root)
            self.assertFalse((self.root/kind).exists())

    def test_held_lock_excludes_other_process_then_releases(self):
        directory=self.root/'locks';directory.mkdir()
        code='''import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('boot',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:
    with m.setup_lock(Path(sys.argv[2]),timeout=.15): pass
except RuntimeError:sys.exit(7)
'''
        command=[sys.executable,'-I','-B','-c',code,str(ROOT/'scripts/portable_bootstrap.py'),str(directory)]
        with boot.setup_lock(directory):
            self.assertEqual(subprocess.run(command,timeout=5,capture_output=True).returncode,7)
        self.assertEqual(subprocess.run(command,timeout=5,capture_output=True).returncode,0)

    def test_main_argument_and_cwd_propagation_without_shell(self):
        python=self.root/'environment python';env={'fixture':'local'}
        with patch.object(boot,'prepare',return_value=(python,env)) as prepare,patch.object(boot.subprocess,'call',return_value=0) as call:
            self.assertEqual(boot.main(['start','--port','4351','--provider','gemini'],root=self.root),0)
            prepare.assert_called_once_with(root=self.root)
            self.assertEqual(call.call_args.args[0], [str(python),'-I','-B',str(self.root/'scripts/local_mvp.py'),'start','--port','4351','--provider','gemini'])
            self.assertEqual(call.call_args.kwargs,{'cwd':self.root,'env':env})
        with patch.object(boot,'prepare',return_value=(python,env)),patch.object(boot.subprocess,'call',return_value=0) as call:
            boot.main([],root=self.root);self.assertEqual(call.call_args.args[0][-2:],['start','--open'])

    def test_platform_scope_and_os_launcher_static_contract(self):
        self.platform.stop()
        for system,machine,expected in [('darwin','arm64','darwin-arm64'),('darwin','x86_64','darwin-x64'),('win32','AMD64','win32-x64')]:
            with patch.object(boot.sys,'platform',system),patch.object(boot.platform,'machine',return_value=machine):self.assertEqual(boot.platform_id(),expected)
        for system,machine in [('linux','x86_64'),('win32','ARM64')]:
            with patch.object(boot.sys,'platform',system),patch.object(boot.platform,'machine',return_value=machine),self.assertRaises(RuntimeError):boot.platform_id()
        self.platform.start()
        shell=(ROOT/'intent-slide').read_text();windows=(ROOT/'intent-slide.cmd').read_text()
        self.assertIn('-I -B',shell);self.assertIn('mktemp',shell);self.assertNotIn('curl',shell)
        self.assertIn('DisableDelayedExpansion',windows);self.assertIn('certutil.exe',windows)
        self.assertIn('fsutil.exe',windows);self.assertNotIn('ExecutionPolicy',windows)
        self.assertNotIn('for /f',windows.lower().replace('rem no for /f','rem'))
        result=subprocess.run(['/bin/sh',str(ROOT/'intent-slide'),'--help'],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0)

if __name__=='__main__':unittest.main()
