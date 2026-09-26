"""Replay a completed run and report paired information effects."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from experiment_runtime import read_jsonl

from . import analysis, episode, market
from .prompts import build_prompt
from .providers import summarize_trace


BOOTSTRAP_DRAWS = 50_000
BOOTSTRAP_SEED = 20260817
MARKET_FIELDS = (
    "idx", "regime", "market_family", "market_design", "core_efficient_quantity", "surplus_level",
    "target_marginal_surplus_quantile", "buyers", "sellers",
    "values", "costs", "first_best_welfare", "first_best_trades",
    "efficient_quantity", "mean_first_best_surplus",
    "marginal_first_best_surplus", "matched_draw_index", "buyer_support",
    "seller_support", "support_overlap",
)
OUTCOME_FIELDS = (
    "trades", "welfare", "buyer_surplus", "seller_surplus",
)


def close(left, right):
    """Recursively compare JSON values, allowing tiny float differences."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-10)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(lambda pair: close(*pair), zip(left, right)))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(close(left[key], right[key]) for key in left)
    return left == right


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved_market_field(row, field, config):
    """Read one market field, filling only legacy descriptive metadata.

    The first three-family runs predate the explicit ``market_design`` field.
    Their generator, prompts, and outcomes are otherwise identical.
    """
    if field == "market_design" and field not in row:
        return config.get("market_design", "three_family")
    return row.get(field)


def pooled_efficiency(rows, indices):
    welfare = sum(rows[index]["welfare"] for index in indices)
    first_best = sum(rows[index]["first_best_welfare"] for index in indices)
    return welfare / first_best


def paired_interval(left, right, statistic, draws, seed):
    """Stratified paired bootstrap for one contrast."""
    indices = list(range(len(left)))
    effect = statistic(left, indices) - statistic(right, indices)
    if draws <= 0:
        return effect, None

    strata = {}
    for index, row in enumerate(left):
        stratum = row["market_family"]
        strata.setdefault(stratum, []).append(index)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        sample = [
            group[rng.randrange(len(group))]
            for group in strata.values()
            for _ in group
        ]
        samples.append(statistic(left, sample) - statistic(right, sample))
    samples.sort()
    return effect, [samples[int(.025 * draws)], samples[int(.975 * draws) - 1]]


def contrast(left, right, field, draws, seed):
    if field == "efficiency":
        statistic = pooled_efficiency
    else:
        statistic = lambda rows, indices: (
            sum(rows[index][field] for index in indices) / len(indices)
        )
    effect, interval = paired_interval(left, right, statistic, draws, seed)
    return {
        "effect": effect,
        "stratified_paired_bootstrap_95_interval": interval,
    }


def expected_prompts(row, condition, config):
    """Reconstruct every prompt and provisional round from saved orders."""
    buyers, sellers = row["buyers"], row["sellers"]
    previous_bids = {name: None for name in buyers}
    previous_asks = {name: None for name in sellers}
    feedback = None
    mechanism = config.get("mechanism", "midpoint")
    expected_log, expected_history = [], []

    for round_number, saved_round in enumerate(row["round_history"], start=1):
        submitted_bids = saved_round["submitted_bids"]
        submitted_asks = saved_round["submitted_asks"]
        bids, asks, invalid = market.validate_orders(
            row["values"], row["costs"], submitted_bids, submitted_asks,
        )
        outcome = market.clear_market(
            row["values"], row["costs"], bids, asks, mechanism,
        )
        diagnostics = analysis.order_diagnostics(
            row["values"], row["costs"], bids, asks,
            row["first_best_welfare"], len(row["first_best_trades"]),
        )
        released = None
        if round_number < config["rounds"]:
            released = episode.feedback_message(
                config["feedback_policy"], bids, asks,
                row["values"], row["costs"], mechanism,
            )
        expected_history.append({
            "round": round_number,
            "submitted_bids": submitted_bids,
            "submitted_asks": submitted_asks,
            "bids": bids,
            "asks": asks,
            "invalid_orders": invalid,
            "provisional_outcome": outcome,
            "diagnostics": diagnostics,
            "feedback_released": released,
        })

        for role, agents, previous in (
            ("buyer", buyers, previous_bids),
            ("seller", sellers, previous_asks),
        ):
            for name in agents:
                private_feedback = feedback.get(name) if isinstance(feedback, dict) else feedback
                expected_log.append({
                    "round": round_number,
                    "who": name,
                    "role": role,
                    "prompt": build_prompt(
                        condition, row, name, role,
                        round_number, config["rounds"], config["feedback_policy"],
                        previous[name], private_feedback,
                    ),
                })
        previous_bids, previous_asks, feedback = submitted_bids, submitted_asks, released
    return expected_log, expected_history


