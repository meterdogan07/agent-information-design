"""User-facing commands for the double-auction experiment."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from experiment_runtime.models import generation_settings, model_catalog

from . import market, prompts


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PLANS = ROOT / "experiments" / market.EXPERIMENT_DIR_NAME / "plans"

GENERATION = generation_settings("auction")
MODELS = model_catalog("auction")


def arm_config(description, *, rounds=1, feedback="none",
               condition_set="endpoint_precision", buyers=3, sellers=3,
               market_design="three_family", mechanism="midpoint"):
    return {
        "description": description,
        "rounds": rounds,
        "feedback": feedback,
        "condition_set": condition_set,
        "buyers": buyers,
        "sellers": sellers,
        "market_design": market_design,
        "mechanism": mechanism,
    }


ARMS = {
    "natural-information": arm_config(
        "Three-family 3x3 least/coarse/full endpoint information"),
    "natural-placement": arm_config(
        "Three-family 3x3 full information routed by market side",
        condition_set="endpoint_placement"),
    "overlap-information": arm_config(
        "Matched 3x3 sweep from separated to overlapping endpoint populations",
        market_design="overlap_sweep"),
    "margin-information": arm_config(
        "Balanced 3x3 sweep over efficient quantity and marginal gains from trade",
        market_design="margin_sweep"),
    "margin-role-mirror": arm_config(
        "Buyer-seller role mirror of the 3x3 margin sweep",
        condition_set="least_full", market_design="margin_sweep_mirrored"),
    "natural-r2-none": arm_config(
        "Two rounds without market feedback", rounds=2),
    "natural-r2-status": arm_config(
        "Two rounds with one private would-trade signal",
        rounds=2, feedback="private_status"),
    "pivotal-6x6": arm_config(
        "Six-by-six competition with a narrow pivotal third trade",
        buyers=6, sellers=6, market_design="pivotal_competition"),
    "scaling-3x3": arm_config(
        "Nested iid three-by-three uniform-price call auction",
        market_design="size_scaling", mechanism="uniform_price"),
    "scaling-6x6": arm_config(
        "Nested iid six-by-six uniform-price call auction",
        buyers=6, sellers=6, market_design="size_scaling", mechanism="uniform_price"),
    "scaling-10x10": arm_config(
        "Nested iid ten-by-ten uniform-price call auction",
        buyers=10, sellers=10, market_design="size_scaling", mechanism="uniform_price"),
}

STUDIES = {
    "information": ("natural-information",),
    "placement": ("natural-placement",),
    "overlap": ("overlap-information",),
    "margin": ("margin-information",),
    "role-mirror": ("margin-role-mirror",),
    # The one-round baseline comes from the information study. These two arms
    # isolate a second attempt from the effect of receiving market feedback.
    "feedback": ("natural-r2-none", "natural-r2-status"),
    "market-size": ("scaling-3x3", "scaling-6x6", "scaling-10x10"),
    "pivotal": ("pivotal-6x6",),
    "all": (
        "natural-information", "natural-placement",
        "overlap-information",
        "margin-information",
        "margin-role-mirror",
        "natural-r2-none", "natural-r2-status",
        "pivotal-6x6",
        "scaling-3x3", "scaling-6x6", "scaling-10x10",
    ),
}

PROMPT_CONDITIONS = tuple({
    condition
    for conditions in market.CONDITION_SETS.values()
    for condition in conditions
})


def package_environment():
    environment = os.environ.copy()
    previous = environment.get("PYTHONPATH")
    paths = [str(SRC), str(ROOT.parent)]
    if previous:
        paths.append(previous)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    return environment


def env_number(name, default, convert):
    value = os.environ.get(name)
    return convert(value) if value is not None else default


def information_prompt_preview(seed=83001, market_index=0, role="buyer",
                               condition=None,
                               condition_set="endpoint_precision"):
    """Render one matched market's prompts without calling a provider."""
    if market_index < 0:
        raise ValueError("market index must be non-negative")
    instance = market.gen_instances(market_index + 1, 3, 3, seed)[market_index]
    agent = instance["buyers"][0] if role == "buyer" else instance["sellers"][0]
    conditions = (condition,) if condition else market.conditions_for(condition_set)
    sections = [
        "OFFLINE INFORMATION-PROMPT INSPECTION",
        "No provider call is made and no result artifact is written.",
        f"Seed: {seed} | market index: {market_index} | role: {role} {agent}",
        "Actual market (shown only here; never included in a hidden-information prompt):",
        json.dumps({
            "regime": instance["regime"],
            "values": instance["values"],
            "costs": instance["costs"],
        }, indent=2, sort_keys=True),
    ]
    for name in conditions:
        text = prompts.build_prompt(
            name, instance, agent, role,
            round_number=1, rounds=1, feedback_policy="none",
        )
        sections.extend(("", f"===== {name.upper()} =====", text.rstrip()))
    return "\n".join(sections) + "\n"


