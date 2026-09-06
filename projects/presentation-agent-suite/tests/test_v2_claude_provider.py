"""Official CLI stream/control shapes exercised at a real bounded pipe boundary.

These are synthetic protocol fixtures, not proof of a locally installed Claude
binary, account entitlement, native sandbox enforcement, or image tool support.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from presentation_agents.v2.claude_provider import ClaudeProvider
from presentation_agents.v2.provider import ProviderError

PEER = r'''
import base64,json,os,sys,time
mode=sys.argv[1]
def emit(x): print(json.dumps(x),flush=True)
if '--version' in sys.argv:
 print('2.1.247 (Claude Code)' if mode=='old' else '2.1.260 (Claude Code)');sys.exit()
if 'auth' in sys.argv:
 if mode=='auth_limit': print('x'*70000);sys.exit()
 emit({'loggedIn':mode!='logout','authMethod':'api_key' if mode=='apikey' else 'claude.ai',
       'apiProvider':'firstParty','subscriptionType':None if mode=='unknown' else 'pro',
       'email':'PRIVATE_EMAIL','organizationUuid':'PRIVATE_ORG','unusedSecret':'PRIVATE_SECRET'})
 sys.exit(1 if mode=='logout' else 0)
assert '--restricted' in sys.argv and '--bare' not in sys.argv
assert '--dangerously-skip-permissions' not in sys.argv and '--add-dir' not in sys.argv
assert sys.argv[sys.argv.index('--permission-prompt-tool')+1]=='stdio'
assert sys.argv[sys.argv.index('--permission-mode')+1]=='default'
settings=json.loads(sys.argv[sys.argv.index('--settings')+1])
assert settings['sandbox']=={'enabled':True,'failIfUnavailable':True,'autoAllowBashIfSandboxed':False,'allowUnsandboxedCommands':False}
assert settings['switchModelsOnFlag'] is False
sid=next(x.split('=',1)[1] for x in sys.argv if x.startswith(('--resume=','--session-id=')))
model=sys.argv[sys.argv.index('--model')+1]
turns=0
png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6AAAAAElFTkSuQmCC'
def assistant(block): emit({'type':'assistant','session_id':sid,'message':{'role':'assistant','content':[block]}})
def result(): emit({'type':'result','session_id':sid,'subtype':'success','is_error':False,'result':'complete'})
def request(name,value,tid='tool-1'):
 assistant({'type':'tool_use','id':tid,'name':name,'input':value})
 emit({'type':'control_request','request_id':0,'request':{'subtype':'can_use_tool','tool_use_id':tid,'tool_name':name,'input':value}})
for line in sys.stdin:
 x=json.loads(line)
 if x['type']=='control_request':
  if x['request']['subtype']=='initialize':
   assert x['request']=={'subtype':'initialize','hooks':None}
   if mode=='bad_json': print('invalid-json',flush=True);continue
   if mode=='large': print('x'*5000,flush=True);continue
   if mode=='hang_init': time.sleep(5);continue
  emit({'type':'control_response','response':{'subtype':'success','request_id':x['request_id'],'response':{'current_permission_mode':'default','models':[{'value':n,'resolvedModel':'claude-'+n+'-5','supportsEffort':True,'supportedEffortLevels':['low'] if mode=='effort_mismatch' else ['low','medium','high','max']} for n in ('sonnet','opus','haiku')]}}})
  if x['request']['subtype']=='interrupt': result()
 elif x['type']=='user':
  turns+=1
  assert x['session_id']==sid
  emit({'type':'system','subtype':'init','session_id':sid,'model':'claude-'+('opus' if mode=='model_mismatch' else model)+'-5',
        'cwd':os.getcwd(),'permissionMode':'bypassPermissions' if mode=='bad_policy' else 'default','mcp_servers':[],'apiKeySource':'none'})
  if mode=='turn_hang':continue
  if mode=='stderr':sys.stderr.write('private'*10000);sys.stderr.flush()
  if mode=='wrong_session': emit({'type':'assistant','session_id':'other','message':{'content':[]}});continue
  if mode in ('approve','cancel','decline','auto'):
   request('Bash',{'command':'pwd'});continue
  if mode=='outside':
   request('Write',{'file_path':'../service.db','content':'x'});continue
  if mode=='write':
   request('Write',{'file_path':'result.json','content':'{"ok":true}'});continue
  if mode=='question':
   request('AskUserQuestion',{'questions':[{'header':'Audience','question':'Who reads this?','options':[{'label':'Board','description':'Executive review'}],'multiSelect':False}]});continue
  if mode in ('image','failed_image','text_image','unmatched_image','bad_image'):
   if mode!='unmatched_image': assistant({'type':'tool_use','id':'read-1','name':'Read','input':{'file_path':'page.png'}})
   content=[{'type':'image','source':{'type':'base64','media_type':'image/png','data':'eA==' if mode=='bad_image' else png}}]
   if mode=='text_image':content=[{'type':'text','text':'I viewed page.png'}]
   block={'type':'tool_result','tool_use_id':'read-1','content':content,'is_error':mode=='failed_image'}
   emit({'type':'user','session_id':sid,'message':{'role':'user','content':[block]}})
   emit({'type':'user','session_id':sid,'message':{'role':'user','content':[block]}})
  emit({'type':'stream_event','session_id':sid,'event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'hello'}}})
  assistant({'type':'text','text':'Finished'})
  result()
 elif x['type']=='control_response':
  assert x['response']['request_id']==0
  payload=x['response']['response']
  assert 'updatedPermissions' not in payload
  if mode=='question': assert payload['updatedInput']['answers']=={'Who reads this?':'Board'}
  elif mode in ('decline','outside'): assert payload['behavior']=='deny'
  elif mode=='write':assert payload['updatedInput']=={'file_path':'result.json','content':'{"ok":true}'}
  else: assert payload=={'behavior':'allow','updatedInput':{'command':'pwd'}}
  result()
'''

class ClaudeProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cwd=Path(self.temp.name).resolve()
        self.providers=[]
    def tearDown(self):
        for provider in self.providers: provider.close()
        self.temp.cleanup()
    def provider(self,mode='normal',**kwargs):
        provider=ClaudeProvider(command=[sys.executable,'-u','-c',PEER,mode],request_timeout=.8,turn_timeout=.5,**kwargs)
        self.providers.append(provider)
        return provider
    def wait_for(self,events,method):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            found=next((x for x in events if x['method']==method),None)
            if found:return found
            time.sleep(.01)
        self.fail(f'Missing {method}: {events}')
    def test_native_metadata_is_sanitized_and_subscription_only(self):
        caps=self.provider().preflight()
        self.assertTrue(caps['ready'])
        self.assertEqual(caps['auth_mode'],'subscription')
        self.assertNotIn('PRIVATE',json.dumps(caps))
        self.assertEqual(caps['runtime']['integration_status'],'UNVERIFIED')
        for mode,auth in [('apikey','api_key'),('unknown','unknown'),('logout','none')]:
            with self.subTest(mode=mode):
                p=self.provider(mode)
                self.assertEqual(p.preflight()['auth_mode'],auth)
                with self.assertRaises(ProviderError):p.start_turn(self.cwd,'prompt',lambda e:None)
    def test_missing_old_and_oversize_metadata_fail_closed(self):
        with patch('shutil.which',return_value=None):
            self.assertEqual(self.provider().preflight()['error_code'],'UNAVAILABLE')
        self.assertEqual(self.provider('old').preflight()['error_code'],'VERSION_UNSUPPORTED')
        self.assertEqual(self.provider('auth_limit').preflight()['error_code'],'OUTPUT_LIMIT')
    def test_id_zero_approval_roundtrip_and_no_permission_expansion(self):
        p=self.provider('approve');events=[]
        ack=p.start_turn(self.cwd,'prompt',events.append,model='sonnet',effort='high')
        request=self.wait_for(events,'item/commandExecution/requestApproval')
        self.assertEqual(request['id'],0)
        self.assertNotIn('_claude_input',request)
        self.assertEqual(ack['model'],'claude-sonnet-5')
        with self.assertRaises(ValueError):p.respond(0,{'decision':'acceptForSession'})
        with self.assertRaises(ValueError):p.respond(True,{'decision':'accept'})
        p.respond(0,{'decision':'accept'})
        self.assertEqual(self.wait_for(events,'turn/completed')['params']['turn']['status'],'completed')
        with self.assertRaises(ProviderError):p.respond(0,{'decision':'accept'})
    def test_callback_can_respond_without_deadlock(self):
        p=self.provider('auto');events=[]
        def event(x):
            events.append(x)
            if x.get('id')==0:p.respond(0,{'decision':'accept'})
        p.start_turn(self.cwd,'prompt',event)
        self.wait_for(events,'turn/completed')
    def test_questions_and_write_metadata_preserve_inputs(self):
        for mode,method in [('question','item/tool/requestUserInput'),('write','item/fileChange/requestApproval')]:
            with self.subTest(mode=mode):
                p=self.provider(mode);events=[]
                p.start_turn(self.cwd,'prompt',events.append)
                request=self.wait_for(events,method)
                if mode=='question':
                    self.assertEqual(request['params']['questions'][0]['id'],'q1')
                    with self.assertRaises(ValueError):p.respond(0,{'answers':{}})
                    p.respond(0,{'answers':{'q1':{'answers':['Board']}}})
                else:
                    item=next(x['params']['item'] for x in events if x['method']=='item/started')
                    self.assertEqual(item['changes'][0]['path'],'result.json')
                    p.respond(0,{'decision':'accept'})
                self.wait_for(events,'turn/completed')
    def test_outside_write_denied_without_asking_for_expansion(self):
        events=[]
        self.provider('outside').start_turn(self.cwd,'prompt',events.append)
        self.wait_for(events,'turn/completed')
        self.assertFalse(any('requestApproval' in x['method'] for x in events))
    def test_image_evidence_requires_successful_matching_actual_payload(self):
        for mode in ('image','failed_image','text_image','unmatched_image','bad_image'):
            with self.subTest(mode=mode):
                events=[]
                self.provider(mode).start_turn(self.cwd,'prompt',events.append)
                self.wait_for(events,'turn/completed')
                images=[x['params']['item'] for x in events if x['method']=='item/completed' and x['params']['item']['type']=='imageView']
                self.assertEqual(len(images),1 if mode=='image' else 0)
                if images:self.assertEqual(images[0]['path'],str(self.cwd/'page.png'))
    def test_cancel_ack_is_followed_by_interrupted_result(self):
        p=self.provider('cancel');events=[]
        ack=p.start_turn(self.cwd,'prompt',events.append)
        self.wait_for(events,'item/commandExecution/requestApproval')
        p.cancel(ack['thread_id'],ack['turn_id'])
        self.assertEqual(self.wait_for(events,'turn/completed')['params']['turn']['status'],'interrupted')
        with self.assertRaises(ProviderError):p.respond(0,{'decision':'accept'})
    def test_session_resume_and_selection_binding(self):
        p=self.provider();events=[]
        first=p.start_turn(self.cwd,'first',events.append)
        self.wait_for(events,'turn/completed');events.clear()
        second=p.start_turn(self.cwd,'second',events.append,thread_id=first['thread_id'])
        self.assertEqual(first['thread_id'],second['thread_id'])
        self.assertNotEqual(first['turn_id'],second['turn_id'])
        self.wait_for(events,'turn/completed')
        with self.assertRaises(ProviderError):p.start_turn(self.cwd,'bad',events.append,thread_id=first['thread_id'],model='opus')
        with self.assertRaises(ValueError):p.start_turn(self.cwd,'bad',events.append,thread_id='--bare')
        q=self.provider();new_events=[]
        self.assertEqual(q.start_turn(self.cwd,'resume',new_events.append,thread_id=first['thread_id'])['thread_id'],first['thread_id'])
        self.wait_for(new_events,'turn/completed')
    def test_native_effort_catalog_is_checked_before_prompt(self):
        p=self.provider('effort_mismatch')
        with self.assertRaises(ProviderError) as raised:
            p.start_turn(self.cwd,'must not run',lambda e:None,effort='high')
        self.assertEqual(raised.exception.code,'EFFORT_UNAVAILABLE')

    def test_protocol_init_policy_and_output_failures_are_bounded(self):
        for mode,code in [('bad_json','PROTOCOL_ERROR'),('large','OUTPUT_LIMIT'),('hang_init','REQUEST_TIMEOUT'),('bad_policy','POLICY_MISMATCH'),('model_mismatch','MODEL_MISMATCH')]:
            with self.subTest(mode=mode):
                p=self.provider(mode,max_frame_bytes=4096)
                with self.assertRaises(ProviderError) as raised:p.start_turn(self.cwd,'prompt',lambda e:None)
                self.assertEqual(raised.exception.code,code)
    def test_wrong_session_and_turn_timeout_fail_as_events(self):
        for mode,code in [('wrong_session','SESSION_MISMATCH'),('turn_hang','TURN_TIMEOUT')]:
            with self.subTest(mode=mode):
                p=self.provider(mode);events=[]
                try:p.start_turn(self.cwd,'prompt',events.append)
                except ProviderError as exc:self.assertEqual(exc.code,code)
                self.assertEqual(self.wait_for(events,'provider/error')['params']['code'],code)
    def test_stderr_is_bounded_and_never_forwarded(self):
        p=self.provider('stderr',max_stderr_bytes=256);events=[]
        p.start_turn(self.cwd,'prompt',events.append)
        self.wait_for(events,'turn/completed')
        self.assertLessEqual(len(p._stderr_tail),256)
        self.assertNotIn('private',json.dumps(events))
