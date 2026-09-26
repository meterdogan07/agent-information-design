"""Dry-by-default, resumable runner for one model and one horizon."""

from __future__ import annotations

from experiment_runtime.models import generation_settings

import argparse
import concurrent.futures
import datetime as dt
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable

from experiment_runtime import read_jsonl, run_directory_lock, sha256_text

from . import design, game
from .models import ModelClient, load_env, summarize_trace, validate_trace
from .verify import (
    analyze_run, audit_run, file_hashes, freeze_source, render_readable,
)

GENERATION = generation_settings("bargaining")


REPOSITORY = Path(__file__).resolve().parents[1]
# Capture the source immediately after imports. Before any paid call is made,
# freeze_source verifies that the on-disk files still match this startup view.
STARTUP_CODE_SHA256 = file_hashes()


def canonical_line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True) + "\n"


def estimate_cost(
    markets: int,
    horizon: int,
    input_price: float,
    output_price: float,
    input_tokens_per_call: int,
    output_tokens_per_call: int,
) -> dict[str, Any]:
    maximum_calls = markets * len(design.CONDITIONS) * horizon
    estimated = maximum_calls * (
        input_tokens_per_call * input_price
        + output_tokens_per_call * output_price
    ) / 1_000_000
    return {
        "markets": markets,
        "conditions": len(design.CONDITIONS),
        "episodes": markets * len(design.CONDITIONS),
        "maximum_calls": maximum_calls,
        "estimated_input_tokens_per_call": input_tokens_per_call,
        "estimated_output_tokens_per_call": output_tokens_per_call,
        "input_usd_per_million": input_price,
        "output_usd_per_million": output_price,
        "estimated_cost_usd": estimated,
        "recommended_authorization_usd": math.ceil(estimated * 150) / 100,
    }


def actual_cost(row: dict[str, Any], config: dict[str, Any]) -> float:
    usage = row.get("usage") or {}
    estimate = config["cost_estimate"]
    return (
        int(usage.get("input_tokens", 0)) * estimate["input_usd_per_million"]
        + int(usage.get("output_tokens", 0)) * estimate["output_usd_per_million"]
    ) / 1_000_000


