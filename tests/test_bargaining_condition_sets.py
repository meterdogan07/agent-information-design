"""Offline checks for the optional three-condition study and old defaults."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
       'PYTHONPATH': os.pathsep.join((str(ROOT / 'single_good_bargaining'), str(ROOT)))}


class ConditionSetTests(unittest.TestCase):
    def run_python(self, code, *args):
        return subprocess.run([sys.executable, '-c', code, *args], cwd='/tmp',
                              env=ENV, text=True, capture_output=True, check=True)

    def test_estimates_and_preserved_defaults(self):
        self.run_python('''
from src import run, design
args = (30, 6, 2., 10., 800, 1300)
assert run.estimate_cost(*args)['episodes'] == 150
estimate = run.estimate_cost(*args, conditions=design.MAIN_CONDITIONS)
assert estimate['episodes'] == 90 and estimate['maximum_calls'] == 540
assert abs(estimate['estimated_cost_usd'] - 7.884) < 1e-9
''')

    def test_mock_execution_audit_analysis_and_resume(self):
        # Reduce bootstrap draws for this test only; no paid client is constructed.
        entry = '''
from unittest.mock import patch
from src import run
with patch('src.verify._bootstrap', lambda values: [min(values), max(values)] if values else None):
    run.main()
'''
        with tempfile.TemporaryDirectory(prefix='bargaining-conditions-') as temp:
            for condition_set, markets, expected in [('main', '30', 90), ('all', '6', 30)]:
                folder = Path(temp) / condition_set
                args = ('--mock', '--model-preset', 'gpt6sol', '--condition-set', condition_set,
                        '--messages', '6', '--markets', markets,
                        '--panel', 'five_per_profitable_stratum_both_starters_v1',
                        '--results-dir', str(folder))
                self.run_python(entry, *args)
                config = json.loads((folder / 'config.json').read_text())
                audit = json.loads((folder / 'audit.json').read_text())
                analysis = json.loads((folder / 'analysis.json').read_text())
                self.assertEqual(config['expected_episodes'], expected)
                self.assertTrue(audit['passed'], audit['errors'])
                self.assertTrue(audit['frozen_source_verified'])
                self.assertEqual(set(analysis['cells']), set(config['conditions']))
                self.assertEqual(len(analysis['paired_contrasts']), 3 if condition_set == 'main' else 7)
                self.assertEqual(len(analysis['first_mover_interactions']), 3 if condition_set == 'main' else 7)
                before = (folder / 'episodes.jsonl').read_bytes()
                self.run_python(entry, *args)
                self.assertEqual((folder / 'episodes.jsonl').read_bytes(), before)
                self.run_python('''
import json, sys
from pathlib import Path
from src.verify import audit_run
p = Path(sys.argv[1])
lines = (p / 'episodes.jsonl').read_text().splitlines()
(p / 'episodes.jsonl').write_text('\\n'.join(lines[:-1]) + '\\n')
assert not audit_run(p)['passed']
config = json.loads((p / 'config.json').read_text())
config['conditions'] = ['hidden', 'hidden', 'exact']
(p / 'config.json').write_text(json.dumps(config))
assert not audit_run(p)['passed']
''', str(folder))


if __name__ == '__main__':
    unittest.main()
