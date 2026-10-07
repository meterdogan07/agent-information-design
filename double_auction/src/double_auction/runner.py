"""Experiment planning and execution for the canonical double-auction package."""

from __future__ import annotations

from experiment_runtime.models import CONFIG_FILES, generation_settings, model_settings

import argparse
import json
import math
import os
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from experiment_runtime import run_directory_lock

from .market import (
    CONDITION_SETS,
    DEFAULT_MARKETS,
    DEFAULT_N_BUYERS,
    DEFAULT_N_SELLERS,
    EXPERIMENT_DIR_NAME,
    FEEDBACK_POLICIES, MARKET_DESIGNS,
    PROJECT_ROOT,
    TEMPERATURE_POLICY,
    conditions_for,
    gen_instances,
    load_env,
    model_slug,
    protocol_version,
)
from .analysis import summarize, summarize_by_market_family
from .episode import make_mock_llm, run_one
from .prompts import build_prompt
from .providers import make_llm
from .render import render

GENERATION = generation_settings("auction")


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SOURCE_FILES = tuple(
    os.path.join(PACKAGE_DIR, name)
    for name in (
        "market.py", "prompts.py", "episode.py", "analysis.py",
        "runner.py", "audit.py", "providers.py",
    )
)
RUNTIME_DIR = Path(PACKAGE_DIR).parents[2] / "experiment_runtime"
SOURCE_FILES = LOCAL_SOURCE_FILES + tuple(
    str(RUNTIME_DIR / name)
    for name in ("__init__.py", "provider.py", "artifacts.py", "models.py")
) + tuple(str(path) for path in CONFIG_FILES)


def run_plan(args, model_calls):
    """Return the immutable specification that must precede a paid run."""
    return {
        "plan_version": 1,
        "protocol_version": protocol_version(args.market_design),
        "run_id": args.run_id,
        "provider": args.provider,
        "model": args.model,
        "openrouter_provider": args.openrouter_provider,
        "reasoning_effort": args.reasoning_effort,
        "temperature": TEMPERATURE_POLICY,
        "max_tokens": args.max_tokens,
        "mechanism": args.mechanism,
        "rounds": args.rounds,
        "feedback_policy": args.feedback_policy,
        "seed": args.seed,
        "markets": args.n,
        "n_buyers": args.n_buyers,
        "n_sellers": args.n_sellers,
        "market_design": args.market_design,
        "condition_set": args.condition_set,
        "conditions": list(conditions_for(args.condition_set)),
        "planned_model_calls": model_calls,
        "execution_order": "seeded_interleaved_conditions",
        "estimated_cost_usd": args.estimated_cost_usd,
    }


