"""Offline regression checks for shared settings and study-specific requests."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from experiment_runtime.models import generation_settings, model_catalog, model_settings

ROOT = Path(__file__).resolve().parents[1]
REQUEST_PROBE = r'''
import json, sys
from experiment_runtime.provider import Completion
arena = sys.argv[1]
if arena == 'trade':
    from src.trading.provider import Model as Client
elif arena == 'bargaining':
    from src.models import ModelClient as Client
else:
    from double_auction.providers import TracedLLM as Client
results = {}
for provider, model, backend in [
    ('openai', 'gpt-5.1-2025-11-13', None),
    ('openai', 'gpt-5-mini-2025-08-07', None),
    ('openai', 'gpt-4o-mini-2024-07-18', None),
    ('openrouter', 'google/gemini-3.7-flash', 'Google'),
    ('openrouter', 'anthropic/claude-sonnet-5', 'Anthropic'),
    ('openrouter', 'x-ai/grok-4.3', 'xAI'),
]:
    for effort in ('low', 'none'):
        for temp in (None, .7):
            client = Client.__new__(Client)  # No SDK client or credentials.
            client.provider, client.model, client.reasoning_effort = provider, model, effort
            client.openrouter_provider = client.backend = backend
            client.max_tokens = 4096
            client.temperature, client.retries = temp, 1
            fmt = {'type': 'json_object'}
            if arena == 'trade':
                request = client._build_request('test prompt', fmt)
            elif arena == 'auction':
                request = client._request('test prompt', 4096)
            else:
                captured = []
                client.complete = lambda req: (captured.append(req) or Completion('{}', {}, 'stop', 'content'))
                client.record = lambda record: None
                client('test prompt', temp, 4096, fmt)
                request = captured[0]
            results[str((provider, model, effort, temp))] = request
print(json.dumps(results))
'''


class SharedModelsTests(unittest.TestCase):
    def test_frozen_sources_include_shared_configuration(self):
        probes = {
            'multiresource_trade': 'from src.trading import run_pair; assert "config/models.json" in run_pair.source_files()',
            'single_good_bargaining': 'from src import run_pair, verify; assert "config/generation.json" in run_pair.source_files(); assert "config_models.json" in verify.file_hashes()',
            'double_auction/src': 'from double_auction import mixed; assert "config/models.json" in mixed.make_plan("gpt51_sonnet5")["source_sha256"]',
        }
        for path, code in probes.items():
            with self.subTest(path=path):
                env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                       'PYTHONPATH': os.pathsep.join((str(ROOT / path), str(ROOT)))}
                subprocess.run([sys.executable, '-c', code], cwd='/tmp', env=env,
                               capture_output=True, text=True, check=True)

    def test_request_parity(self):
        expected = json.loads((ROOT / 'tests/fixtures/model_requests.json').read_text())
        for arena, path in [('trade', 'multiresource_trade'),
                            ('bargaining', 'single_good_bargaining'),
                            ('auction', 'double_auction/src')]:
            with self.subTest(arena=arena):
                env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                       'PYTHONPATH': os.pathsep.join((str(ROOT / path), str(ROOT)))}
                process = subprocess.run([sys.executable, '-c', REQUEST_PROBE, arena],
                                         cwd='/tmp', env=env, capture_output=True,
                                         text=True, check=True)
                self.assertEqual(json.loads(process.stdout), expected[arena])

    def test_generation_defaults(self):
        self.assertEqual(generation_settings('trade')['max_tokens'], 4096)
        self.assertEqual(generation_settings('trade')['runner_max_tokens'], 2048)
        self.assertEqual(generation_settings('bargaining')['max_tokens'], 4096)
        self.assertEqual(generation_settings('auction')['max_tokens'], 2048)
        self.assertEqual(generation_settings('trade', 'gpt4omini')['reasoning_effort'], 'none')
        for study in ('trade', 'bargaining', 'auction'):
            self.assertIsNone(generation_settings(study)['temperature'])

    def test_catalogs_preserve_rates_and_routing(self):
        self.assertEqual(model_catalog('bargaining')['gemini']['input_usd_per_million'], .375)
        self.assertEqual(model_catalog('mixed')['gemini']['input_price'], .75)
        self.assertEqual(model_catalog('auction')['gemini']['backend'], 'Google')
        changed = model_settings('gemini')
        changed['backend'] = 'other'
        self.assertEqual(model_settings('gemini')['backend'], 'Google')


if __name__ == '__main__':
    unittest.main()
