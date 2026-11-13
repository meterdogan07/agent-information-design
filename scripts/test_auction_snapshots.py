"""Offline regression tests for automatic single-model auction snapshots."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'experiments/double_auction/src')]
from double_auction import runner, audit
from double_auction.snapshots import verify_snapshot
import experiments


class AuctionSnapshotsTest(unittest.TestCase):
    def test_direct_launcher(self):
        args = experiments.parser().parse_args([
            'main', 'auction', 'gpt51', '--mode', 'execute', '--run-label', 'test',
            '--max-tokens', '4096'])
        cmd, _, _ = experiments.build_command(args)
        self.assertNotIn('--estimated-cost', cmd)
        self.assertIn('execute', cmd)

    def test_paid_authorization_required(self):
        with patch.object(sys, 'argv', ['runner', '--n', '3', '--seed', '97617', '--run-id', 'test']):
            with self.assertRaisesRegex(SystemExit, 'confirm-paid-run'):
                runner.main()

    def test_resume_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ['runner', '--mock', '--n', '3', '--seed', '97617',
                    '--market-design', 'margin_sweep', '--run-id', 'test', '--workers', '1']
            original = runner.run_one
            failed = [False]

            def fail_once(*a, **kw):
                if not failed[0]:
                    failed[0] = True
                    raise RuntimeError('simulated interruption')
                return original(*a, **kw)

            with patch.object(runner, 'PROJECT_ROOT', tmp), patch.object(runner, 'git_revision', return_value=None), contextlib.redirect_stdout(io.StringIO()):
                with patch.object(sys, 'argv', args), patch.object(runner, 'run_one', side_effect=fail_once):
                    with self.assertRaisesRegex(RuntimeError, '1 model episode'):
                        runner.main()
                directory = next(Path(tmp).rglob('run_config.json')).parent
                config = json.loads((directory / 'run_config.json').read_text())
                self.assertIsNone(config['estimated_cost_usd'])
                self.assertFalse(any('.env' in name for name in config['source_sha256']))
                verify_snapshot(directory, config)
                with patch.object(sys, 'argv', args + ['--resume']), patch.object(runner, 'run_one', wraps=original) as calls:
                    runner.main()
                    self.assertEqual(calls.call_count, 1)
                result = audit.audit_run(directory, bootstrap_draws=20)
                self.assertTrue(result['passed'], result)
                with patch.object(sys, 'argv', args + ['--resume']):
                    with self.assertRaises(FileExistsError):
                        runner.main()
                path = directory / 'markets.jsonl'
                path.write_bytes(path.read_bytes() + b'\n')
                with self.assertRaisesRegex(ValueError, 'market snapshot'):
                    verify_snapshot(directory, config)

    def test_mismatch_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run'
            config = {'model': 'test', 'source_sha256': {'a.py': 'abc'}, 'markets_sha256': 'xyz'}
            runner.prepare_output_directory(str(path), config)
            for field in ('model', 'source_sha256', 'markets_sha256'):
                altered = {**config, field: 'changed'}
                with self.assertRaisesRegex(ValueError, field):
                    runner.prepare_output_directory(str(path), altered, resume=True)
            legacy = Path(tmp) / 'plan.json'
            legacy.write_text(json.dumps({'model': 'test', 'created_utc': 'then'}))
            with patch.object(runner, 'require_committed') as checked:
                _, plan = runner.load_frozen_plan(str(legacy), {'model': 'test'})
                self.assertEqual(plan['model'], 'test')
                self.assertGreater(checked.call_count, 1)
                with self.assertRaises(SystemExit):
                    runner.load_frozen_plan(str(legacy), {'model': 'different'})


if __name__ == '__main__':
    unittest.main()
