"""Check post-run audit paths without executing experiments or API calls."""

import contextlib
import io
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from double_auction import cli


class AuditPathTests(unittest.TestCase):
    def test_audit_uses_runner_sanitization(self):
        preset = next(iter(cli.ARMS))
        for run_id, expected_suffix in (
            ("pilot run/2", "pilot_run_2"),
            ("pilot run", "pilot_run"),
            ("pilot_run-2.0", "pilot_run-2.0"),
            (None, f"{preset}_s123"),
        ):
            with self.subTest(run_id=run_id):
                args = Namespace(action="execute", preset=preset, seed=123,
                                 run_id=run_id, model="gpt-5.1")
                runner_command = ["offline-runner-placeholder"]
                with patch.object(cli, "ROOT", Path("/offline-test")), \
                     patch.object(cli, "runner_command", return_value=runner_command), \
                     patch.object(cli, "package_environment", return_value={}), \
                     patch.object(cli.subprocess, "run") as run, \
                     contextlib.redirect_stdout(io.StringIO()):
                    cli.run_arm(args)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0], runner_command)
                expected_path = (
                    Path("/offline-test/experiments") / cli.market.EXPERIMENT_DIR_NAME
                    / f"{cli.market.model_slug(args.model)}_{expected_suffix}"
                )
                self.assertEqual(run.call_args_list[1].args[0],
                                 [sys.executable, "-m", "double_auction.audit",
                                  str(expected_path)])
                self.assertTrue(run.call_args_list[1].kwargs["check"])
                self.assertEqual(args.run_id, run_id)


if __name__ == "__main__":
    unittest.main()
