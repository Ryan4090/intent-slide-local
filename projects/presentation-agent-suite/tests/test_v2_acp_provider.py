"""Real JSON-RPC pipes with a synthetic ACP peer; no model or account access."""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE / 'src'))
from presentation_agents.v2.acp_provider import ACPProvider
from presentation_agents.v2.provider import ProviderError

PEER = r'''
import base64,json,pathlib,sys
mode=sys.argv[1]; sessions={}; count=0; waiting=None; pending=None; model='fixture/a'
def send(obj):
    print(json.dumps({'jsonrpc':'2.0',**obj}),flush=True)
def reply(req,result): send({'id':req['id'],'result':result})
def config():
    return [{'id':'choose-model','category':'model','type':'select','name':'Model','currentValue':model,
             'options':[{'value':'fixture/a','name':'A'},{'value':'fixture/b','name':'B'}]},
            {'id':'thought','category':'thought_level','type':'select','name':'Effort','currentValue':'high',
             'options':[{'value':'high','name':'High'},{'value':'low','name':'Low'}]}]
def update(s,u):send({'method':'session/update','params':{'sessionId':s,'update':u}})
def complete(req,reason='end_turn'):
    update(req['params']['sessionId'],{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'완료'}})
    reply(req,{'stopReason':reason})
def image(req):
    s=req['params']['sessionId']; p=pathlib.Path(sessions[s])/'page.png'
    raw=p.read_bytes()
    tool={'sessionUpdate':'tool_call','toolCallId':'img-1','kind':'read','title':'Read page',
          'status':'in_progress','rawInput':{'file_path':str(p)},'locations':[{'path':str(p)}]}
    if mode=='outside_image':tool['rawInput']['file_path']=str(p.parent.parent/'outside.png');tool['locations']=[]
    if mode=='symlink_image':tool['rawInput']['file_path']=str(p.parent/'linked.png');tool['locations']=[]
    if mode=='image_partial':tool.pop('rawInput');tool.pop('locations')
    update(s,tool)
    if mode=='image_partial':update(s,{'sessionUpdate':'tool_call_update','toolCallId':'img-1','status':'in_progress','rawInput':{'file_path':str(p)}})
    content={'type':'content','content':{'type':'image','mimeType':'image/png','data':base64.b64encode(raw).decode()}}
    if mode=='wrong_image':content['content']['data']=base64.b64encode(raw+b'wrong').decode()
    if mode=='fake_image':content={'type':'content','content':{'type':'text','text':'I viewed page.png'}}
    u={'sessionUpdate':'tool_call_update','toolCallId':'img-1','status':'failed' if mode=='failed_image' else 'completed','content':[content]}
    if mode=='image_retarget':u['rawInput']={'file_path':str(p.parent.parent/'outside.png')}
    update(s,u);update(s,u)
    complete(req)
for line in sys.stdin:
    req=json.loads(line); method=req.get('method'); p=req.get('params',{})
    assert req.get('jsonrpc')=='2.0'
    if method=='initialize':
        assert p['protocolVersion']==1
        assert p['clientCapabilities']['fs']=={'readTextFile':True,'writeTextFile':False}
        assert p['clientCapabilities']['terminal'] is False
        reply(req,{'protocolVersion':2 if mode=='bad_version' else 1,
                   'agentCapabilities':{'loadSession':mode!='no_resume','promptCapabilities':{'image':True}},
                   'agentInfo':{'name':'fixture','version':'1.0'},'authMethods':[{'id':'native','name':'Existing login'}]})
    elif method in ('session/new','session/load'):
        assert p['mcpServers']==[]
        if mode=='auth_required':
            send({'id':req['id'],'error':{'code':-32000,'message':'private account information'}});continue
        count+=1;s=p.get('sessionId','session-'+str(count));sessions[s]=p['cwd']
        result={'sessionId':s,'configOptions':config()}
        if mode=='no_model':result.pop('configOptions')
        if mode.startswith('legacy_model'):result={'sessionId':s,'models':{'currentModelId':'fixture/a','availableModels':[{'modelId':'fixture/a','name':'A'},{'modelId':'fixture/b','name':'B'}]}}
        if mode=='wrong_resume' and method=='session/load':result['sessionId']='wrong-session'
        reply(req,result)
    elif method=='session/set_config_option':
        assert p['sessionId'] in sessions
        if p['configId']=='choose-model':model=p['value'] if mode!='model_not_acknowledged' else 'fixture/a'
        opts=config()
        if p['configId']=='thought':opts[1]['currentValue']=p['value']
        reply(req,{'configOptions':opts})
    elif method=='session/set_model':
        assert p['modelId']=='fixture/b'
        if mode=='legacy_model_reject':send({'id':req['id'],'error':{'code':-32601,'message':'unsupported'}})
        else:model=p['modelId'];reply(req,{})
    elif method=='session/prompt':
        s=p['sessionId']; assert p['prompt'][0]['type']=='text'
        pathlib.Path(sessions[s],'prompt-received.txt').write_text(p['prompt'][0]['text'])
        if mode=='prompt_auth':send({'id':req['id'],'error':{'code':-32000,'message':'private credential detail'}})
        elif mode in ('normal','auto_reply','cancel','hang_cancel','permission_only'):
            pending=req
            if mode!='permission_only':
                update(s,{'sessionUpdate':'tool_call','toolCallId':'cmd-1','kind':'execute','title':'Write result',
                          'status':'pending','rawInput':{'command':'write stage_result.json'}})
            send({'id':0,'method':'session/request_permission','params':{'sessionId':s,
                  'toolCall':{'toolCallId':'cmd-1','kind':'execute','title':'Write result'},
                  'options':[{'optionId':'once-1','kind':'allow_once','name':'Allow once'},
                             {'optionId':'always-1','kind':'allow_always','name':'Always'},
                             {'optionId':'no-1','kind':'reject_once','name':'Reject'}]}})
        elif mode.startswith(('image','outside_image','symlink_image','fake_image','wrong_image','failed_image')):
            image(req)
        elif mode in ('fs_read','fs_outside','fs_write','terminal'):
            pending=req
            target=str(pathlib.Path(sessions[s])/'input.txt')
            if mode=='fs_outside':target=str(pathlib.Path(sessions[s]).parent/'outside.txt')
            meth='fs/write_text_file' if mode=='fs_write' else 'terminal/create' if mode=='terminal' else 'fs/read_text_file'
            send({'id':'fs-1','method':meth,'params':{'sessionId':s,'path':target,'content':'unsafe write'}})
        elif mode=='turn_hang':pending=req
        else:complete(req,'max_tokens' if mode=='max_tokens' else 'end_turn')
    elif method=='session/cancel':
        if mode!='hang_cancel' and pending:complete(pending,'cancelled');pending=None
    elif method is None:
        if req['id']==0:
            outcome=req['result']['outcome']
            if outcome['outcome']=='selected':
                assert outcome['optionId'] in ('once-1','no-1')
                if outcome['optionId']=='once-1':
                    pathlib.Path(sessions[pending['params']['sessionId']],'stage_result.json').write_text('{"kind":"result","data":{"ok":true}}')
                if mode=='permission_only':update(pending['params']['sessionId'],{'sessionUpdate':'tool_call_update','toolCallId':'cmd-1','status':'completed'})
                complete(pending);pending=None
            else:assert outcome['outcome']=='cancelled'
        elif req['id']=='fs-1':
            if mode=='fs_read':assert req['result']['content']=='allowed input'
            else:assert 'error' in req
            complete(pending);pending=None
        else:raise AssertionError('unexpected client response')
    else:raise AssertionError('unexpected method '+str(method))
'''


