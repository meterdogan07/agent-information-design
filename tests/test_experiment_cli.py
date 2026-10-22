"""Offline checks for the common launcher, including paid-run guards."""
import unittest
from unittest.mock import patch

import experiments


class LauncherTests(unittest.TestCase):
    def build(self, *arguments):
        args = experiments.parser().parse_args(["main", *arguments])
        return experiments.build_command(args, {"PATH": "/usr/bin"})

    def test_preview_is_default_for_every_game(self):
        for game in ("trade", "bargaining", "auction"):
            command, cwd, env = self.build(game, "gpt51")
            self.assertIn("preview", command)
            self.assertTrue(cwd.is_dir())
            self.assertTrue(env["PYTHONPATH"].startswith(str(experiments.ROOT)))

    def test_bargaining_main_has_three_conditions(self):
        command, _, _ = self.build("bargaining", "gpt51", "--messages", "4")
        self.assertEqual(command[command.index("--condition-set") + 1], "main")
        self.assertEqual(command[command.index("--messages") + 1], "4")

    def test_auction_uses_paper_market_panel(self):
        command, _, _ = self.build("auction", "gpt51")
        self.assertIn("margin", command)
        self.assertEqual(command[command.index("--seed") + 1], "97617")

    def test_paid_guards(self):
        for arguments in (("bargaining", "gpt51", "--mode", "execute"),
                          ("auction", "gpt51", "--mode", "execute"),
                          ("auction", "gpt51", "--mode", "freeze")):
            with self.assertRaises(ValueError):
                self.build(*arguments)

    def test_unsupported_options_fail_instead_of_being_ignored(self):
        for arguments in (("trade", "gpt51", "--budget", "5"),
                          ("auction", "gpt51", "--messages", "4"),
                          ("trade", "gpt51", "--mode", "freeze"),
                          ("auction", "gpt51", "--resume"),
                          ("trade", "gpt51", "--workers", "0"),
                          ("bargaining", "gpt51", "--budget", "nan")):
            with self.assertRaises(ValueError):
                self.build(*arguments)

    def test_resume_preserves_auction_guard(self):
        command, _, _ = self.build("auction", "gpt51", "--mode", "execute",
                                  "--run-label", "example", "--estimated-cost", "5", "--resume")
        self.assertIn("--resume", command)
        self.assertNotIn("--confirm-paid-run", command)  # Native CLI validates the plan.

    def test_child_failure_propagates(self):
        with patch("experiments.subprocess.run") as run:
            run.return_value.returncode = 7
            self.assertEqual(experiments.main(["main", "trade", "gpt51"]), 7)


if __name__ == "__main__":
    unittest.main()
