"""Public provider selection is a closed, non-executable JSON contract."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from presentation_agents.v2.contracts import ContractError
from presentation_agents.v2.provider_registry import PROVIDER_IDS, normalize_selection, provider_factory, provider_metadata

class RegistryTests(unittest.TestCase):
    def test_default_and_provider_string(self):
        self.assertEqual(normalize_selection(), {'provider':'codex','model':None,'effort':None})
        self.assertEqual(normalize_selection('claude')['provider'], 'claude')
        self.assertEqual(PROVIDER_IDS, ('codex','claude','gemini','opencode'))
    def test_closed_object_and_types(self):
        for value in ([], 3, True, {'provider':'other'}, {'provider':'codex','token':'secret'}, {'model':True}, {'effort':[]}):
            with self.subTest(value=value), self.assertRaises(ContractError): normalize_selection(value)
    def test_injection_and_cross_provider_effort_rejected(self):
        for model in ('--dangerously-skip-permissions', 'opus --bare', 'x\n--bare', 'x;pwd', ''):
            with self.subTest(model=model), self.assertRaises(ContractError): normalize_selection({'model':model})
        with self.assertRaises(ContractError): normalize_selection({'provider':'claude','effort':'ultra'})
        self.assertEqual(normalize_selection({'provider':'codex','model':'gpt-5.5','effort':'xhigh'})['effort'], 'xhigh')
    def test_metadata_and_factory_have_no_auth_side_effects(self):
        with patch('subprocess.Popen', side_effect=AssertionError('metadata must be cheap')):
            self.assertEqual([x['id'] for x in provider_metadata()], list(PROVIDER_IDS))
            for name in PROVIDER_IDS:
                with provider_factory(name) as provider: self.assertTrue(callable(provider.preflight))
    def test_claude_billing_models_and_unknown_effort_profiles_rejected(self):
        for model in ('fable','best','opusplan','claude-fable-5','custom-gateway'):
            with self.subTest(model=model), self.assertRaises(ContractError): normalize_selection({'provider':'claude','model':model})
        with self.assertRaises(ContractError): normalize_selection({'provider':'claude','model':'haiku','effort':'high'})