def write_plan(path, plan):
    """Write once: a plan must never be silently replaced after inspection."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "x") as fh:
        json.dump({**plan, "created_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh, indent=1)


def git_revision():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def require_committed(path):
    """Require a file to be tracked and unchanged without policing other work."""
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=PROJECT_ROOT, text=True
    ).strip()
    absolute = os.path.realpath(path)
    if os.path.commonpath([root, absolute]) != root:
        raise SystemExit(f"frozen input is outside the repository: {absolute}")
    relative = os.path.relpath(absolute, root)
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", relative], cwd=root, text=True
    ).strip()
    if tracked.returncode or dirty:
        raise SystemExit(
            f"paid runs require a committed, unchanged file: {relative}"
        )


def load_frozen_plan(path, expected):
    """Load a committed plan and require an exact scientific match."""
    if not path:
        raise SystemExit("paid runs require --plan-file")
    absolute = os.path.abspath(path)
    require_committed(absolute)
    for source in SOURCE_FILES:
        require_committed(source)
    with open(absolute) as fh:
        saved = json.load(fh)
    mismatches = [key for key, value in expected.items() if saved.get(key) != value]
    if mismatches:
        raise SystemExit(
            "frozen plan does not match this run: " + ", ".join(mismatches)
        )
    return absolute, saved


RUN_IDENTITY_FIELDS = (
    "protocol_version", "run_id", "provider", "model", "openrouter_provider",
    "reasoning_effort", "temperature",
    "max_tokens", "mechanism", "rounds", "feedback_policy", "seed", "markets",
    "n_buyers", "n_sellers", "market_design", "condition_set", "conditions", "mock",
    "planned_model_calls",
    "execution_order",
    "estimated_cost_usd", "paid_run_authorized",
)
INSTANCE_FIELDS = (
    "regime", "market_family", "market_design", "core_efficient_quantity", "surplus_level",
    "target_marginal_surplus_quantile", "buyers", "sellers", "values", "costs",
    "first_best_welfare",
    "first_best_trades", "efficient_quantity", "mean_first_best_surplus",
    "marginal_first_best_surplus", "matched_draw_index", "buyer_support",
    "seller_support", "support_overlap",
)


def prepare_output_directory(out_root, config, resume=False):
    """Create a new run directory or safely reopen an incomplete identical run."""
    config_path = os.path.join(out_root, "run_config.json")
    summary_path = os.path.join(out_root, "summary.json")

    # Acquiring the run lock creates the directory before configuration is saved.
    # A lock-only directory has no previous run to overwrite.
    if not os.path.exists(out_root) or set(os.listdir(out_root)) == {".run.lock"}:
        os.makedirs(out_root, exist_ok=True)
        with open(config_path, "x") as fh:
            json.dump(config, fh, indent=1)
        return config

    if not resume:
        raise FileExistsError(
            f"run directory already exists: {out_root}; choose a new --run-id "
            "or use --resume for an incomplete identical run"
        )
    if os.path.exists(summary_path):
        raise FileExistsError(
            f"run is already complete and immutable: {out_root}; choose a new --run-id"
        )
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"cannot resume {out_root}: run_config.json is missing"
        )

    with open(config_path) as fh:
        saved = json.load(fh)
    mismatches = [
        field for field in RUN_IDENTITY_FIELDS if saved.get(field) != config.get(field)
    ]
    if mismatches:
        raise ValueError(
            "refusing to mix configurations while resuming; mismatched fields: "
            + ", ".join(mismatches)
        )
    return saved


def completed_indices(path, condition, instances, config):
    """Validate existing partial rows before treating them as resumable work."""
    if not os.path.exists(path):
        return set()
    rows = [json.loads(line) for line in open(path) if line.strip()]
    indices = [row.get("idx") for row in rows]
    if len(indices) != len(set(indices)):
        raise ValueError(f"cannot resume {path}: duplicate market indices")

    done = set()
    for row in rows:
        idx = row.get("idx")
        if not isinstance(idx, int) or not 0 <= idx < len(instances):
            raise ValueError(f"cannot resume {path}: invalid market index {idx!r}")
        if row.get("protocol_version") != config["protocol_version"]:
            raise ValueError(f"cannot resume {path}: protocol version mismatch")
        if row.get("condition") != condition:
            raise ValueError(f"cannot resume {path}: condition mismatch at index {idx}")
        for field in ("mechanism", "rounds", "feedback_policy", "temperature", "max_tokens"):
            if row.get(field) != config[field]:
                raise ValueError(
                    f"cannot resume {path}: {field} mismatch at index {idx}"
                )
        expected = instances[idx]
        mismatches = [
            field for field in INSTANCE_FIELDS
            if row.get(field) != expected.get(field)
        ]
        if mismatches:
            raise ValueError(
                f"cannot resume {path}: market {idx} differs in " + ", ".join(mismatches)
            )
        done.add(idx)
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default="openai",
                    choices=["openrouter", "openai", "anthropic"])
    ap.add_argument("--model", default=model_settings("gpt51")["model"])
    ap.add_argument("--openrouter-provider",
                    help="fixed OpenRouter backend; disables fallback routing")
    ap.add_argument("--reasoning-effort", default=GENERATION["reasoning_effort"],
                    choices=["none", "low", "medium", "high"])
    ap.add_argument("--max-tokens", type=int, default=GENERATION["max_tokens"])
    ap.add_argument("--n", type=int, default=DEFAULT_MARKETS)
    ap.add_argument("--n-buyers", type=int, default=DEFAULT_N_BUYERS)
    ap.add_argument("--n-sellers", type=int, default=DEFAULT_N_SELLERS)
    ap.add_argument("--market-design", default="three_family",
                    choices=list(MARKET_DESIGNS))
    ap.add_argument("--mechanism", default="midpoint",
                    choices=["midpoint", "uniform_price"])
    ap.add_argument("--seed", type=int, required=True,
                    help="precommitted fresh-market seed")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--feedback-policy", default="none",
                    choices=list(FEEDBACK_POLICIES))
    ap.add_argument("--condition-set", default="endpoint_precision",
                    choices=list(CONDITION_SETS))
    ap.add_argument("--run-id", default="")
    ap.add_argument("--resume", action="store_true",
                    help="resume an incomplete run only when its configuration matches")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the run plan and one prompt per condition, then exit")
    ap.add_argument("--write-plan",
                    help="with --dry-run, create a frozen plan at this path")
    ap.add_argument("--plan-file",
                    help="committed frozen plan required for a paid run")
    ap.add_argument("--confirm-paid-run", action="store_true")
    ap.add_argument("--estimated-cost-usd", type=float,
                    help="written pre-run cost estimate; required for a paid run")
    args = ap.parse_args()

    if args.n <= 0:
        raise ValueError("--n must be positive")
    if args.market_design in {"three_family", "overlap_sweep"} and args.n % 3:
        raise ValueError("--n must be divisible by 3 to balance market families")
    if args.market_design in {"margin_sweep", "margin_sweep_mirrored"} and args.n % 3:
        raise ValueError("--n must be divisible by 3 to balance quantity and margin")
    if args.workers <= 0 or args.max_tokens <= 0:
        raise ValueError("--workers and --max-tokens must be positive")
    if args.n_buyers <= 0 or args.n_sellers <= 0:
        raise ValueError("--n-buyers and --n-sellers must be positive")
    if (args.market_design == "size_scaling") != (args.mechanism == "uniform_price"):
        raise ValueError("size_scaling and uniform_price must be used together")
    if args.openrouter_provider and args.provider != "openrouter":
        raise ValueError("--openrouter-provider requires --provider openrouter")
    if args.rounds == 1 and args.feedback_policy != "none":
        raise ValueError("one-shot runs require --feedback-policy none")
    if args.write_plan and not args.dry_run:
        raise SystemExit("--write-plan is only valid with --dry-run")
    if (args.write_plan or not args.dry_run) and not args.run_id:
        raise SystemExit("--run-id is required")
    if args.write_plan and (args.estimated_cost_usd is None
                            or not math.isfinite(args.estimated_cost_usd)
                            or args.estimated_cost_usd < 0):
        raise SystemExit("--write-plan requires a nonnegative --estimated-cost-usd")
    instances = gen_instances(
        args.n, n_buyers=args.n_buyers, n_sellers=args.n_sellers,
        seed=args.seed, market_design=args.market_design,
    )
    conditions = conditions_for(args.condition_set)
    model_calls = args.n * (args.n_buyers + args.n_sellers) * args.rounds * len(conditions)
    plan = run_plan(args, model_calls)

    if args.dry_run:
        print(json.dumps({**plan,
            "regime_counts": {
                regime: sum(i["regime"] == regime for i in instances)
                for regime in sorted({i["regime"] for i in instances})
            },
            "surplus_level_counts": {
                level: sum(i["surplus_level"] == level for i in instances)
                for level in sorted({i["surplus_level"] for i in instances})
            },
            "market_family_counts": {
                family: sum(i["market_family"] == family for i in instances)
                for family in sorted({i["market_family"] for i in instances})
            },
        }, indent=1))
        for c in conditions:
            print(f"\n===== example buyer prompt: {c} =====")
            print(build_prompt(
                c, instances[0], "B1", "buyer", 1,
                args.rounds, args.feedback_policy, None, None,
            ))
        if args.write_plan:
            write_plan(args.write_plan, plan)
            print(f"\nFrozen plan written once: {os.path.abspath(args.write_plan)}")
        return

    frozen_path, frozen_plan = None, None
    if args.mock:
        llm = make_mock_llm()
    else:
        if (args.estimated_cost_usd is None
                or not math.isfinite(args.estimated_cost_usd)
                or args.estimated_cost_usd < 0):
            raise SystemExit(
                "refusing to start a paid run without --estimated-cost-usd"
            )
        if not args.confirm_paid_run:
            raise SystemExit(
                f"refusing to start a paid run of ~{model_calls} calls without "
                "--confirm-paid-run (use --dry-run to inspect the plan first)")
        frozen_path, frozen_plan = load_frozen_plan(args.plan_file, plan)
        load_env()
        llm = make_llm(provider=args.provider, model=args.model,
                       reasoning_effort=args.reasoning_effort,
                       default_max_tokens=args.max_tokens,
                       openrouter_provider=args.openrouter_provider)

    slug = ("mock_" if args.mock else "") + model_slug(args.model)
    slug = f"{slug}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', args.run_id)}"
    out_root = os.path.join(PROJECT_ROOT, "experiments", EXPERIMENT_DIR_NAME, slug)

    config = {
        **plan,
        "mock": args.mock,
        "paid_run_authorized": bool(args.confirm_paid_run and not args.mock),
        "git_commit": git_revision(),
        "frozen_plan_file": (os.path.relpath(frozen_path, PROJECT_ROOT)
                             if frozen_path else None),
        "frozen_plan": frozen_plan,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with run_directory_lock(out_root, filename=".run.lock"):
        config = prepare_output_directory(out_root, config, resume=args.resume)

        report, cell_report = {}, {}
        paths = {}
        pending_jobs = []
        for condition in conditions:
            path = os.path.join(out_root, condition, "details.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            paths[condition] = path
            done = completed_indices(path, condition, instances, config)
            pending = [i for i in instances if i["idx"] not in done]
            print(f"{condition}: done {len(done)}, pending {len(pending)}", flush=True)
            pending_jobs.extend((condition, inst) for inst in pending)

        # Interleave information conditions so provider timing cannot align with a
        # treatment. The schedule is deterministic from the frozen market seed.
        random.Random(args.seed + 91_003).shuffle(pending_jobs)
        if pending_jobs:
            print(f"\n=== interleaved model episodes: {len(pending_jobs)} ===", flush=True)
            t0 = time.time()
            completed_by_condition = {condition: 0 for condition in conditions}
            failed_jobs = []
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(
                    run_one, condition, llm, inst,
                    args.max_tokens, args.rounds, args.feedback_policy,
                ): (condition, inst["idx"]) for condition, inst in pending_jobs}
                for k, fut in enumerate(as_completed(futures), start=1):
                    condition, market_idx = futures[fut]
                    try:
                        row = fut.result()
                    except Exception as error:
                        failed_jobs.append((condition, market_idx, str(error)))
                        print(
                            f"  FAILED condition={condition} market={market_idx}: {error}",
                            flush=True,
                        )
                        continue
                    with open(paths[condition], "a") as fh:
                        fh.write(json.dumps(row) + "\n")
                    completed_by_condition[condition] += 1
                    if k % 5 == 0 or k == len(pending_jobs):
                        counts = ", ".join(
                            f"{name}={completed_by_condition.get(name, 0)}"
                            for name in conditions
                        )
                        print(
                            f"  {k}/{len(pending_jobs)} ({time.time() - t0:.0f}s; {counts})",
                            flush=True,
                        )
            if failed_jobs:
                failed = "; ".join(
                    f"{condition}[{market_idx}]" for condition, market_idx, _ in failed_jobs
                )
                raise RuntimeError(
                    f"{len(failed_jobs)} model episode(s) failed: {failed}. "
                    "Resume the frozen run to retry only these jobs."
                )

        for condition in conditions:
            path = paths[condition]
            rows = [json.loads(l) for l in open(path) if l.strip()]
            report[condition] = summarize(rows)
            cell_report[condition] = summarize_by_market_family(rows)
            s = report[condition]
            print(f"** {condition}: efficiency={s['efficiency']:.3f} "
                  f"trades={s['trades_per_market']:.2f} "
                  f"invalid_orders={s['invalid_orders']}", flush=True)

        out = os.path.join(out_root, "summary.json")
        with open(out, "x") as fh:
            json.dump({
                "config": config,
                "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "conditions": report,
                "by_market_family": cell_report,
            }, fh, indent=1)
        print(f"\nSaved -> {os.path.relpath(out, PROJECT_ROOT)}")
        readable = render(
            Path(out_root), Path(out_root) / "readable_episodes",
        )
        print(f"Readable model calls -> {os.path.relpath(readable, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