class ACPProviderTests(unittest.TestCase):
    def setUp(self):
        (SUITE/'.runtime').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='acp-test-',dir=SUITE/'.runtime')
        self.root=Path(self.temp.name)
        self.cwd=self.root/'attempt';self.cwd.mkdir()
        self.providers=[]
        from PIL import Image
        Image.new('RGB',(2,2),(10,120,190)).save(self.cwd/'page.png')
        (self.cwd/'input.txt').write_text('allowed input')
        (self.root/'outside.txt').write_text('private outside fixture')
        (self.root/'outside.png').write_bytes((self.cwd/'page.png').read_bytes())

    def tearDown(self):
        for provider in self.providers:
            provider.close()
            if provider._process:self.assertIsNotNone(provider._process.poll())
        self.temp.cleanup()

    def provider(self,mode='normal',**kwargs):
        provider=ACPProvider(provider_id='gemini',command=[sys.executable,'-u','-c',PEER,mode],
                             probe_directory=self.root,request_timeout=1,turn_timeout=2,**kwargs)
        self.providers.append(provider)
        return provider

    def wait(self,events,method,timeout=4):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            for event in list(events):
                if event.get('method')==method:return event
            time.sleep(.01)
        self.fail(f'Missing {method}: {[x.get("method") for x in events]}')

    def test_preflight_protocol_only_never_prompts_or_reads_credentials(self):
        p=self.provider();caps=p.preflight()
        self.assertTrue(caps['ready']);self.assertTrue(caps['protocol_ready'])
        self.assertFalse(caps['auto_connect']);self.assertEqual(caps['auth_mode'],'native_unverified')
        self.assertEqual(caps['default_model'],'fixture/a')
        self.assertFalse(list(self.root.rglob('prompt-received.txt')))
        self.assertNotIn('private',json.dumps(caps))

    def test_preflight_auth_failure_and_unknown_model_fail_closed(self):
        for mode,code in [('auth_required','AUTH_REQUIRED'),('no_model','MODEL_UNAVAILABLE'),('bad_version','PROTOCOL_VERSION')]:
            with self.subTest(mode=mode):
                caps=self.provider(mode).preflight();self.assertFalse(caps['ready']);self.assertEqual(caps['error_code'],code)
                self.assertNotIn('private',json.dumps(caps))

    def test_model_effort_ack_and_real_workspace_output(self):
        p=self.provider();events=[]
        ack=p.start_turn(self.cwd,'write fixture',events.append,model='fixture/b',effort='low')
        self.assertEqual((ack['model'],ack['effort']),('fixture/b','low'))
        req=self.wait(events,'item/commandExecution/requestApproval');self.assertEqual(req['id'],0)
        with self.assertRaises(ValueError):p.respond(0,{'decision':'acceptForSession'})
        p.respond(0,{'decision':'accept'})
        self.assertEqual(self.wait(events,'turn/completed')['params']['turn']['status'],'completed')
        self.assertTrue(json.loads((self.cwd/'stage_result.json').read_text())['data']['ok'])
        self.wait(events,'serverRequest/resolved')
        with self.assertRaises(ProviderError):p.respond(0,{'decision':'accept'})

    def test_unknown_model_effort_and_unacknowledged_change_never_prompt(self):
        for mode,model,effort in [('normal','unknown',None),('normal',None,'ultra'),('model_not_acknowledged','fixture/b',None)]:
            with self.subTest(mode=mode,model=model,effort=effort):
                with self.assertRaises(ProviderError):self.provider(mode).start_turn(self.cwd,'do not call model',lambda e:None,model=model,effort=effort)
                self.assertFalse((self.cwd/'prompt-received.txt').exists())

    def test_legacy_model_selection_requires_catalog_and_successful_native_ack(self):
        p=self.provider('legacy_model');events=[]
        ack=p.start_turn(self.cwd,'native legacy selection',events.append,model='fixture/b')
        self.assertEqual(ack['model'],'fixture/b');self.wait(events,'turn/completed')
        (self.cwd/'prompt-received.txt').unlink()
        with self.assertRaises(ProviderError):self.provider('legacy_model_reject').start_turn(self.cwd,'must not prompt',lambda e:None,model='fixture/b')
        self.assertFalse((self.cwd/'prompt-received.txt').exists())

    def test_permission_can_introduce_tool_without_prior_update(self):
        p=self.provider('permission_only');events=[]
        p.start_turn(self.cwd,'native permission introduction',events.append)
        self.wait(events,'item/commandExecution/requestApproval');p.respond(0,{'decision':'accept'})
        self.wait(events,'turn/completed')
        completed=[e['params']['item'] for e in events if e.get('method')=='item/completed']
        self.assertTrue(any(item['id']=='cmd-1' and item['status']=='completed' for item in completed))

    def test_callback_can_answer_without_reader_deadlock(self):
        p=self.provider();events=[]
        def callback(e):
            events.append(e)
            if e['method']=='item/commandExecution/requestApproval':p.respond(e['id'],{'decision':'decline'})
        p.start_turn(self.cwd,'decline fixture',callback)
        self.wait(events,'turn/completed');self.assertFalse((self.cwd/'stage_result.json').exists())

    def test_actual_image_once_requires_completed_content_and_workspace_path(self):
        for mode,expected in [('image',1),('failed_image',0),('fake_image',0),('wrong_image',0),('outside_image',0),('symlink_image',0)]:
            with self.subTest(mode=mode):
                link=self.cwd/'linked.png'
                if not link.exists():link.symlink_to(self.root/'outside.png')
                events=[];self.provider(mode).start_turn(self.cwd,'look at image',events.append)
                self.wait(events,'turn/completed')
                views=[e['params']['item'] for e in events if e.get('method')=='item/completed' and e['params']['item']['type']=='imageView']
                self.assertEqual(len(views),expected)
                if views:
                    import hashlib
                    self.assertEqual(views[0]['sha256'],hashlib.sha256((self.cwd/'page.png').read_bytes()).hexdigest())

    def test_client_file_proxy_read_is_scoped_write_and_terminal_are_denied(self):
        for mode in ('fs_read','fs_outside','fs_write','terminal'):
            with self.subTest(mode=mode):
                events=[];self.provider(mode).start_turn(self.cwd,'file proxy fixture',events.append)
                self.wait(events,'turn/completed')
                self.assertEqual((self.cwd/'input.txt').read_text(),'allowed input')
                self.assertEqual((self.root/'outside.txt').read_text(),'private outside fixture')

    def test_partial_read_input_is_frozen_before_completion_and_retarget_is_rejected(self):
        for mode,expected in [('image_partial',1),('image_retarget',0)]:
            with self.subTest(mode=mode):
                events=[];self.provider(mode).start_turn(self.cwd,'image fixture',events.append)
                self.wait(events,'turn/completed')
                self.assertEqual(sum(e.get('method')=='item/completed' and e['params']['item']['type']=='imageView' for e in events),expected)

    def test_prompt_auth_error_is_immediate_redacted_failure(self):
        events=[];p=self.provider('prompt_auth');p.start_turn(self.cwd,'auth fixture',events.append)
        error=self.wait(events,'provider/error',timeout=1)
        self.assertEqual(error['params']['code'],'AUTH_REQUIRED')
        self.assertNotIn('private',json.dumps(events))

    def test_changed_or_deleted_image_after_tool_start_is_not_observation(self):
        p=self.provider();p._session_id='fixture';p._session_cwds['fixture']=self.cwd
        events=[];p._emit=lambda method,params,**kwargs:events.append({'method':method,'params':params})
        original=(self.cwd/'page.png').read_bytes()
        for action in ('changed','deleted'):
            with self.subTest(action=action):
                (self.cwd/'page.png').write_bytes(original)
                tool={'rawInput':{'path':'page.png'}};p._capture_tool_file(tool)
                if action=='changed':(self.cwd/'page.png').write_bytes(original+b'changed')
                else:(self.cwd/'page.png').unlink()
                tool['content']=[{'type':'content','content':{'type':'image','mimeType':'image/png','data':base64.b64encode(original).decode()}}]
                p._image_observation(action,tool)
                self.assertEqual(events,[])

    @unittest.skipUnless(__import__('os').open in __import__('os').supports_dir_fd, 'Native dir_fd protection is POSIX only')
    def test_directory_symlink_swap_cannot_read_outside_proxy(self):
        import os
        p=self.provider();p._session_cwds['fixture']=self.cwd
        assets=self.cwd/'assets';assets.mkdir();(assets/'secret.txt').write_text('allowed')
        outside=self.root/'outside-dir';outside.mkdir();(outside/'secret.txt').write_text('private fixture')
        original=os.open;swapped=False
        def swap(path,flags,*args,**kwargs):
            nonlocal swapped
            if path=='assets' and kwargs.get('dir_fd') is not None and not swapped:
                swapped=True;assets.rename(self.cwd/'old-assets');assets.symlink_to(outside,target_is_directory=True)
            return original(path,flags,*args,**kwargs)
        with patch('os.open',side_effect=swap) as opened, patch('os.supports_dir_fd',os.supports_dir_fd|{opened}):
            with self.assertRaises((OSError,ValueError)):p._safe_bytes('fixture','assets/secret.txt')
        self.assertTrue(swapped)

    def test_resume_checks_capability_and_session_identity(self):
        for mode in ('no_resume','wrong_resume'):
            with self.subTest(mode=mode):
                with self.assertRaises(ProviderError):self.provider(mode).start_turn(self.cwd,'resume',lambda e:None,thread_id='known-session')
        p=self.provider('legacy_model');events=[]
        ack=p.start_turn(self.cwd,'resume',events.append,thread_id='known-session')
        self.assertEqual(ack['thread_id'],'known-session');self.wait(events,'turn/completed')

    def test_cancel_resolves_permission_and_waits_for_native_cancelled(self):
        p=self.provider('cancel');events=[];ack=p.start_turn(self.cwd,'cancel',events.append)
        self.wait(events,'item/commandExecution/requestApproval')
        p.cancel(ack['thread_id'],ack['turn_id'])
        final=self.wait(events,'turn/completed')
        self.assertEqual(final['params']['turn']['status'],'interrupted')
        self.assertFalse((self.cwd/'stage_result.json').exists())
        with self.assertRaises(ProviderError):p.respond(0,{'decision':'accept'})

    def test_unresponsive_cancel_kills_owned_process(self):
        p=self.provider('hang_cancel');events=[];ack=p.start_turn(self.cwd,'cancel',events.append)
        self.wait(events,'item/commandExecution/requestApproval');p.cancel(ack['thread_id'],ack['turn_id'])
        self.wait(events,'provider/error');self.assertIsNotNone(p._process.poll())

    def test_refusal_limits_do_not_report_success(self):
        events=[];self.provider('max_tokens').start_turn(self.cwd,'limited',events.append)
        self.assertEqual(self.wait(events,'turn/completed')['params']['turn']['status'],'failed')

    def test_registry_profiles_and_timeout_are_bounded(self):
        self.assertEqual(ACPProvider('gemini').command,['gemini','--acp'])
        self.assertEqual(ACPProvider(provider_id='opencode').command,['opencode','acp'])
        for args in ({'provider_id':'arbitrary shell'},{'request_timeout':61}):
            with self.assertRaises(ValueError):ACPProvider(**args)

if __name__=='__main__':unittest.main()
