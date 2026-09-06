#!/usr/bin/env python3
"""Maintainer smoke: bundled runtime, real PPTX render and local HTTP boundary."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import portable_bootstrap

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'projects/presentation-agent-suite'


def main():
    python, environment = portable_bootstrap.prepare()
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve())], env=environment, cwd=ROOT)
    os.environ.update(environment)
    sys.path.insert(0, str(SUITE/'src'))
    from presentation_agents.v2.stdio_transport import StdioTransport
    from presentation_agents.v2.portable_render import find_soffice
    assert find_soffice() == environment['INTENT_SLIDE_SOFFICE']
    if sys.platform == 'win32':
        for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
            assert (Path(environment['INTENT_SLIDE_SOFFICE']).parent/name).is_file(), name
    version = subprocess.run([environment['INTENT_SLIDE_CODEX'], '--version'], capture_output=True, text=True, check=True, timeout=30)
    assert version.stdout.strip() == 'codex-cli 0.153.4', version.stdout
    environment['PYTHONPATH'] = str(SUITE/'src')
    tests = ['test_v2_stdio_transport.py', 'test_v2_login.py', 'test_v2_discovery.py',
             'test_v2_service.py', 'test_v2_portable_render.py']
    for pattern in tests:
        subprocess.run([str(python), '-m', 'unittest', 'discover', '-s', str(SUITE/'tests'), '-p', pattern],
                       cwd=ROOT, env=environment, check=True, timeout=180)
    with tempfile.TemporaryDirectory(prefix='clone smoke 한글 ', dir=ROOT/'.runtime') as work:
        owner = StdioTransport.launch([str(python), str(SUITE/'scripts/presentation_console.py'),
            '--data-dir', work, '--no-runner', '--port', '48317'], cwd=ROOT, env=environment)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(owner.process.stdout.readline()), daemon=True)
        reader.start()
        drain = threading.Thread(target=lambda: owner.process.stderr.read(1024*1024), daemon=True)
        drain.start()
        try:
            line = lines.get(timeout=20)
            launch = json.loads(line)
            url = urlsplit(launch['url'])
            connection = http.client.HTTPConnection('127.0.0.1', url.port, timeout=5)
            try:
                connection.request('GET','/api/v2/runs')
                response = connection.getresponse(); assert response.status == 401; response.read()
                body = json.dumps({'bootstrap_token':url.fragment.removeprefix('session=')})
                connection.request('POST','/api/v2/session',body,{'Content-Type':'application/json'})
                response = connection.getresponse(); assert response.status == 200
                cookie = response.getheader('Set-Cookie').split(';',1)[0]
                token = json.loads(response.read())['csrf_token']
                connection.request('GET','/api/v2/runs',headers={'Cookie':cookie})
                response = connection.getresponse(); assert response.status == 200
                assert json.loads(response.read()) == {'runs':[]}
                connection.request('POST','/api/v2/providers/login',json.dumps({'provider':'codex'}),{'Cookie':cookie,'Content-Type':'application/json'})
                response = connection.getresponse(); assert response.status == 403; response.read()
                connection.request('GET','/')
                response = connection.getresponse(); assert response.status == 200
                assert b'Intent-Slide' in response.read()
            finally:
                connection.close()
        finally:
            owner.close()
            reader.join(timeout=2); drain.join(timeout=2)
    print(json.dumps({'status':'VERIFIED','platform':portable_bootstrap.platform_id(),
        'bundled_python':True,'bundled_codex':True,'real_pptx_render':True,'local_http_boundary':True,
        'model_generation':'UNVERIFIED (no account used by smoke)'},ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
