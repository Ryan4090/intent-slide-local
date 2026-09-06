"""Local subscription provider selection; metadata never starts a CLI."""
from __future__ import annotations
import re
import os
from pathlib import Path
from typing import Any
from .contracts import ContractError

PROVIDER_IDS = ('codex', 'claude', 'gemini', 'opencode')
# Keep historical selections readable; the current product enables only Codex.
MVP_PROVIDER_IDS = ('codex',)
EFFORTS = {
    'codex': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    'claude': ('low', 'medium', 'high', 'max'),
    'gemini': (),
    'opencode': (),
}
# Deliberately omit Fable/best: print mode can bill usage credits without asking.
# Aliases are a documented selection profile, not a discovered account catalog.
CLAUDE_MODELS = ('sonnet', 'opus', 'haiku')


def normalize_selection(value: Any = None) -> dict[str, Any]:
    if value is None:
        value = {}
    elif isinstance(value, str):
        value = {'provider': value}
    if not isinstance(value, dict) or not set(value) <= {'provider', 'model', 'effort'}:
        raise ContractError('Provider selection must contain only provider, model, and effort')
    provider = value.get('provider', 'codex')
    if not isinstance(provider, str) or provider not in PROVIDER_IDS:
        raise ContractError('Choose a supported local AI provider')
    model, effort = value.get('model'), value.get('effort')
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model)):
        raise ContractError('Model must be a bounded model identifier, without command options')
    valid_effort = (isinstance(effort,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',effort))) if provider in {'gemini','opencode'} else effort in EFFORTS[provider]
    if effort is not None and (not isinstance(effort, str) or not valid_effort):
        raise ContractError(f'Unsupported reasoning effort for {provider}')
    if provider == 'claude':
        if model is not None and model not in CLAUDE_MODELS:
            raise ContractError('The local subscription MVP supports Claude sonnet, opus, or haiku only')
        if model == 'haiku' and effort is not None:
            raise ContractError('Haiku does not expose a verified effort profile; omit effort')
    return {'provider': provider, 'model': model, 'effort': effort}


def mvp_selection(value: Any = None) -> dict[str, Any]:
    selection = normalize_selection(value)
    if selection['provider'] not in MVP_PROVIDER_IDS:
        raise ContractError('현재 MVP는 Codex 전용입니다. Claude Code 연동은 MVP 이후 제공할 예정입니다.')
    return selection


def provider_metadata() -> list[dict[str, str]]:
    return [
        {'id':'codex', 'label':'Codex', 'install_url':'https://developers.openai.com/codex/cli/', 'login_command':'codex login', 'auto_connect':True, 'efforts':list(EFFORTS['codex'])},
        {'id':'claude', 'label':'Claude Code', 'install_url':'https://code.claude.com/docs/en/setup', 'login_command':'claude auth login', 'auto_connect':True, 'efforts':list(EFFORTS['claude']), 'beta':True},
        {'id':'gemini', 'label':'Gemini CLI', 'install_url':'https://geminicli.com/docs/get-started/installation/', 'login_command':'gemini', 'auto_connect':False, 'efforts':[], 'beta':True},
        {'id':'opencode', 'label':'OpenCode', 'install_url':'https://opencode.ai/docs/', 'login_command':'opencode auth login', 'auto_connect':False, 'efforts':[], 'beta':True},
    ]


def provider_factory(provider_id: str = 'codex'):
    selected = normalize_selection(provider_id)['provider']
    if selected == 'codex':
        from .provider import CodexProvider
        bundled=os.environ.get('INTENT_SLIDE_CODEX')
        if bundled:
            if not Path(bundled).is_file() or not Path(bundled).is_absolute():
                raise ContractError('Bundled Codex executable is unavailable')
            return CodexProvider(command=[bundled,'app-server'])
        return CodexProvider()
    if selected == 'claude':
        from .claude_provider import ClaudeProvider
        return ClaudeProvider()
    from .acp_provider import ACPProvider
    return ACPProvider(selected)
