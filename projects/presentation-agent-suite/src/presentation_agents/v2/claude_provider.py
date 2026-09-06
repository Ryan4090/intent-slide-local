"""Claude Code's unmodified local CLI, with native subscription authentication.

Protocol sources (retrieved 2026-09-06):
https://code.claude.com/docs/en/cli-reference
https://code.claude.com/docs/en/headless
https://code.claude.com/docs/en/sandboxing
https://code.claude.com/docs/en/model-config
https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py

No SDK dependency, token access, bare mode, API fallback, persistent permission
rules, or global settings writes. CodexProvider's bounded process/queue machinery
is shared; Claude wire frames are translated here, never passed off as Codex RPC.
A fake peer verifies framing. Real CLI availability/auth/tool execution remain a
separate integration check, explicitly reported in preflight metadata.
"""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any
import uuid

from .provider import CodexProvider, EventCallback, ProviderError
from .provider_registry import CLAUDE_MODELS, EFFORTS, normalize_selection
from .stdio_transport import TransportError, probe_command

_MIN_VERSION = (2, 1, 248)  # --restricted is the file-tool isolation boundary.
_TOOLS = ('Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebSearch', 'WebFetch', 'AskUserQuestion')
_SUBSCRIPTIONS = {'pro', 'max', 'team', 'enterprise'}
def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment['DISABLE_AUTOUPDATER'] = '1'
    return environment


_SETTINGS = {
    'sandbox': {'enabled': True, 'failIfUnavailable': True,
                'autoAllowBashIfSandboxed': False, 'allowUnsandboxedCommands': False},
    'permissions': {'defaultMode': 'default', 'additionalDirectories': []},
    'switchModelsOnFlag': False,
    'ultracode': False,
}


def _probe(command: list[str], timeout: float, limit: int = 65536) -> tuple[int, bytes]:
    """Read public CLI metadata with a deadline and bounded combined output."""
    try:
        return probe_command(command, timeout=timeout, limit=limit, env=_child_environment())
    except TransportError as exc:
        raise ProviderError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise ProviderError('UNAVAILABLE', 'Claude Code executable is not available') from exc