def audit_run(run_dir, bootstrap_draws=BOOTSTRAP_DRAWS,
              bootstrap_seed=BOOTSTRAP_SEED):
    """Verify source, markets, prompts, parsing, clearing, and summaries."""
    failures = []

    def check(ok, message, **context):
        if not ok:
            failures.append({"error": message, **context})

    config_path, summary_path = run_dir / "run_config.json", run_dir / "summary.json"
    if not config_path.exists() or not summary_path.exists():
        return {"run": str(run_dir), "passed": False,
                "failures": [{"error": "missing run_config.json or summary.json"}]}

    config, stored_summary = read_json(config_path), read_json(summary_path)
    check(config.get("protocol_version") == market.protocol_version(
        config.get("market_design", "three_family")),
          "protocol version mismatch")
    check(config.get("conditions") == list(market.conditions_for(config["condition_set"])),
          "information conditions mismatch")
    check(stored_summary.get("config") == config, "summary configuration mismatch")

    if not config.get("mock"):
        plan = config.get("frozen_plan")
        check(isinstance(plan, dict), "missing frozen paid-run plan")
        if isinstance(plan, dict):
            for key, value in plan.items():
                if key != "created_utc" and key in config:
                    check(config[key] == value, "frozen plan differs", field=key)

    instances = market.gen_instances(
        config["markets"], config["n_buyers"], config["n_sellers"],
        config["seed"], config.get("market_design", "three_family"),
    )
    cells = {}
    for condition in config["conditions"]:
        path = run_dir / condition / "details.jsonl"
        if not path.exists():
            check(False, "missing condition results", condition=condition)
            continue
        rows = sorted(read_jsonl(path), key=lambda row: row["idx"])
        check([row["idx"] for row in rows] == list(range(config["markets"])),
              "incomplete or duplicate market indices", condition=condition)
        if len(rows) != config["markets"]:
            continue
        cells[condition] = rows

        for index, row in enumerate(rows):
            context = {"condition": condition, "idx": index}
            check(all(close(saved_market_field(row, field, config),
                            instances[index].get(field))
                      for field in MARKET_FIELDS), "market mismatch", **context)

            submitted_bids, submitted_asks = row["submitted_bids"], row["submitted_asks"]
            bids, asks, invalid = market.validate_orders(
                row["values"], row["costs"], submitted_bids, submitted_asks,
            )
            outcome = market.clear_market(
                row["values"], row["costs"], bids, asks,
                config.get("mechanism", "midpoint"),
            )
            diagnostics = analysis.order_diagnostics(
                row["values"], row["costs"], bids, asks,
                row["first_best_welfare"], len(row["first_best_trades"]),
            )
            check(close(row["bids"], bids) and close(row["asks"], asks)
                  and close(row["invalid_orders"], invalid),
                  "order validation mismatch", **context)
            check(all(close(row[field], outcome[field]) for field in OUTCOME_FIELDS),
                  "clearing mismatch", **context)
            check(all(close(row[field], value) for field, value in diagnostics.items()),
                  "diagnostic mismatch", **context)
            check(close(row["efficiency"], outcome["welfare"] / row["first_best_welfare"]),
                  "efficiency mismatch", **context)

            expected_log, expected_history = expected_prompts(row, condition, config)
            check(close(row["round_history"], expected_history),
                  "round replay mismatch", **context)
            check(len(row["log"]) == len(expected_log), "model-call count mismatch", **context)
            for saved, expected in zip(row["log"], expected_log):
                call_context = {**context, "agent": expected["who"], "round": expected["round"]}
                check(all(saved.get(key) == value for key, value in expected.items()),
                      "saved prompt mismatch", **call_context)
                order_name = "bid" if expected["role"] == "buyer" else "ask"
                check(close(saved.get(order_name), market.parse_number(
                    saved.get("response", ""), order_name,
                )), "response parse mismatch", **call_context)

            raw_calls = row.get("raw_calls", [])
            check(config.get("mock") or len(raw_calls) == len(row["log"]),
                  "provider trace count mismatch", **context)
            for saved, raw in zip(row["log"], raw_calls):
                check(not raw.get("error") and raw.get("prompt") == saved.get("prompt")
                      and raw.get("response") == saved.get("response"),
                      "provider trace mismatch", **context, agent=saved.get("who"))
            check(close(row.get("usage"), summarize_trace(raw_calls)),
                  "usage summary mismatch", **context)

    recomputed = {name: analysis.summarize(rows) for name, rows in cells.items()}
    recomputed_families = {
        name: analysis.summarize_by_market_family(rows)
        for name, rows in cells.items()
    }
    check(close(stored_summary.get("conditions"), recomputed),
          "aggregate summary mismatch")
    check(close(stored_summary.get("by_market_family"), recomputed_families),
          "market-family summary mismatch")

    effects, surplus = {}, {}
    for name, left_name, right_name in market.CONTRASTS_BY_CONDITION_SET[config["condition_set"]]:
        if left_name not in cells or right_name not in cells:
            continue
        left, right = cells[left_name], cells[right_name]
        effects[name] = contrast(
            left, right, "efficiency", bootstrap_draws, bootstrap_seed,
        )
        surplus[name] = {
            side: contrast(left, right, f"{side}_surplus", bootstrap_draws, bootstrap_seed)
            for side in ("buyer", "seller")
        }

    return {
        "run": str(run_dir),
        "model": config.get("model"),
        "markets": config.get("markets"),
        "rounds": config.get("rounds"),
        "feedback_policy": config.get("feedback_policy"),
        "conditions": recomputed,
        "by_market_family": recomputed_families,
        "paired_efficiency": effects,
        "paired_surplus": surplus,
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "passed": not failures,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--bootstrap-draws", type=int, default=BOOTSTRAP_DRAWS)
    parser.add_argument("--bootstrap-seed", type=int, default=BOOTSTRAP_SEED)
    args = parser.parse_args()
    result = audit_run(args.run_dir.resolve(), args.bootstrap_draws, args.bootstrap_seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
