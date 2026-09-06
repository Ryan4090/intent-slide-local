"""Local subscription provider selection; metadata never starts a CLI."""
from __future__ import annotations
import re
from typing import Any
from .contracts import ContractError

PROVIDER_IDS = ('codex', 'claude')
EFFORTS = {
    'codex': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    'claude': ('low', 'medium', 'high', 'max'),
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
        raise ContractError('Choose codex or claude as the local provider')
    model, effort = value.get('model'), value.get('effort')
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model)):
        raise ContractError('Model must be a bounded model identifier, without command options')
    if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS[provider]):
        raise ContractError(f'Unsupported reasoning effort for {provider}')
    if provider == 'claude':
        if model is not None and model not in CLAUDE_MODELS:
            raise ContractError('The local subscription MVP supports Claude sonnet, opus, or haiku only')
        if model == 'haiku' and effort is not None:
            raise ContractError('Haiku does not expose a verified effort profile; omit effort')
    return {'provider': provider, 'model': model, 'effort': effort}


def provider_metadata() -> list[dict[str, str]]:
    return [
        {'id':'codex', 'label':'Codex', 'install_url':'https://developers.openai.com/codex/cli/', 'login_command':'codex login'},
        {'id':'claude', 'label':'Claude Code', 'install_url':'https://code.claude.com/docs/en/setup', 'login_command':'claude auth login'},
    ]


def provider_factory(provider_id: str = 'codex'):
    selected = normalize_selection(provider_id)['provider']
    if selected == 'codex':
        from .provider import CodexProvider
        return CodexProvider()
    from .claude_provider import ClaudeProvider
    return ClaudeProvider()
