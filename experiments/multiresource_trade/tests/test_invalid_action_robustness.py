import copy
import unittest
from unittest.mock import patch

from src.analysis.invalid_action_robustness import analyze_invalid_actions
from src.analysis.metrics import summarize_cell


def episode(market, condition, welfare, errors=None, replication=0):
    return {"status": "completed", "max_messages": 8, "first_actor": "A",
            "market": {"market_id": market}, "replication": replication,
            "condition": condition, "outcome": {"normalized_welfare": welfare},
            "transcript": [{"errors": errors or []}]}


class RobustnessTests(unittest.TestCase):
    def analyze(self, rows):
        with patch("src.analysis.invalid_action_robustness.extraction_group",
                   return_value=("b_higher", 0.1)):
            return analyze_invalid_actions(rows)["h8_first_A"]

    def test_matched_exclusion_and_least_does_not_trigger_exclusion(self):
        rows = [episode("bad", "rank_only", .2, ["bad_action", "bad_quantity"]),
                episode("bad", "exact_values", .8),
                episode("good", "rank_only", .4),
                episode("good", "exact_values", .6),
                episode("good", "inventory_only", 0, ["bad_action"])]
        original = copy.deepcopy(rows)
        result = self.analyze(rows)
        self.assertEqual(rows, original)
        counts = result["counts_by_condition"]["rank_only"]
        self.assertEqual(counts["invalid_actions"], 1)
        self.assertEqual(counts["negotiations_with_invalid_actions"], 1)
        for group in ("overall", "b_higher"):
            summary = result["welfare_by_group"][group]
            self.assertEqual(summary["excluded_pairs"], 1)
            clean = summary["excluding_invalid_pairs"]
            self.assertEqual(clean["matched_pairs"], 1)
            self.assertAlmostEqual(clean["full_minus_coarse_W"], .2)

    def test_full_error_and_empty_filtered_group(self):
        result = self.analyze([episode("x", "rank_only", .4),
                               episode("x", "exact_values", .6, ["bad_action"])])
        self.assertIsNone(result["welfare_by_group"]["overall"]
                          ["excluding_invalid_pairs"]["full_minus_coarse_W"])

    def test_reject_duplicate_unmatched_and_incomplete_runs(self):
        coarse = episode("x", "rank_only", .4)
        full = episode("x", "exact_values", .6)
        for rows in ([coarse], [coarse, full, coarse],
                     [dict(coarse, status="failed"), full]):
            with self.assertRaises(ValueError):
                self.analyze(rows)

    def test_replications_are_matched_separately(self):
        rows = [episode("x", c, .4, ["bad"] if r == 0 else [], replication=r)
                for c in ("rank_only", "exact_values") for r in (0, 1)]
        clean = self.analyze(rows)["welfare_by_group"]["overall"]["excluding_invalid_pairs"]
        self.assertEqual(clean["matched_pairs"], 1)

    def test_primary_summary_counts_errors_without_excluding_welfare(self):
        rows = [episode("x", "rank_only", .2, ["bad", "bad_quantity"]),
                episode("y", "rank_only", .8)]
        for row in rows:
            row["outcome"].update(agreement=True, individually_rational=True,
                                  gain_a=1, gain_b=1)
        with patch("src.analysis.metrics.first_best_gain", return_value=2):
            summary = summarize_cell(rows)
        self.assertEqual(summary["episodes"], 2)
        self.assertEqual(summary["invalid_actions"], 1)
        self.assertEqual(summary["episodes_with_invalid_actions"], 1)
        self.assertAlmostEqual(summary["welfare"], .5)


if __name__ == "__main__":
    unittest.main()
