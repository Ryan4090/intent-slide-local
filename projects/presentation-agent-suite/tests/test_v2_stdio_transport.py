"""Portable real-pipe tests; native Windows Job tests run only on Windows."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from presentation_agents.v2.stdio_transport import StdioTransport, TransportError, probe_command, resolve_command


class StdioTransportTests(unittest.TestCase):
    def launch(self, code, **kwargs):
        owner = StdioTransport.launch([sys.executable, '-u', '-c', code], **kwargs)
        self.addCleanup(owner.close)
        return owner

    def test_unicode_frame_roundtrip_without_select(self):
        owner = self.launch('import sys; line=sys.stdin.buffer.readline();sys.stdout.buffer.write(line);sys.stdout.flush()')
        data = ('{"text":"한글 Windows macOS"}\n').encode()
        with patch('select.select', side_effect=AssertionError('No select on anonymous pipes')):
            owner.write_frame(data, timeout=1)
        self.assertEqual(owner.process.stdout.readline(), data)

    def test_partial_writes_preserve_every_byte(self):
        owner = self.launch('import sys;sys.stdout.buffer.write(sys.stdin.buffer.readline());sys.stdout.flush()')
        stream = owner.process.stdin
        class ShortWrites:
            def write(self, data): return stream.write(data[:3])
        owner._input = ShortWrites()
        data = '한글 partial write\n'.encode()
        owner.write_frame(data, timeout=1)
        self.assertEqual(owner.process.stdout.readline(), data)

    def test_nonreading_peer_times_out_and_reaps_owned_process(self):
        owner = self.launch('import time;time.sleep(30)', max_frame_bytes=2*1024*1024)
        start = time.monotonic()
        with self.assertRaises(TransportError) as caught:
            owner.write_frame(b'x'*(1024*1024), timeout=.1)
        self.assertEqual(caught.exception.code, 'REQUEST_TIMEOUT')
        owner.close()
        self.assertLess(time.monotonic()-start, 3)
        self.assertIsNotNone(owner.process.poll())
        self.assertFalse(owner._writer.is_alive())

    def test_input_size_rejected_before_pipe_write(self):
        owner = self.launch('import time;time.sleep(30)', max_frame_bytes=8)
        with self.assertRaises(TransportError) as caught: owner.write_frame(b'x'*9, timeout=1)
        self.assertEqual(caught.exception.code, 'INPUT_LIMIT')

    def test_probe_combined_output_limit_and_timeout_are_bounded(self):
        for code, expected in [('import sys;sys.stderr.write("x"*10000)', 'OUTPUT_LIMIT'),
                               ('import time;time.sleep(30)', 'REQUEST_TIMEOUT')]:
            with self.subTest(expected=expected), self.assertRaises(TransportError) as caught:
                probe_command([sys.executable,'-u','-c',code], timeout=.2, limit=100)
            self.assertEqual(caught.exception.code, expected)
        code, output = probe_command([sys.executable,'-c','print("metadata")'], timeout=2)
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), b'metadata')

    def test_bootstrap_waits_for_gate_before_starting_command(self):
        import presentation_agents.v2.stdio_transport as module
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary)/'started'
            command = [sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).write_text("ok")']
            process = subprocess.Popen([sys.executable,'-I','-u',module.__file__,'--bridge',json.dumps(command)],
                                       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            time.sleep(.1)
            self.assertFalse(marker.exists())
            self.assertEqual(process.communicate(b'\0',timeout=3), (b'',b''))
            self.assertTrue(marker.exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Object APIs')
    def test_windows_job_cancels_descendant_after_direct_peer_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary)/'orphan-wrote'
            child = f'import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).write_text("orphan")'
            peer = f'import subprocess,sys;subprocess.Popen([sys.executable,"-c",{child!r}]);print("spawned",flush=True)'
            owner = self.launch(peer)
            self.assertEqual(owner.process.stdout.readline().strip(), b'spawned')
            owner.process.wait(timeout=2)
            owner.close()
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_job_assignment_failure_never_sends_gate_or_starts_cli(self):
        events = []
        class FakeProcess:
            pid = 123
            stdin, stdout, stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            def kill(self): events.append('kill-bootstrap')
            def wait(self, timeout): return 0
        class FailedJob:
            def assign(self, pid):
                events.append('assign-rejected')
                raise TransportError('PROCESS_ISOLATION', 'fixture denial')
            def close(self): events.append('close-job')
        with patch('presentation_agents.v2.stdio_transport.is_windows',return_value=True), \
                patch('presentation_agents.v2.stdio_transport._WindowsJob',return_value=FailedJob()), \
                patch('presentation_agents.v2.stdio_transport.subprocess.Popen',return_value=FakeProcess()) as spawn, \
                patch.object(StdioTransport,'write_frame',side_effect=AssertionError('gate sent before job assignment')), \
                self.assertRaises(TransportError) as caught:
            StdioTransport.launch([sys.executable,'-c','print("not executed")'])
        self.assertEqual(caught.exception.code,'PROCESS_ISOLATION')
        self.assertEqual(events,['assign-rejected','close-job','kill-bootstrap'])
        self.assertEqual(spawn.call_args.kwargs['start_new_session'],False)
        self.assertFalse(spawn.call_args.kwargs['shell'])
        self.assertIn('--bridge',spawn.call_args.args[0])

    def test_close_interrupts_a_pending_write_without_waiting_for_request_deadline(self):
        owner = self.launch('import time;time.sleep(30)',max_frame_bytes=2*1024*1024)
        outcomes=[]
        def send():
            try: owner.write_frame(b'x'*(1024*1024),timeout=30)
            except TransportError as exc: outcomes.append(exc.code)
        sender=threading.Thread(target=send);sender.start()
        time.sleep(.05)
        start=time.monotonic();owner.close();sender.join(timeout=2)
        self.assertFalse(sender.is_alive())
        self.assertLess(time.monotonic()-start,3)
        self.assertTrue(outcomes)

    def test_windows_paired_codex_npm_helper_resolution(self):
        from presentation_agents.v2.provider import CodexProvider
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);shim=root/'codex.cmd';shim.write_text('never execute');shim.chmod(0o755)
            main=root/'node_modules/@openai/codex';(main/'bin').mkdir(parents=True)
            (main/'bin/codex.js').write_text('// fixture')
            (main/'package.json').write_text(json.dumps({'name':'@openai/codex','version':'0.153.4','bin':{'codex':'bin/codex.js'},
                'optionalDependencies':{'@openai/codex-win32-x64':'npm:@openai/codex@0.153.4-win32-x64'}}))
            native=root/'node_modules/@openai/codex-win32-x64';native.mkdir()
            (native/'package.json').write_text(json.dumps({'name':'@openai/codex','version':'0.153.4-win32-x64','os':['win32'],'cpu':['x64']}))
            binary=native/'vendor/x86_64-pc-windows-msvc/bin';binary.mkdir(parents=True)
            for name in ['codex.exe','codex-code-mode-host.exe']:
                p=binary/name;p.write_bytes(b'fixture');p.chmod(0o755)
            (root/'node.exe').write_bytes(b'fixture')
            with patch('presentation_agents.v2.stdio_transport.is_windows',return_value=True), \
                 patch('platform.system',return_value='Windows'),patch('platform.machine',return_value='AMD64'):
                provider=CodexProvider(command=[str(shim),'app-server'])
                env=provider._runtime_environment()
            self.assertEqual(env['PATH'].split(os.pathsep)[0],str(binary.resolve()))
            self.assertEqual(provider._runtime['host_discovery'],'npm_platform_package')
            self.assertEqual(provider._runtime['code_mode_host'],str((binary/'codex-code-mode-host.exe').resolve()))

    def test_windows_batch_resolution_uses_reviewed_metadata_and_node_without_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for cli,package,entry in [('codex','@openai/codex','bin/codex.js'),('claude','@anthropic-ai/claude-code','cli.js'),('claude','@anthropic-ai/claude-code','bin/claude.exe')]:
                shim=root/(cli+'.cmd');shim.write_text('must not execute')
                package_root=root/'node_modules'/package
                js=package_root/entry;js.parent.mkdir(parents=True,exist_ok=True);js.write_text('// official-layout fixture')
                (package_root/'package.json').write_text(json.dumps({'name':package,'version':'1.2.3','bin':{cli:entry}}))
                node=root/'node.exe';node.write_bytes(b'fixture')
                with patch('presentation_agents.v2.stdio_transport.is_windows',return_value=True):
                    expected=([str(js.resolve()),'--version'] if entry.endswith('.exe')
                              else [str(node.resolve()),str(js.resolve()),'--version'])
                    self.assertEqual(resolve_command([str(shim),'--version']),expected)
            (package_root/'package.json').write_text('{"name":"wrong"}')
            with patch('presentation_agents.v2.stdio_transport.is_windows',return_value=True), self.assertRaises(TransportError):
                resolve_command([str(shim)])


if __name__ == '__main__': unittest.main()