class ClaudeProvider(CodexProvider):
    """One session per owned CLI process, normalized to the common Runner events."""
    def __init__(self, *, command: list[str] | None = None, **bounds: Any) -> None:
        super().__init__(command=command or ['claude'], **bounds)
        self._base_command = self.command[:]
        self._session_id: str | None = None
        self._turn_id: str | None = None
        self._cwd: Path | None = None
        self._effective_model: str | None = None
        self._selected_model: str | None = None
        self._selected_effort: str | None = None
        self._session_ready = threading.Event()
        self._tool_uses: dict[str, dict[str, Any]] = {}
        self._tool_done: set[str] = set()
        self._native_requests: dict[int | str, dict[str, Any]] = {}
        self._cancel_requested = False
        self._caps: dict[str, Any] = {}

    def preflight(self) -> dict[str, Any]:
        """Only native public metadata is inspected; all identity fields discarded."""
        caps: dict[str, Any] = {
            'provider':'claude', 'ready':False, 'authenticated':False, 'auth_mode':'none',
            'auth_type':None, 'reason':None, 'status':'BLOCKED', 'models':[],
            'default_model':'sonnet', 'effective_model_source':'Explicit safe alias; system/init actual model',
            'features': {'streaming':True, 'approvals':True, 'user_input':True, 'image_view':True,
                         'resume':True, 'cancel':True, 'subscription_only':True,
                         'workspace_isolation':'restricted file tools + required native Bash sandbox'},
            'runtime': {'status':'UNVERIFIED', 'minimum_version':'.'.join(map(str, _MIN_VERSION)),
                        'integration_status':'UNVERIFIED', 'reason':'Native auth and tool execution must be tested on this host'},
            'rate_limits':None, 'rate_limits_status':'UNAVAILABLE',
        }
        try:
            if not shutil.which(self._base_command[0]):
                raise ProviderError('UNAVAILABLE', 'Install the official Claude Code CLI, then use claude auth login')
            code, raw = _probe(self._base_command + ['--version'], self.request_timeout)
            match = re.search(rb'(?<!\d)(\d+)\.(\d+)\.(\d+)', raw)
            if code or not match:
                raise ProviderError('VERSION_UNKNOWN', 'Claude Code did not report a recognizable version')
            version = tuple(map(int, match.groups()))
            caps['runtime']['version'] = '.'.join(map(str, version))
            if version < _MIN_VERSION:
                raise ProviderError('VERSION_UNSUPPORTED', 'Claude Code 2.1.248 or newer is required for restricted file tools')
            code, raw = _probe(self._base_command + ['auth', 'status'], self.request_timeout)
            try:
                account = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                raise ProviderError('AUTH_UNKNOWN', 'Native auth status is not recognized; log in through Claude Code') from exc
            if not isinstance(account, dict):
                raise ProviderError('AUTH_UNKNOWN', 'Native auth status is not an object')
            logged_in = account.get('loggedIn') is True
            method = account.get('authMethod')
            subscription = account.get('subscriptionType')
            api_provider = account.get('apiProvider')
            auth_mode = ('subscription' if logged_in and method == 'claude.ai'
                         and api_provider == 'firstParty' and isinstance(subscription,str) and subscription in _SUBSCRIPTIONS
                         else 'api_key' if isinstance(method,str) and method in {'api_key', 'apiKey', 'api-key'}
                         else 'unknown' if logged_in else 'none')
            caps.update(auth_mode=auth_mode, authenticated=logged_in,
                        auth_type=method if isinstance(method,str) and method in {'claude.ai','api_key','apiKey','api-key'} else None)
            if code or auth_mode != 'subscription':
                raise ProviderError('SUBSCRIPTION_REQUIRED', 'A recognized native Claude subscription login is required; API billing and unknown auth are blocked')
            # Documentation profiles are not an account entitlement claim.
            caps['models'] = [
                {'id':name, 'model':name, 'displayName':'Claude '+name.title(), 'isDefault':name=='sonnet',
                 'supportedReasoningEfforts':[{'reasoningEffort':x} for x in EFFORTS['claude']] if name != 'haiku' else [],
                 'defaultReasoningEffort':None, 'availability':'UNVERIFIED', 'source':'documented_alias'}
                for name in CLAUDE_MODELS
            ]
            caps.update(ready=True, status='PARTIALLY_VERIFIED')
            caps['runtime']['status'] = 'PARTIALLY_VERIFIED'
        except ProviderError as exc:
            caps.update(reason=str(exc), error_code=exc.code)
            caps['runtime']['status'] = 'BLOCKED'
        self._caps, self._ready, self._models = caps, caps['ready'], caps['models']
        return copy.deepcopy(caps)

    def start_turn(self, cwd: Path, prompt: str, on_event: EventCallback,
                   thread_id: str | None = None, model: str | None = None,
                   effort: str | None = None) -> dict[str, Any]:
        selected = normalize_selection({'provider':'claude', 'model':model, 'effort':effort})
        cwd = Path(cwd).resolve()
        if not cwd.is_dir() or not isinstance(prompt, str) or not prompt.strip() or not callable(on_event):
            raise ValueError('An existing attempt directory, prompt, and event callback are required')
        if thread_id is not None:
            try:
                if str(uuid.UUID(thread_id)) != thread_id:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('Claude session id must be a canonical UUID') from exc
        with self._start_lock:
            if self._closed:
                raise ProviderError('CLOSED', 'Claude provider is closed')
            if self._failure:
                raise self._failure
            if self._active:
                raise ProviderError('TURN_ACTIVE', 'Only one active Claude turn is supported')
            caps = self.preflight()  # Refresh public auth metadata before each turn.
            if not caps['ready']:
                raise ProviderError(caps.get('error_code','SUBSCRIPTION_REQUIRED'), caps['reason'])
            chosen_model = selected['model'] or 'sonnet'
            if self._process and (thread_id != self._session_id or cwd != self._cwd
                                  or chosen_model != self._selected_model or effort != self._selected_effort):
                raise ProviderError('SESSION_MISMATCH', 'Create a fresh provider to change session, workspace, model, or effort')
            fresh = self._process is None
            self._session_id = thread_id or str(uuid.uuid4())
            self._turn_id = str(uuid.uuid4())
            self._cwd, self._selected_model, self._selected_effort = cwd, chosen_model, effort
            self._cancel_requested = False
            self._tool_uses.clear()
            self._tool_done.clear()
            self._native_requests.clear()
            self._callbacks[self._session_id] = on_event
            self._starting_callback = on_event
            if fresh:
                self.command = self._base_command + [
                    '-p', '--input-format','stream-json','--output-format','stream-json','--verbose',
                    '--include-partial-messages', '--permission-prompt-tool','stdio',
                    '--permission-mode','default', '--restricted', '--strict-mcp-config',
                    '--mcp-config','{"mcpServers":{}}', '--tools',','.join(_TOOLS),
                    '--settings',json.dumps(_SETTINGS, separators=(',',':')),
                    '--model',chosen_model,
                    ('--resume=' if thread_id else '--session-id=') + self._session_id,
                ]
                if effort:
                    self.command.extend(['--effort',effort])
                try:
                    self._launch_process(self.command, cwd=cwd, env=_child_environment())
                except ProviderError:
                    self._active.clear()
                    raise
                for target in (self._read_stdout,self._read_stderr,self._dispatch,self._watchdog):
                    worker = threading.Thread(target=target, daemon=True, name=f'claude-{target.__name__}')
                    self._threads.append(worker)
                    worker.start()
                initialization = self._control({'subtype':'initialize','hooks':None})
                try:
                    self._validate_initialization(initialization)
                except ProviderError as exc:
                    self._fail(exc)
                    raise
                self._initialized = True
            self._active[(self._session_id,self._turn_id)] = time.monotonic()
            self._starting_callback = None
            self._send({'type':'user','session_id':self._session_id,
                        'message':{'role':'user','content':prompt}, 'parent_tool_use_id':None})
            if not self._session_ready.wait(self.request_timeout):
                error = ProviderError('REQUEST_TIMEOUT', 'Claude Code system/init did not acknowledge the session')
                self._fail(error)
                raise error
            if self._failure:
                raise self._failure
            return {'thread_id':self._session_id,'turn_id':self._turn_id,
                    'model':self._effective_model,'effort':effort}

    def _validate_initialization(self, response: dict[str, Any]) -> None:
        """Validate public native capabilities before submitting any user prompt.

        The 2.1.263 initialize-only probe returned models with value,
        resolvedModel, supportsEffort, and supportedEffortLevels. This is a
        capability catalog; the subsequent request can still fail entitlement.
        """
        if response.get('current_permission_mode') != 'default':
            raise ProviderError('POLICY_MISMATCH','Claude did not initialize in default permission mode')
        models = response.get('models')
        if not isinstance(models,list) or len(models) > 4096:
            raise ProviderError('INVALID_RESPONSE','Claude did not report a bounded model capability catalog')
        family = f'claude-{self._selected_model}-'
        matches = [entry for entry in models if isinstance(entry,dict) and (
            entry.get('value') == self._selected_model
            or (isinstance(entry.get('resolvedModel'),str) and entry['resolvedModel'].startswith(family)))]
        if not matches:
            raise ProviderError('MODEL_UNAVAILABLE','The selected Claude family is absent from the native model catalog')
        if self._selected_effort is not None and not any(
            entry.get('supportsEffort') is True
            and isinstance(entry.get('supportedEffortLevels'),list)
            and self._selected_effort in entry['supportedEffortLevels'] for entry in matches
        ):
            raise ProviderError('EFFORT_UNAVAILABLE','The native Claude catalog does not support the requested effort')

    def _control(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = f'intent-slide-{self._next_id}'
            waiter = {'event':threading.Event(), 'response':None}
            self._pending[request_id] = waiter
        try:
            self._send({'type':'control_request','request_id':request_id,'request':request})
            if not waiter['event'].wait(self.request_timeout):
                error = ProviderError('REQUEST_TIMEOUT', 'Claude Code control acknowledgement timed out')
                self._fail(error)
                raise error
            response = waiter['response']
            if isinstance(response, ProviderError):
                raise response
            if response.get('subtype') != 'success' or not isinstance(response.get('response',{}),dict):
                raise ProviderError('RPC_ERROR', 'Claude Code rejected the control request')
            return response.get('response',{})
        finally:
            with self._lock:
                self._pending.pop(request_id,None)

    def _emit(self, method: str, params: dict[str, Any], request_id: int | str | None = None) -> None:
        message = {'method':method, 'params':{'threadId':self._session_id,'turnId':self._turn_id,**params}}
        if request_id is not None:
            message['id'] = request_id
        # Reuse the bounded dispatcher and authoritative request lifecycle.
        super()._receive(message)

    def _receive(self, message: dict[str, Any]) -> None:
        try:
            self._receive_claude(message)
        except (ValueError, TypeError, KeyError, AttributeError, ProviderError) as exc:
            self._fail(exc if isinstance(exc,ProviderError) else ProviderError('PROTOCOL_ERROR','Claude Code emitted an invalid stream frame'))

    def _receive_claude(self, message: dict[str, Any]) -> None:
        kind = message.get('type')
        if not isinstance(kind,str):
            raise ValueError('missing stream frame type')
        if kind in {'assistant','user','result','stream_event'} and message.get('session_id') != self._session_id:
            raise ProviderError('SESSION_MISMATCH','Execution events must identify the current session')
        if message.get('session_id') not in (None, self._session_id):
            raise ProviderError('SESSION_MISMATCH','Claude event belongs to a different session')
        if kind == 'control_response':
            response = message.get('response')
            if (not isinstance(response,dict) or not isinstance(response.get('request_id'),(str,int))
                    or isinstance(response.get('request_id'),bool)):
                raise ValueError('invalid control response')
            with self._lock:
                waiter = self._pending.get(response['request_id'])
                if waiter:
                    waiter['response'] = response
                    waiter['event'].set()
            return
        if kind == 'control_request':
            self._permission(message)
            return
        if kind == 'control_cancel_request':
            request_id = message.get('request_id')
            with self._lock:
                self._requests.pop(request_id,None)
                self._native_requests.pop(request_id,None)
            self._emit('serverRequest/resolved', {'requestId':request_id})
            return
        session = message.get('session_id')
        if session is not None and session != self._session_id:
            raise ProviderError('SESSION_MISMATCH','Claude event belongs to a different session')
        if kind == 'system' and message.get('subtype') == 'init':
            actual = message.get('model')
            selected = self._selected_model
            if not isinstance(actual,str) or not (actual == selected or actual.startswith(f'claude-{selected}-')):
                raise ProviderError('MODEL_MISMATCH','Claude Code did not select the requested model family')
            if (message.get('permissionMode') != 'default'
                    or Path(message.get('cwd','')).resolve() != self._cwd
                    or message.get('mcp_servers',[]) != []
                    or message.get('apiKeySource') not in (None,'none')
                    or (self._selected_effort is not None and message.get('effortLevel',self._selected_effort) != self._selected_effort)):
                raise ProviderError('POLICY_MISMATCH','Claude Code did not acknowledge the requested workspace and permission policy')
            self._effective_model = actual
            self._session_ready.set()
            return
        if not self._session_ready.is_set():
            # Hooks/status before init are intentionally not persisted.
            if kind in {'assistant','user','result'}:
                raise ProviderError('PROTOCOL_ERROR','Claude emitted execution data before system/init')
            return
        if message.get('parent_tool_use_id') is not None:
            # Agent/Task are not exposed; delegated evidence cannot satisfy G5.
            return
        if kind == 'stream_event':
            event = message.get('event',{})
            delta = event.get('delta',{})
            if delta.get('type') == 'text_delta' and isinstance(delta.get('text'),str):
                self._emit('item/agentMessage/delta', {'delta':delta['text']})
        elif kind == 'assistant':
            body = message.get('message',{})
            actual_model = body.get('model')
            if actual_model is not None and actual_model != self._effective_model:
                raise ProviderError('MODEL_MISMATCH','Claude changed the active model during the turn')
            for block in body.get('content',[]):
                if not isinstance(block,dict):
                    raise ValueError('invalid content block')
                if block.get('type') == 'text' and isinstance(block.get('text'),str):
                    self._emit('item/completed', {'item':{'type':'agentMessage','id':str(message.get('uuid','message')),
                               'text':block['text'], 'status':'completed'}})
                elif block.get('type') == 'tool_use':
                    self._record_tool(block)
        elif kind == 'user':
            body = message.get('message',{})
            content = body.get('content',[])
            if isinstance(content,list):
                for block in content:
                    if isinstance(block,dict) and block.get('type') == 'tool_result':
                        self._complete_tool(block)
        elif kind == 'result':
            if not self._active:
                raise ProviderError('PROTOCOL_ERROR','Claude emitted a duplicate terminal result')
            if not isinstance(message.get('is_error'),bool):
                raise ValueError('terminal result must report a boolean is_error')
            failed = message['is_error'] or message.get('subtype') != 'success'
            status = 'interrupted' if self._cancel_requested else 'failed' if failed else 'completed'
            self._native_requests.clear()
            self._emit('turn/completed', {'turn':{'id':self._turn_id,'status':status,
                       **({'error':{'message':'Claude Code reported an execution error'}} if failed else {})}})

    def _record_tool(self, block: dict[str, Any]) -> None:
        tool_id, name, tool_input = block.get('id'), block.get('name'), block.get('input')
        if not isinstance(tool_id,str) or not isinstance(name,str) or not isinstance(tool_input,dict):
            raise ValueError('invalid tool use')
        if name not in _TOOLS:
            raise ProviderError('TOOL_UNSUPPORTED','Claude requested a tool outside the configured allowlist')
        if tool_id in self._tool_uses:
            if self._tool_uses[tool_id] != {'type':'tool_use','id':tool_id,'name':name,'input':tool_input}:
                raise ProviderError('PROTOCOL_ERROR','Claude reused a tool id with different input')
            return
        if len(self._tool_uses) >= 4096:
            raise ProviderError('OUTPUT_LIMIT','Claude tool ledger exceeded its bound')
        self._tool_uses[tool_id] = copy.deepcopy({'type':'tool_use','id':tool_id,'name':name,'input':tool_input})
        item = self._tool_item(tool_id, name, tool_input)
        self._emit('item/started', {'item':item})

    def _tool_item(self, tool_id: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {'id':tool_id,'type':'toolCall','status':'inProgress','tool':name}
        if name == 'Bash':
            item.update(type='commandExecution', command=str(value.get('command',''))[:16000], cwd=str(self._cwd))
        elif name in {'Write','Edit'}:
            diff = value.get('content') if name == 'Write' else f"{value.get('old_string','')}\n→\n{value.get('new_string','')}"
            item.update(type='fileChange', changes=[{'path':value.get('file_path',''),
                        'kind':'add' if name == 'Write' else 'update', 'diff':str(diff)[:16000]}])
        return item

    def _complete_tool(self, block: dict[str, Any]) -> None:
        tool_id = block.get('tool_use_id')
        use = self._tool_uses.get(tool_id)
        if use is None or tool_id in self._tool_done:
            return  # replay/unmatched evidence never counts
        self._tool_done.add(tool_id)
        item = self._tool_item(tool_id,use['name'],use['input'])
        success = block.get('is_error') is not True
        item['status'] = 'completed' if success else 'failed'
        self._emit('item/completed', {'item':item})
        if success and use['name'] == 'Read' and self._has_image(block.get('content')):
            path = use['input'].get('file_path')
            if isinstance(path,str) and path:
                resolved = (self._cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
                # Only attempt inputs are admissible as G5 evidence.
                if resolved.is_relative_to(self._cwd):
                    self._emit('item/completed', {'item':{'type':'imageView','id':tool_id+'-image',
                               'status':'completed','path':str(resolved)}})

    @staticmethod
    def _has_image(content: Any) -> bool:
        if not isinstance(content,list):
            return False
        for part in content:
            if not isinstance(part,dict) or part.get('type') != 'image':
                continue
            source = part.get('source',{})
            if (not isinstance(source,dict) or source.get('type') != 'base64'
                    or source.get('media_type') not in {'image/png','image/jpeg','image/webp','image/gif'}
                    or not isinstance(source.get('data'),str) or not source['data']):
                continue
            try:
                raw = base64.b64decode(source['data'], validate=True)
            except (ValueError, TypeError):
                continue
            if ((source['media_type']=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))
                    or (source['media_type']=='image/jpeg' and raw.startswith(b'\xff\xd8\xff'))
                    or (source['media_type']=='image/gif' and raw.startswith((b'GIF87a',b'GIF89a')))
                    or (source['media_type']=='image/webp' and raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')):
                return True
        return False

    def _permission(self, message: dict[str, Any]) -> None:
        request_id, request = message.get('request_id'), message.get('request')
        if not isinstance(request_id,(int,str)) or isinstance(request_id,bool) or not isinstance(request,dict):
            raise ValueError('invalid permission request')
        if request.get('subtype') != 'can_use_tool':
            self._send({'type':'control_response','response':{'subtype':'error','request_id':request_id,
                        'error':'This client does not implement that control request'}})
            return
        name, value, tool_id = request.get('tool_name'), request.get('input'), request.get('tool_use_id')
        if not isinstance(name,str) or not isinstance(value,dict) or not isinstance(tool_id,str):
            raise ValueError('invalid permission tool input')
        if not self._session_ready.is_set() or not self._active:
            raise ProviderError('PROTOCOL_ERROR','Permission request has no current initialized turn')
        if name not in _TOOLS or value.get('dangerouslyDisableSandbox') or value.get('run_in_background'):
            self._control_reply(request_id, {'behavior':'deny','message':'This tool or sandbox escape is unsupported'})
            return
        if name in {'Read','Write','Edit'}:
            path = value.get('file_path')
            resolved = (self._cwd / path).resolve() if isinstance(path,str) and path else None
            if resolved is None or not resolved.is_relative_to(self._cwd):
                self._control_reply(request_id, {'behavior':'deny','message':'File tool paths must remain inside the current attempt'})
                return
        self._record_tool({'type':'tool_use','id':tool_id,'name':name,'input':value})
        params: dict[str, Any] = {'itemId':tool_id,'reason':'Claude Code requests one-time tool permission',
                                  'claude_tool':name}
        method = 'item/commandExecution/requestApproval'
        if name in {'Write','Edit'}:
            method = 'item/fileChange/requestApproval'
        elif name == 'AskUserQuestion':
            method = 'item/tool/requestUserInput'
            questions = value.get('questions')
            if not isinstance(questions,list) or not 1 <= len(questions) <= 10:
                raise ValueError('invalid questions')
            params['questions'] = []
            for index, question in enumerate(questions):
                if not isinstance(question,dict) or not isinstance(question.get('question'),str):
                    raise ValueError('invalid question')
                params['questions'].append({'id':f'q{index+1}', 'header':question.get('header',''),
                    'question':question['question'], 'options':question.get('options',[]),
                    'multiSelect':question.get('multiSelect',False)})
        else:
            params.update(command=value.get('command') if name=='Bash' else f'{name} {value.get("file_path",value.get("query",value.get("url","")))}', cwd=str(self._cwd))
        # _emit records the authoritative pending request before callback dispatch.
        # Keep native input separately from public params and never expose auth.
        with self._lock:
            self._emit(method, params, request_id)
            if request_id in self._requests:
                self._native_requests[request_id] = copy.deepcopy(value)

    def _control_reply(self, request_id: int | str, response: dict[str, Any]) -> None:
        self._send({'type':'control_response','response':{'subtype':'success','request_id':request_id,'response':response}})

    def respond(self, request_id: int | str, decision_payload: dict[str, Any]) -> None:
        if not isinstance(request_id,(str,int)) or isinstance(request_id,bool):
            raise ValueError('A native request id is required')
        with self._lock:
            pending = self._requests.get(request_id)
            if pending is None:
                raise ProviderError('STALE_REQUEST','This Claude request is no longer pending')
            self._validate_decision(pending['method'],decision_payload)
            if pending['method'] == 'item/tool/requestUserInput':
                value = copy.deepcopy(self._native_requests[request_id])
                expected = {f'q{i+1}' for i in range(len(value['questions']))}
                answers = decision_payload['answers']
                if set(answers) != expected or any(not answers[key]['answers'] for key in expected):
                    raise ValueError('Answer each current question exactly once')
                value['answers'] = {q['question']:', '.join(answers[f'q{i+1}']['answers']) for i,q in enumerate(value['questions'])}
                result = {'behavior':'allow','updatedInput':value}
            elif decision_payload['decision'] == 'accept':
                result = {'behavior':'allow','updatedInput':self._native_requests[request_id]}
            else:
                result = {'behavior':'deny','message':'User declined this tool request'}
                if decision_payload['decision'] == 'cancel':
                    result['interrupt'] = True
                    self._cancel_requested = True
            self._control_reply(request_id,result)
            del self._requests[request_id]
            self._native_requests.pop(request_id,None)
        self._emit('serverRequest/resolved', {'requestId':request_id})

    def cancel(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        with self._lock:
            if (thread_id,turn_id) not in self._active:
                raise ProviderError('STALE_TURN','This Claude turn is not active on this connection')
            self._cancel_requested = True
        return self._control({'subtype':'interrupt'})

    def close(self) -> None:
        super().close()
        self._native_requests.clear()
        self._tool_uses.clear()
        self._tool_done.clear()

    def _fail(self, error: ProviderError) -> None:
        super()._fail(error)
        self._session_ready.set()  # wake a start waiting on an invalid init