def runner_arguments(args):
    """Convert one named arm to the runner's explicit parameters."""
    arm = ARMS[args.preset]
    return [
        "--provider", args.provider,
        "--model", args.model,
        *(["--openrouter-provider", args.openrouter_provider]
          if args.openrouter_provider else []),
        "--reasoning-effort", args.reasoning_effort,
        "--max-tokens", str(args.max_tokens),
        "--workers", str(args.workers),
        "--n", str(args.markets),
        "--n-buyers", str(arm["buyers"]),
        "--n-sellers", str(arm["sellers"]),
        "--market-design", arm["market_design"],
        "--mechanism", arm["mechanism"],
        "--seed", str(args.seed),
        "--rounds", str(arm["rounds"]),
        "--feedback-policy", arm["feedback"],
        "--condition-set", arm["condition_set"],
        "--run-id", args.run_id or f"{args.preset}_s{args.seed}",
    ]


def runner_command(args):
    run_id = args.run_id or f"{args.preset}_s{args.seed}"
    command = [sys.executable, "-m", "double_auction.runner", *runner_arguments(args)]
    if args.action == "preview":
        command.append("--dry-run")
        return command

    if args.estimated_cost is None or args.estimated_cost <= 0:
        raise SystemExit("freeze and execute require a positive --estimated-cost")
    plan = PLANS / f"{run_id}.json"
    command.extend(["--estimated-cost-usd", str(args.estimated_cost)])
    if args.action == "freeze":
        command.extend(["--dry-run", "--write-plan", str(plan)])
    else:
        command.extend(["--plan-file", str(plan), "--confirm-paid-run"])
        if args.resume:
            command.append("--resume")
    return command


def run_arm(args):
    print(f"\n{args.preset}: {ARMS[args.preset]['description']}", flush=True)
    command = runner_command(args)
    if args.action == "preview" and not args.show_prompts:
        completed = subprocess.run(
            command, cwd=ROOT, env=package_environment(),
            text=True, capture_output=True,
        )
        if completed.returncode:
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            raise SystemExit(completed.returncode)
        plan, _ = json.JSONDecoder().raw_decode(completed.stdout.lstrip())
        print(json.dumps(plan, indent=2))
        return

    subprocess.run(command, cwd=ROOT, env=package_environment(), check=True)
    if args.action == "execute":
        run_id = args.run_id or f"{args.preset}_s{args.seed}"
        run_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)
        result = (
            ROOT / "experiments" / market.EXPERIMENT_DIR_NAME
            / f"{market.model_slug(args.model)}_{run_slug}"
        )
        subprocess.run(
            [sys.executable, "-m", "double_auction.audit", str(result)],
            cwd=ROOT, env=package_environment(), check=True,
        )