def run_one(
    progress: int,
    total: int,
    market: dict[str, Any],
    condition: str,
    model: ModelClient | Callable[..., str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    raw_calls: list[dict[str, Any]] = []
    episode_id = f"{market['market_id']}::{condition}"
    try:
        if hasattr(model, "start_trace"):
            model.start_trace()  # type: ignore[attr-defined]
        row = game.run_episode(
            market,
            condition,
            model,
            horizon=int(config["horizon"]),
            max_tokens=int(config["max_tokens"]),
        )
        row.update({"episode_id": episode_id, "status": "completed"})
        success = True
    except Exception as exc:
        row = {
            "protocol_version": design.PROTOCOL_VERSION,
            "prompt_version": design.PROMPT_VERSION,
            "episode_id": episode_id,
            "condition": condition,
            "signal_by_actor": design.signals_for(condition),
            "market": market,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        success = False
    finally:
        if hasattr(model, "end_trace"):
            raw_calls = model.end_trace()  # type: ignore[attr-defined]
    row["raw_api_calls"] = raw_calls
    row["usage"] = summarize_trace(raw_calls)
    if success and config["provider"] != "mock":
        error = validate_trace(
            raw_calls,
            len(row["outcome"]["calls"]),
            str(config["provider"]),
            config.get("backend"),
            str(config["model"]),
            str(config["reasoning_effort"]),
            int(config["max_tokens"]),
        )
        if error:
            row["status"] = "failed"
            row["error"] = error
            success = False
    row["actual_cost_usd"] = actual_cost(row, config)
    row["manifest"] = {
        "market_sha256": sha256_text(game.canonical_json(market)),
        "provider": config["provider"],
        "model": config["model"],
        "backend": config.get("backend"),
        "code_sha256": config["code_sha256"],
    }
    row["progress_index"] = progress
    row["progress_total"] = total
    return row, success


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-preset", choices=tuple(design.MODEL_PRESETS), default="gpt51")
    parser.add_argument("--messages", "--horizon", dest="horizon", type=int,
                        choices=design.HORIZONS, default=2,
                        help="Maximum messages per negotiation, counting both agents' turns.")
    parser.add_argument("--markets", type=int, default=design.MARKET_COUNT)
    parser.add_argument("--panel", choices=design.PANEL_IDS, default=design.PANEL_ID)
    parser.add_argument("--seed", type=int, default=design.MARKET_SEED)
    parser.add_argument("--max-tokens", type=int, default=GENERATION["max_tokens"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=GENERATION["attempts"])
    parser.add_argument("--results-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--estimated-input-tokens-per-call", type=int, default=800)
    parser.add_argument("--estimated-output-tokens-per-call", type=int, default=1300)
    parser.add_argument("--budget", "--authorized-max-cost-usd",
                        dest="authorized_max_cost_usd", type=float,
                        help="Authorized dollar ceiling; required with --execute.")
    args = parser.parse_args()
    if args.markets <= 0 or args.max_tokens <= 0 or args.workers <= 0 or args.retries <= 0:
        raise SystemExit("markets, max-tokens, workers, and retries must be positive")

    preset = design.MODEL_PRESETS[args.model_preset]
    input_price = (
        float(args.input_usd_per_million)
        if args.input_usd_per_million is not None
        else float(preset["input_usd_per_million"])
    )
    output_price = (
        float(args.output_usd_per_million)
        if args.output_usd_per_million is not None
        else float(preset["output_usd_per_million"])
    )
    markets = design.generate_markets(args.markets, args.seed, args.panel)
    market_count = len(markets)
    market_text = "".join(canonical_line(value) for value in markets)
    estimate = estimate_cost(
        market_count,
        args.horizon,
        input_price,
        output_price,
        args.estimated_input_tokens_per_call,
        args.estimated_output_tokens_per_call,
    )
    config = {
        "protocol_version": design.PROTOCOL_VERSION,
        "prompt_version": design.PROMPT_VERSION,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "offline_mock" if args.mock else "paid_exploratory",
        "provider": "mock" if args.mock else preset["provider"],
        "model_preset": "mock" if args.mock else args.model_preset,
        "model": "deterministic_mock" if args.mock else preset["model"],
        "backend": None if args.mock else preset["backend"],
        "reasoning_effort": None if args.mock else preset["reasoning_effort"],
        "pricing_verified_on": None if args.mock else preset["pricing_verified_on"],
        "conditions": list(design.CONDITIONS),
        "condition_signals": design.CONDITION_SIGNALS,
        "horizon": args.horizon,
        "max_tokens": args.max_tokens,
        "expected_markets": market_count,
        "expected_episodes": market_count * len(design.CONDITIONS),
        "seed": args.seed,
        "prior_id": design.PRIOR_ID,
        "panel_id": args.panel,
        "markets_sha256": sha256_text(market_text),
        "code_sha256": STARTUP_CODE_SHA256,
        "workers": args.workers,
        "retries": args.retries,
        "cost_estimate": estimate,
        "authorized_max_cost_usd": args.authorized_max_cost_usd,
        "paid_run_confirmed": bool(args.execute),
    }
    print("Self-contained single-good bargaining experiment")
    print(f"  model: {config['provider']} / {config['model']}")
    if config["backend"]:
        print(f"  backend: {config['backend']} (fallback disabled)")
    print(
        f"  design: {market_count} markets x {len(design.CONDITIONS)} conditions "
        f"at H{args.horizon} = {config['expected_episodes']} episodes"
    )
    counts = {family: sum(market["family"] == family for market in markets) for family in design.FAMILIES}
    print(f"  market labels: {counts}")
    starters = {
        mover: sum(market["first_mover"] == mover for market in markets)
        for mover in ("seller", "buyer")
    }
    print(f"  starting order: {starters}; panel: {args.panel}")
    print(
        f"  pricing: ${input_price:g}/M input, ${output_price:g}/M output "
        f"(verified {preset['pricing_verified_on']})"
    )
    print(
        f"  estimated maximum-call cost: ${estimate['estimated_cost_usd']:.4f}; "
        f"recommended --budget: ${estimate['recommended_authorization_usd']:.2f}"
    )
    if not args.mock and not args.execute:
        print("DRY RUN: no files and no provider calls.")
        return
    if args.execute:
        if input_price <= 0 or output_price <= 0:
            raise SystemExit("paid execution requires positive preset prices")
        if args.authorized_max_cost_usd is None:
            raise SystemExit("paid execution requires an explicit dollar ceiling")
        if estimate["recommended_authorization_usd"] > args.authorized_max_cost_usd:
            raise SystemExit("estimated budget exceeds the authorized ceiling")
        load_env()
        model: ModelClient | Callable[..., str] = ModelClient(
            provider=str(preset["provider"]),
            model=str(preset["model"]),
            backend=preset["backend"],
            reasoning_effort=str(preset["reasoning_effort"]),
            retries=args.retries,
        )
    else:
        model = game.make_mock_model()

    results_root = REPOSITORY / "results"
    if args.panel != design.PANEL_ID:
        results_root = results_root / args.panel
    default_dir = (
        results_root / f"seed{args.seed}_n{market_count}" / f"h{args.horizon}"
        / (args.model_preset if not args.mock else "mock")
    )
    results_dir = (args.results_dir or default_dir).resolve()
    with run_directory_lock(results_dir):
        results_dir.mkdir(parents=True, exist_ok=True)
        config_path = results_dir / "config.json"
        markets_path = results_dir / "markets.jsonl"
        episodes_path = results_dir / "episodes.jsonl"
        failures_path = results_dir / "failed_episodes.jsonl"
        immutable = (
            "protocol_version", "prompt_version", "provider", "model_preset", "model",
            "backend", "reasoning_effort", "conditions", "condition_signals", "horizon",
            "max_tokens", "expected_markets", "expected_episodes", "seed", "prior_id",
            "panel_id", "markets_sha256", "code_sha256", "pricing_verified_on",
            "cost_estimate", "authorized_max_cost_usd", "workers", "retries",
        )
        if config_path.exists():
            old = json.loads(config_path.read_text())
            mismatch = [key for key in immutable if old.get(key) != config.get(key)]
            if mismatch:
                raise RuntimeError("existing run differs in: " + ", ".join(mismatch))
            config = old
        else:
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
        freeze_source(results_dir, config["code_sha256"])
        if markets_path.exists() and markets_path.read_text() != market_text:
            raise RuntimeError("existing frozen markets differ")
        if not markets_path.exists():
            markets_path.write_text(market_text)

        existing = read_jsonl(episodes_path)
        completed = {row["episode_id"] for row in existing}
        if len(existing) != len(completed):
            raise RuntimeError("duplicate completed episode IDs")
        previous_failures = read_jsonl(failures_path)
        spent = sum(float(row.get("actual_cost_usd", 0)) for row in existing + previous_failures)
        specs = []
        for market in markets:
            shift = market["idx"] % len(design.CONDITIONS)
            order = design.CONDITIONS[shift:] + design.CONDITIONS[:shift]
            for condition in order:
                if f"{market['market_id']}::{condition}" not in completed:
                    specs.append((market, condition))
        print(f"Running {len(specs)} pending episodes with {args.workers} workers.")
        next_spec = 0
        futures: dict[concurrent.futures.Future, int] = {}
        buffer = max(0.03, .05 * float(args.authorized_max_cost_usd or 0))
        launch_limit = (
            float(args.authorized_max_cost_usd) - buffer if args.execute else float("inf")
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            while next_spec < len(specs) or futures:
                while next_spec < len(specs) and len(futures) < args.workers and spent < launch_limit:
                    market, condition = specs[next_spec]
                    future = pool.submit(
                        run_one, next_spec + 1, len(specs), market, condition, model, config
                    )
                    futures[future] = next_spec + 1
                    next_spec += 1
                if not futures:
                    break
                done, _ = concurrent.futures.wait(
                    futures, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    futures.pop(future)
                    row, success = future.result()
                    spent += float(row.get("actual_cost_usd", 0))
                    target = episodes_path if success else failures_path
                    with target.open("a") as handle:
                        handle.write(canonical_line(row))
                    if success:
                        completed.add(row["episode_id"])
                        detail = (
                            f"{row['outcome']['termination']}; "
                            f"welfare={row['metrics']['normalized_welfare']:.3f}"
                        )
                    else:
                        detail = "FAILED; " + row.get("error", "unknown error")
                    print(
                        f"[{row['progress_index']}/{len(specs)}] {row['episode_id']}: "
                        f"{detail}; spent=${spent:.4f}",
                        flush=True,
                    )
        if args.execute and spent > float(args.authorized_max_cost_usd):
            raise SystemExit("recorded spend exceeded authorization; no more calls launched")
        if len(completed) != config["expected_episodes"]:
            print(
                f"Incomplete: {len(completed)}/{config['expected_episodes']} episodes. "
                "All completed and failed attempts were preserved.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        audit = audit_run(results_dir)
        (results_dir / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
        if not audit["passed"]:
            raise SystemExit("audit failed; inspect audit.json")
        analysis = analyze_run(results_dir)
        (results_dir / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
        readable = render_readable(results_dir)
        print(f"Audit passed: {audit['episodes']} episodes; cost=${spent:.4f}")
        for condition, cell in analysis["cells"].items():
            print(
                f"  {condition:16s} welfare={cell['mean_normalized_welfare']:.3f} "
                f"agreement={cell['agreement_rate']:.3f}"
            )
        print(f"Readable transcripts: {readable}")


if __name__ == "__main__":
    main()
