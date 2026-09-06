"""Bounded ACP v1 adapters for existing Gemini CLI and OpenCode installations.

Usage: with ACPProvider(provider_id='gemini') as provider: provider.preflight()
Dependencies: existing native CLI, shared stdio transport, declared Pillow.
Protocol: https://agentclientprotocol.com/protocol/v1/initialization
No login, credential inspection, API fallback, global settings or shell launcher.
The native agent owns its tools; this client only proxies scoped text reads.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .provider import CodexProvider, EventCallback, ProviderError

_COMMANDS = {'gemini': ('gemini', '--acp'), 'opencode': ('opencode', 'acp')}
_BILLING = '이 도구의 기존 로그인·모델·과금 설정을 사용합니다. 구독 포함 여부는 제공자에서 확인하세요.'
_SUITE = Path(__file__).resolve().parents[3]
_MAX_FILE = 8 * 1024 * 1024
_MAX_TOOLS = 4096


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value))


class ACPProvider(CodexProvider):
    """One explicitly selected native agent; readiness never asserts billing.

    ``command`` is an internal fixture/developer seam, never a web setting.
    Production registry must instantiate only the two fixed provider ids.
    ``ready`` means a native session could be created and a model
    was reported. It does not establish subscription entitlement or tool sandbox.
    """

    def __init__(self, provider_id: str = 'gemini', *, command: list[str] | None = None,
                 probe_directory: Path | None = None, **bounds: Any) -> None:
        if provider_id not in _COMMANDS:
            raise ValueError('Only reviewed Gemini and OpenCode ACP profiles are available')
        if not 0 < bounds.get('request_timeout', 30) <= 60:
            raise ValueError('ACP request timeout must be within 60 seconds')
        super().__init__(command=command or list(_COMMANDS[provider_id]), **bounds)
        self.provider_id = provider_id
        self._probe_parent = Path(probe_directory) if probe_directory else _SUITE / '.runtime'
        self._probe_temp: tempfile.TemporaryDirectory | None = None
        self._agent_caps: dict[str, Any] = {}
        self._caps: dict[str, Any] = {}
        self._session_cwds: dict[str, Path] = {}
        self._session_id: str | None = None
        self._turn_id: str | None = None
        self._cwd: Path | None = None
        self._tools: dict[str, dict[str, Any]] = {}
        self._tool_done: set[str] = set()
        self._native_requests: dict[int | str, dict[str, Any]] = {}
        self._permission_lock = threading.RLock()
        self._cancel_deadline: float | None = None
        self._prompt_request: int | None = None
        self._message_parts: list[str] = []
        self._model_id: str | None = None
        self._selected_effort: str | None = None
        self._config: list[dict[str, Any]] = []

    def _send(self, message: dict[str, Any]) -> None:
        super()._send({'jsonrpc': '2.0', **message})

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise ProviderError('CLOSED', 'ACP connection is closed')
            if self._failure:
                raise self._failure
            if self._initialized:
                return
            executable = shutil.which(self.command[0])
            if not executable:
                raise ProviderError('UNAVAILABLE', '선택한 CLI를 찾을 수 없습니다. 공식 도구를 직접 설치하고 로그인해 주세요.')
            self._probe_parent.mkdir(parents=True, exist_ok=True)
            self._probe_temp = tempfile.TemporaryDirectory(prefix='acp-preflight-', dir=self._probe_parent)
            # Shared transport resolves supported Windows npm shims without cmd.exe.
            self._launch_process([str(Path(executable).absolute()), *self.command[1:]], cwd=Path(self._probe_temp.name))
            for target in (self._read_stdout, self._read_stderr, self._dispatch, self._watchdog):
                worker = threading.Thread(target=target, daemon=True, name=f'acp-{target.__name__}')
                self._threads.append(worker)
                worker.start()
            result = self._rpc('initialize', {
                'protocolVersion': 1,
                'clientCapabilities': {'fs': {'readTextFile': True, 'writeTextFile': False}, 'terminal': False},
                'clientInfo': {'name': 'intent-slide', 'title': 'Intent-Slide', 'version': '0.1.0'},
            })
            if result.get('protocolVersion') != 1:
                error = ProviderError('PROTOCOL_VERSION', 'The native agent did not negotiate supported ACP v1')
                self._fail(error)
                raise error
            caps = result.get('agentCapabilities', {})
            if not isinstance(caps, dict):
                raise ProviderError('INVALID_RESPONSE', 'ACP capabilities must be an object')
            self._agent_caps = copy.deepcopy(caps)
            if not isinstance(caps.get('promptCapabilities', {}), dict):
                raise ProviderError('INVALID_RESPONSE', 'ACP prompt capabilities must be an object')
            info = result.get('agentInfo', {})
            self._server = {key: info[key][:256] for key in ('name', 'version')
                            if isinstance(info, dict) and isinstance(info.get(key), str)}
            self._initialized = True

    def preflight(self) -> dict[str, Any]:
        """Initialize and create a no-prompt probe session; never authenticate."""
        if self._caps:
            return copy.deepcopy(self._caps)
        result = {
            'provider': self.provider_id, 'ready': False, 'protocol_ready': False,
            'auto_connect': False, 'auth_mode': 'native_unverified', 'authenticated': None,
            'billing_notice': _BILLING, 'models': [], 'default_model': None,
            'status': 'UNVERIFIED', 'reason': None,
            'features': {'streaming': True, 'approvals': True, 'user_input': False,
                         'image_input': False, 'image_view': False, 'image_view_verified': False,
                         'image_view_status': 'UNVERIFIED', 'resume': False, 'cancel': True,
                         'workspace_isolation': 'ACP cwd; scoped read-only client proxy; native tool sandbox UNVERIFIED'},
            'runtime': {'status': 'UNVERIFIED', 'integration_status': 'UNVERIFIED'},
            'rate_limits': None, 'rate_limits_status': 'UNAVAILABLE',
        }
        try:
            self._ensure_started()
            result['server'] = self._server
            result['features'].update(resume=self._agent_caps.get('loadSession') is True,
                                      image_input=self._agent_caps.get('promptCapabilities', {}).get('image') is True)
            if self.provider_id == 'gemini':
                result['features']['image_view_reason'] = 'Gemini CLI 0.54.4 ACP reports tool display text/diff, not image bytes; G5 needs actual image content evidence'
            assert self._probe_temp is not None
            directory = Path(self._probe_temp.name).resolve()
            state = self._rpc('session/new', {'cwd': str(directory), 'mcpServers': []})
            session_id = state.get('sessionId')
            if not _identifier(session_id):
                raise ProviderError('INVALID_RESPONSE', 'ACP did not return a bounded session id')
            self._session_cwds[session_id] = directory
            models, current, config = self._model_state(state)
            self._models, self._model_id, self._config = models, current, config
            result.update(ready=True, protocol_ready=True, status='PARTIALLY_VERIFIED',
                          models=models, default_model=current, server=self._server)
            result['runtime'].update(status='PARTIALLY_VERIFIED',
                                     reason='ACP initialize and session/new succeeded; no model prompt was sent')
        except ProviderError as exc:
            result.update(reason=str(exc), error_code=exc.code)
            if exc.code == 'AUTH_REQUIRED':
                result.update(auth_mode='none', authenticated=False)
        self._ready = result['ready']
        self._caps = result
        return copy.deepcopy(result)

    @staticmethod
    def _option_values(option: dict[str, Any]) -> list[dict[str, str]]:
        raw = option.get('options', [])
        if not isinstance(raw, list) or len(raw) > 512:
            raise ProviderError('INVALID_RESPONSE', 'ACP option list exceeds its bound')
        flat = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise ProviderError('INVALID_RESPONSE', 'ACP option is not an object')
            group = entry.get('options')
            items = group if isinstance(group, list) else [entry]
            if len(items) + len(flat) > 512:
                raise ProviderError('INVALID_RESPONSE', 'ACP option group exceeds its bound')
            for item in items:
                if not isinstance(item, dict) or not _identifier(item.get('value')):
                    raise ProviderError('INVALID_RESPONSE', 'ACP option value is invalid')
                name = item.get('name', item['value'])
                flat.append({'value': item['value'], 'name': name[:256] if isinstance(name, str) else item['value']})
        if len({x['value'] for x in flat}) != len(flat):
            raise ProviderError('INVALID_RESPONSE', 'ACP option values are duplicated')
        return flat

    @classmethod
    def _model_state(cls, state: dict[str, Any]) -> tuple[list[dict], str, list[dict]]:
        config = state.get('configOptions', [])
        if not isinstance(config, list) or len(config) > 128 or not all(isinstance(x, dict) for x in config):
            raise ProviderError('INVALID_RESPONSE', 'ACP configuration is invalid')
        model_option = next((x for x in config if x.get('category') == 'model' and x.get('type') == 'select'), None)
        effort_option = next((x for x in config if x.get('category') == 'thought_level' and x.get('type') == 'select'), None)
        if model_option:
            options = cls._option_values(model_option)
            current = model_option.get('currentValue')
        else:
            # Older ACP peers expose SessionModelState and session/set_model.
            legacy = state.get('models', {})
            values = legacy.get('availableModels', []) if isinstance(legacy, dict) else []
            if not isinstance(values, list) or len(values) > 512:
                raise ProviderError('INVALID_RESPONSE', 'ACP legacy model catalog is invalid')
            options = [{'value': x.get('modelId'), 'name': x.get('name', x.get('modelId'))}
                       for x in values if isinstance(x, dict)]
            current = legacy.get('currentModelId') if isinstance(legacy, dict) else None
        if not options or not _identifier(current) or current not in {x['value'] for x in options}:
            raise ProviderError('MODEL_UNAVAILABLE', 'The native session did not report an available current model')
        if len({x['value'] for x in options}) != len(options) or any(not _identifier(x['value']) for x in options):
            raise ProviderError('INVALID_RESPONSE', 'The native model catalog contains invalid or duplicate ids')
        efforts = cls._option_values(effort_option) if effort_option else []
        models = [{'id': x['value'], 'model': x['value'],
                   'displayName': x['name'][:256] if isinstance(x['name'], str) else x['value'],
                   'isDefault': x['value'] == current,
                   'supportedReasoningEfforts': [{'reasoningEffort': e['value']} for e in efforts] if x['value'] == current else [],
                   'availability': 'native_session_catalog'} for x in options]
        return models, current, copy.deepcopy(config)

    def _configure(self, session_id: str, state: dict[str, Any], model: str | None, effort: str | None) -> tuple[str, str | None]:
        models, current, config = self._model_state(state)
        chosen = model if model is not None else current
        if chosen not in {x['model'] for x in models}:
            raise ProviderError('MODEL_UNAVAILABLE', 'Choose a model reported by this native session')
        if chosen != current:
            option = next((x for x in config if x.get('category') == 'model' and x.get('type') == 'select'), None)
            if option and _identifier(option.get('id')):
                state = self._rpc('session/set_config_option', {'sessionId': session_id, 'configId': option['id'], 'value': chosen})
                models, current, config = self._model_state(state)
                if current != chosen:
                    raise ProviderError('MODEL_MISMATCH', 'The native agent did not acknowledge the requested model')
            elif isinstance(state.get('models'), dict):
                # Gemini 0.54.4 implements this official SDK method with an
                # empty success response. Catalog membership plus successful
                # RPC acknowledgement is its selection receipt; not a claim
                # that the subsequently billed model has been observed.
                receipt = self._rpc('session/set_model', {'sessionId': session_id, 'modelId': chosen})
                if receipt:
                    raise ProviderError('MODEL_MISMATCH', 'Unexpected legacy model-selection response')
                current = chosen
            else:
                raise ProviderError('MODEL_SELECTION_UNAVAILABLE', 'The native agent did not expose model selection')
        if effort is not None:
            option = next((x for x in config if x.get('category') == 'thought_level' and x.get('type') == 'select'), None)
            if not option or effort not in {x['value'] for x in self._option_values(option)} or not _identifier(option.get('id')):
                raise ProviderError('EFFORT_UNAVAILABLE', 'The native model did not advertise the selected effort')
            state = self._rpc('session/set_config_option', {'sessionId': session_id, 'configId': option['id'], 'value': effort})
            models, acknowledged_model, config = self._model_state(state)
            acknowledged = next((x for x in config if x.get('id') == option['id']), {})
            if acknowledged.get('currentValue') != effort or acknowledged_model != chosen:
                raise ProviderError('EFFORT_MISMATCH', 'The native agent did not acknowledge the requested model and effort')
        self._models, self._model_id, self._config = models, chosen, config
        return chosen, effort

    def start_turn(self, cwd: Path, prompt: str, on_event: EventCallback, thread_id: str | None = None,
                   model: str | None = None, effort: str | None = None) -> dict[str, Any]:
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir() or not isinstance(prompt, str) or not prompt.strip() or not callable(on_event):
            raise ValueError('A real attempt directory, prompt and callback are required')
        if thread_id is not None and not _identifier(thread_id):
            raise ValueError('Invalid ACP session id')
        with self._start_lock:
            caps = self.preflight()
            if not caps['ready']:
                raise ProviderError(caps.get('error_code', 'UNAVAILABLE'), caps.get('reason') or 'ACP is not ready')
            if self._failure or self._closed:
                raise self._failure or ProviderError('CLOSED', 'ACP is closed')
            with self._lock:
                if self._active:
                    raise ProviderError('BUSY', 'An ACP turn is already running')
            if thread_id and self._agent_caps.get('loadSession') is not True:
                raise ProviderError('RESUME_UNAVAILABLE', 'This native agent does not advertise session/load')
            # Replay emitted during session/load is intentionally not generation evidence.
            params = {'cwd': str(directory), 'mcpServers': []}
            if thread_id:
                params['sessionId'] = thread_id
            state = self._rpc('session/load' if thread_id else 'session/new', params)
            session_id = state.get('sessionId', thread_id)
            if not _identifier(session_id) or (thread_id and session_id != thread_id):
                raise ProviderError('INVALID_RESPONSE', 'ACP returned another session')
            self._session_cwds[session_id] = directory
            chosen_model, chosen_effort = self._configure(session_id, state, model, effort)
            turn_id = 'acp-turn-' + uuid.uuid4().hex
            with self._lock:
                self._session_id, self._turn_id, self._cwd = session_id, turn_id, directory
                self._selected_effort = chosen_effort
                self._tools.clear(); self._tool_done.clear(); self._native_requests.clear()
                self._message_parts.clear(); self._cancel_deadline = None
                self._callbacks[session_id] = on_event
                self._active[(session_id, turn_id)] = time.monotonic()
                self._next_id += 1
                request_id = self._next_id
                waiter = {'event': threading.Event(), 'response': None}
                self._pending[request_id] = waiter
                self._prompt_request = request_id
            try:
                self._send({'id': request_id, 'method': 'session/prompt',
                            'params': {'sessionId': session_id, 'prompt': [{'type': 'text', 'text': prompt}]}})
            except Exception:
                with self._lock:
                    self._pending.pop(request_id, None)
                    self._active.pop((session_id, turn_id), None)
                raise
            worker = threading.Thread(target=self._await_prompt, args=(request_id, waiter, session_id, turn_id), daemon=True, name='acp-prompt')
            self._threads.append(worker); worker.start()
            return {'thread_id': session_id, 'turn_id': turn_id, 'model': chosen_model, 'effort': chosen_effort}

    def _await_prompt(self, request_id: int, waiter: dict, session_id: str, turn_id: str) -> None:
        try:
            # session/prompt responds at turn completion, not within the short
            # RPC acknowledgement timeout. The shared active-turn watchdog owns
            # this wait, including pausing while a human permission is pending.
            while not waiter['event'].wait(.1):
                if self._closed or self._failure:
                    return
            response = waiter['response']
            if isinstance(response, ProviderError):
                self._fail(response)
                return
            result = response.get('result', {}) if isinstance(response, dict) else {}
            reason = result.get('stopReason') if isinstance(result, dict) else None
            status = ('interrupted' if self._cancel_deadline is not None or reason == 'cancelled'
                      else 'completed' if reason == 'end_turn' else 'failed')
            if self._message_parts:
                self._emit('item/completed', {'item': {'type': 'agentMessage', 'id': turn_id + '-message', 'text': ''.join(self._message_parts)}})
            self._resolve_all_cancelled()
            with self._lock:
                self._active.pop((session_id, turn_id), None)
                self._cancel_deadline = None
                self._prompt_request = None
            turn = {'id': turn_id, 'status': status}
            if status == 'failed':
                turn['error'] = {'message': 'ACP turn failed or stopped before end_turn'}
            self._emit('turn/completed', {'turn': turn})
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def _emit(self, method: str, params: dict[str, Any], request_id: int | str | None = None) -> None:
        if not self._session_id or not self._turn_id:
            return
        callback = self._callbacks.get(self._session_id)
        if callback:
            event = {'method': method, 'params': {'threadId': self._session_id, 'turnId': self._turn_id, **params}}
            if request_id is not None:
                event['id'] = request_id
            self._enqueue(callback, event)

    def _receive(self, message: dict[str, Any]) -> None:
        if message.get('jsonrpc') != '2.0':
            self._fail(ProviderError('PROTOCOL_ERROR', 'ACP peer emitted a non-JSON-RPC-v2 frame'))
            return
        if 'method' not in message:
            if not isinstance(message.get('id'), (int, str)) or isinstance(message.get('id'), bool):
                self._fail(ProviderError('PROTOCOL_ERROR', 'ACP response id is invalid'))
                return
            error = message.get('error')
            if isinstance(error, dict) and error.get('code') == -32000:
                with self._lock:
                    waiter = self._pending.get(message.get('id'))
                    if waiter:
                        waiter['response'] = ProviderError('AUTH_REQUIRED', '기존 CLI에서 직접 로그인한 뒤 다시 연결해 주세요. 자동 인증이나 API 대체는 수행하지 않습니다.')
                        waiter['event'].set()
                return
            super()._receive(message)
            return
        method, params = message.get('method'), message.get('params', {})
        if not isinstance(method, str) or not isinstance(params, dict):
            self._fail(ProviderError('PROTOCOL_ERROR', 'ACP message shape is invalid'))
            return
        try:
            if 'id' in message:
                request_id = message['id']
                if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
                    raise ProviderError('PROTOCOL_ERROR', 'ACP request id is invalid')
                if method == 'session/request_permission':
                    self._permission(request_id, params)
                elif method == 'fs/read_text_file':
                    self._read_text_proxy(request_id, params)
                else:
                    # Never execute an agent-supplied terminal command, write a
                    # client file, authenticate or open an arbitrary URL.
                    self._send({'id': request_id, 'error': {'code': -32601, 'message': 'Client capability is not implemented'}})
                return
            if method == 'session/update' and params.get('sessionId') == self._session_id and self._active:
                update = params.get('update')
                if not isinstance(update, dict):
                    raise ProviderError('PROTOCOL_ERROR', 'ACP session update is invalid')
                self._update(update)
        except ProviderError as exc:
            self._fail(exc)
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            self._fail(ProviderError('PROTOCOL_ERROR', 'ACP peer emitted an invalid or inaccessible tool event'))

    def _permission(self, request_id: int | str, params: dict[str, Any]) -> None:
        if params.get('sessionId') != self._session_id or not self._active or self._cancel_deadline is not None:
            self._send({'id': request_id, 'result': {'outcome': {'outcome': 'cancelled'}}})
            return
        tool, options = params.get('toolCall'), params.get('options')
        if not isinstance(tool, dict) or not _identifier(tool.get('toolCallId')) or not isinstance(options, list) or len(options) > 32:
            raise ProviderError('PROTOCOL_ERROR', 'ACP permission request is invalid')
        choices = {}
        for option in options:
            if not isinstance(option, dict) or not _identifier(option.get('optionId')):
                raise ProviderError('PROTOCOL_ERROR', 'ACP permission option is invalid')
            if option.get('kind') in ('allow_once', 'reject_once'):
                choices.setdefault(option['kind'], option['optionId'])
        if not choices:
            self._send({'id': request_id, 'result': {'outcome': {'outcome': 'cancelled'}}})
            return
        tool_id = tool['toolCallId']
        if tool_id not in self._tools:
            # Gemini introduces tools requiring permission through this request
            # and sends only tool_call_update after the user has responded.
            self._update({'sessionUpdate': 'tool_call', 'status': 'pending', **tool})
        context = {**self._tools.get(tool_id, {}), **tool}
        raw = context.get('rawInput', {})
        command = raw.get('command') if isinstance(raw, dict) else None
        if not isinstance(command, str):
            command = context.get('title', 'Native ACP tool permission')
        normalized = {'threadId': self._session_id, 'turnId': self._turn_id,
                      'itemId': tool_id, 'command': str(command)[:16000], 'cwd': str(self._cwd),
                      'reason': 'Native agent requests one-time tool permission', 'toolKind': context.get('kind', 'other')}
        with self._lock:
            if self._cancel_deadline is not None or not self._active:
                self._send({'id': request_id, 'result': {'outcome': {'outcome': 'cancelled'}}})
                return
            if request_id in self._requests or len(self._requests) >= 64:
                raise ProviderError('PROTOCOL_ERROR', 'ACP duplicated a request id or exceeded pending request bounds')
            self._native_requests[request_id] = choices
            self._requests[request_id] = {'method': 'item/commandExecution/requestApproval', 'params': normalized}
        self._emit('item/commandExecution/requestApproval', normalized, request_id=request_id)

    def respond(self, request_id: int | str, decision_payload: dict[str, Any]) -> None:
        self._validate_decision('item/commandExecution/requestApproval', decision_payload)
        # Serialize one-time decisions with cancellation responses. The watchdog
        # does not take this lock and can still kill a non-reading process.
        with self._permission_lock:
            with self._lock:
                choices = self._native_requests.get(request_id)
                if choices is None or request_id not in self._requests or self._cancel_deadline is not None:
                    raise ProviderError('STALE_REQUEST', 'The ACP permission request is no longer pending')
                decision = decision_payload['decision']
                kind = 'allow_once' if decision == 'accept' else 'reject_once'
                if decision != 'cancel' and kind not in choices:
                    raise ValueError('The native agent did not offer this one-time decision')
                outcome = {'outcome': 'cancelled'} if decision == 'cancel' else {'outcome': 'selected', 'optionId': choices[kind]}
                del self._native_requests[request_id]
                del self._requests[request_id]
            self._send({'id': request_id, 'result': {'outcome': outcome}})
        self._emit('serverRequest/resolved', {'requestId': request_id})

    def _resolve_all_cancelled(self) -> None:
        with self._permission_lock:
            with self._lock:
                pending = list(self._native_requests)
                self._native_requests.clear()
                for request_id in pending:
                    self._requests.pop(request_id, None)
            for request_id in pending:
                self._send({'id': request_id, 'result': {'outcome': {'outcome': 'cancelled'}}})
                self._emit('serverRequest/resolved', {'requestId': request_id})

    def cancel(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        with self._lock:
            if (thread_id, turn_id) not in self._active:
                raise ProviderError('STALE_TURN', 'This ACP turn is not active')
            if self._cancel_deadline is not None:
                return {}
            self._cancel_deadline = time.monotonic() + min(3, self.request_timeout)
        # Watchdog terminates an unresponsive process group even if sending
        # these short frames encounters a full pipe.
        self._resolve_all_cancelled()
        self._send({'method': 'session/cancel', 'params': {'sessionId': thread_id}})
        return {}

    def _watchdog(self) -> None:
        previous = time.monotonic()
        while not self._stop.wait(.1):
            current = time.monotonic()
            with self._lock:
                if self._cancel_deadline is not None and current >= self._cancel_deadline:
                    self._fail(ProviderError('CANCEL_TIMEOUT', 'ACP cancellation was not acknowledged; the owned process was terminated'))
                    return
                waiting = {x['params'].get('threadId') for x in self._requests.values()}
                for key, start in list(self._active.items()):
                    if key[0] in waiting:
                        self._active[key] += current - previous
                    elif current - start > self.turn_timeout:
                        self._fail(ProviderError('TURN_TIMEOUT', 'ACP exceeded its active execution deadline'))
                        return
            previous = current

    def _safe_bytes(self, session_id: str, value: Any, *, limit: int = _MAX_FILE) -> tuple[Path, bytes]:
        root = self._session_cwds.get(session_id)
        if root is None or not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
            raise ValueError('Unknown session or invalid file path')
        path = Path(value)
        if '..' in path.parts:
            raise ValueError('Parent traversal is not allowed')
        path = path if path.is_absolute() else root / path
        if not path.is_relative_to(root):
            raise ValueError('File is outside the current workspace')
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Symlink file paths are not allowed')
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError('Only regular workspace files are available')
        # On POSIX anchor every parent to an already opened directory. A worker
        # swapping an intermediate directory for a symlink cannot expose an
        # outside file between the lexical check and open().
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parts = path.relative_to(root).parts
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    os.close(parent)
                    parent = child
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            finally:
                os.close(parent)
        else:
            descriptor = os.open(resolved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('File is not regular or exceeds its read bound')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(descriptor)
            disk = resolved.stat()
            if (len(data) > limit or len(data) != before.st_size
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or (after.st_dev, after.st_ino) != (disk.st_dev, disk.st_ino)
                    or path.resolve(strict=True) != resolved):
                raise ValueError('File changed while reading')
            return resolved, data
        finally:
            os.close(descriptor)

    def _read_text_proxy(self, request_id: int | str, params: dict[str, Any]) -> None:
        try:
            _, raw = self._safe_bytes(params.get('sessionId'), params.get('path'), limit=1024 * 1024)
            text = raw.decode('utf-8')
            if '\x00' in text:
                raise ValueError('Text proxy does not expose binary files')
            line, count = params.get('line', 1), params.get('limit')
            if isinstance(line, bool) or not isinstance(line, int) or line < 1 or (count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1)):
                raise ValueError('Invalid text range')
            if line != 1 or count is not None:
                lines = text.splitlines(keepends=True)
                text = ''.join(lines[line - 1:None if count is None else line - 1 + count])
            self._send({'id': request_id, 'result': {'content': text}})
        except (OSError, ValueError, UnicodeError):
            self._send({'id': request_id, 'error': {'code': -32602, 'message': 'Only bounded regular UTF-8 files inside this session workspace may be read'}})

    @staticmethod
    def _tool_path(tool: dict[str, Any]) -> Any:
        raw = tool.get('rawInput', {})
        value = next((raw[k] for k in ('file_path', 'filePath', 'path') if isinstance(raw, dict) and isinstance(raw.get(k), str)), None)
        locations = tool.get('locations', [])
        if value is None and isinstance(locations, list) and len(locations) == 1 and isinstance(locations[0], dict):
            value = locations[0].get('path')
        return value

    def _capture_tool_file(self, tool: dict[str, Any]) -> None:
        value = self._tool_path(tool)
        if value is None or tool.get('_file_rejected'):
            return
        try:
            path, data = self._safe_bytes(self._session_id, value)
            tool['_file'] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        except (OSError, ValueError):
            tool['_file'] = None
            tool['_file_rejected'] = True

    def _update(self, update: dict[str, Any]) -> None:
        kind = update.get('sessionUpdate')
        if kind == 'agent_message_chunk':
            content = update.get('content', {})
            if isinstance(content, dict) and content.get('type') == 'text' and isinstance(content.get('text'), str):
                if sum(map(len, self._message_parts)) + len(content['text']) > 50000:
                    raise ProviderError('OUTPUT_LIMIT', 'ACP assistant message exceeded its bound')
                self._message_parts.append(content['text'])
                self._emit('item/agentMessage/delta', {'delta': content['text']})
            return
        if kind not in ('tool_call', 'tool_call_update'):
            return
        tool_id = update.get('toolCallId')
        if not _identifier(tool_id):
            raise ProviderError('PROTOCOL_ERROR', 'ACP tool call id is invalid')
        if tool_id in self._tool_done:
            return
        if kind == 'tool_call':
            if tool_id in self._tools or len(self._tools) >= _MAX_TOOLS:
                raise ProviderError('OUTPUT_LIMIT', 'ACP tool ledger is duplicated or exceeds its bound')
            tool = {k: copy.deepcopy(v) for k, v in update.items()
                    if k in ('kind', 'title', 'status', 'rawInput', 'locations', 'content', 'rawOutput')}
            self._tools[tool_id] = tool
            if tool.get('kind') == 'read':
                self._capture_tool_file(tool)
            item = self._tool_item(tool_id, tool, 'inProgress')
            self._emit('item/started', {'item': item})
        elif tool_id not in self._tools:
            return  # Replay or unmatched completions are not evidence.
        tool = self._tools[tool_id]
        # ACP inputs may arrive in a later in-progress update. Capture the first
        # explicit path before completion, then reject any retargeting.
        if kind == 'tool_call_update' and tool.get('kind') == 'read':
            proposed = {**tool, **{k: update[k] for k in ('rawInput', 'locations') if k in update}}
            value = self._tool_path(proposed)
            frozen = tool.get('_file')
            if frozen and any(k in update for k in ('rawInput', 'locations')):
                try:
                    path, _ = self._safe_bytes(self._session_id, value)
                    if str(path) != frozen['path']:
                        raise ValueError('Tool path changed')
                except (OSError, ValueError):
                    tool['_file'] = None
                    tool['_file_rejected'] = True
            elif not frozen and update.get('status', tool.get('status')) in ('pending', 'in_progress'):
                for field in ('rawInput', 'locations'):
                    if field in update:
                        tool[field] = copy.deepcopy(update[field])
                self._capture_tool_file(tool)
        for field in ('status', 'content', 'rawOutput'):
            if field in update:
                tool[field] = copy.deepcopy(update[field])
        if tool.get('status') not in ('completed', 'failed'):
            return
        self._tool_done.add(tool_id)
        success = tool['status'] == 'completed' and self._cancel_deadline is None
        self._emit('item/completed', {'item': self._tool_item(tool_id, tool, 'completed' if success else 'failed')})
        if success and tool.get('kind') == 'read':
            self._image_observation(tool_id, tool)
        # Large base64 payloads must not accumulate across an entire deck.
        tool.pop('content', None); tool.pop('rawOutput', None)

    def _tool_item(self, tool_id: str, tool: dict[str, Any], status: str) -> dict[str, Any]:
        item = {'id': tool_id, 'type': 'toolCall', 'status': status, 'tool': tool.get('kind', 'other')}
        raw = tool.get('rawInput', {})
        if tool.get('kind') == 'execute':
            command = raw.get('command') if isinstance(raw, dict) else None
            item.update(type='commandExecution', command=command[:16000] if isinstance(command, str) else str(tool.get('title', 'Native command'))[:16000], cwd=str(self._cwd))
        return item

    def _image_observation(self, tool_id: str, tool: dict[str, Any]) -> None:
        from PIL import Image
        frozen, content = tool.get('_file'), tool.get('content', [])
        if not frozen or not isinstance(content, list) or len(content) > 128:
            return
        try:
            path, data = self._safe_bytes(self._session_id, frozen['path'])
            digest = hashlib.sha256(data).hexdigest()
            if digest != frozen['sha256']:
                return
            for part in content:
                image = part.get('content') if isinstance(part, dict) and part.get('type') == 'content' else None
                if not isinstance(image, dict) or image.get('type') != 'image' or image.get('mimeType') not in ('image/png', 'image/jpeg'):
                    continue
                encoded = image.get('data')
                if not isinstance(encoded, str) or len(encoded) > 4 * ((_MAX_FILE + 2) // 3):
                    continue
                actual = base64.b64decode(encoded, validate=True)
                if actual != data:
                    continue
                with Image.open(io.BytesIO(actual)) as decoded:
                    if (decoded.width * decoded.height > 64_000_000
                            or {'PNG': 'image/png', 'JPEG': 'image/jpeg'}.get(decoded.format) != image['mimeType']):
                        continue
                    width, height = decoded.size
                    decoded.verify()
                self._emit('item/completed', {'item': {'id': tool_id + '-image', 'type': 'imageView',
                           'status': 'completed', 'path': str(path), 'sha256': digest, 'width': width, 'height': height}})
                return
        except (OSError, ValueError, KeyError, Image.DecompressionBombError):
            return  # Missing, changed, non-image or differently rendered bytes.

    def close(self) -> None:
        if self._active and not self._closed and not self._failure and self._session_id and self._turn_id:
            try:
                self.cancel(self._session_id, self._turn_id)
            except ProviderError:
                pass
        super().close()
        if self._probe_temp:
            self._probe_temp.cleanup()
            self._probe_temp = None