def run_study(args):
    model = MODELS[args.model_name]
    if args.mode != "preview" and not args.run_label:
        raise SystemExit("freeze and execute require --run-label (or RUN_LABEL)")
    if args.resume and args.mode != "execute":
        raise SystemExit("--resume is valid only with execute")

    print(
        f"Model: {args.model_name} | {model['provider']} / {model['model']}"
        f" | backend: {model['backend'] or 'OpenAI'}",
        flush=True,
    )
    for preset in STUDIES[args.study]:
        run_id = (
            f"{args.run_label or 'preview'}_{model['tag']}_{preset}_s{args.seed}"
        )
        arm_args = argparse.Namespace(
            action=args.mode,
            preset=preset,
            provider=model["provider"],
            model=model["model"],
            openrouter_provider=model["backend"],
            reasoning_effort=model.get("reasoning_effort", args.reasoning_effort),
            max_tokens=args.max_tokens,
            workers=args.workers,
            markets=args.markets,
            seed=args.seed,
            run_id=run_id,
            estimated_cost=args.estimated_cost,
            resume=args.resume,
            show_prompts=args.show_prompts,
        )
        run_arm(arm_args)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    commands.add_parser("models", help="list configured models")
    commands.add_parser("list", help="list individual experiment arms")

    inspect = commands.add_parser("inspect", help="print prompts without API calls")
    inspect.add_argument("--seed", type=int, default=83001)
    inspect.add_argument("--market-index", type=int, default=0)
    inspect.add_argument("--role", choices=["buyer", "seller"], default="buyer")
    inspect.add_argument("--condition", choices=sorted(PROMPT_CONDITIONS))
    inspect.add_argument("--condition-set", choices=market.CONDITION_SETS,
                         default="endpoint_precision")

    study = commands.add_parser("study", help="run a complete named study")
    study.add_argument("model_name", choices=MODELS)
    study.add_argument("study", choices=STUDIES)
    study.add_argument("mode", nargs="?", choices=["preview", "freeze", "execute"],
                       default="preview")
    study.add_argument("--seed", type=int,
                       default=env_number("SEED", 83001, int))
    study.add_argument("--markets", type=int,
                       default=env_number("MARKETS", market.DEFAULT_MARKETS, int))
    study.add_argument("--workers", type=int,
                       default=env_number("WORKERS", 2, int))
    study.add_argument("--max-tokens", type=int,
                       default=env_number("MAX_TOKENS", GENERATION["max_tokens"], int))
    study.add_argument("--reasoning-effort",
                       default=os.environ.get("REASONING_EFFORT", GENERATION["reasoning_effort"]))
    study.add_argument("--run-label", default=os.environ.get("RUN_LABEL"))
    study.add_argument("--estimated-cost", type=float,
                       default=env_number("ESTIMATED_COST", None, float))
    study.add_argument("--resume", action="store_true",
                       default=os.environ.get("RESUME") == "1")
    study.add_argument("--show-prompts", action="store_true")

    audit = commands.add_parser("audit", help="replay a completed result")
    audit.add_argument("result_dir", type=Path)
    render = commands.add_parser("render", help="export readable conversations")
    render.add_argument("result_dir", type=Path)
    render.add_argument("--output", type=Path)
    return result


def main():
    args = parser().parse_args()
    if args.action == "models":
        for name, config in MODELS.items():
            print(
                f"{name:15} {config['provider']:10} {config['model']:32} "
                f"{config['backend'] or 'OpenAI'}"
            )
        return
    if args.action == "list":
        for name, arm in ARMS.items():
            print(f"{name:20} {arm['description']}")
        return
    if args.action == "inspect":
        print(information_prompt_preview(
            args.seed, args.market_index, args.role,
            args.condition, args.condition_set,
        ), end="")
        return
    if args.action == "study":
        run_study(args)
        return
    if args.action in {"audit", "render"}:
        result_dir = args.result_dir if args.result_dir.is_absolute() else ROOT / args.result_dir
        module = "double_auction.audit" if args.action == "audit" else "double_auction.render"
        command = [sys.executable, "-m", module, str(result_dir)]
        if args.action == "render" and args.output:
            command.extend(["--output", str(args.output)])
        subprocess.run(command, cwd=ROOT, env=package_environment(), check=True)
        return
if __name__ == "__main__":
    main()
