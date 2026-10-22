"""Consistent entry point for the three main studies; preview by default."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys

from experiment_runtime.models import MODEL_NAMES, model_settings

ROOT = Path(__file__).resolve().parent


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="list model presets and endpoints")
    run = commands.add_parser("main", help="preview or run a main study")
    run.add_argument("game", choices=("trade", "bargaining", "auction"))
    run.add_argument("model", choices=MODEL_NAMES)
    run.add_argument("--mode", choices=("preview", "freeze", "execute"), default="preview")
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--messages", type=int, choices=(2, 4, 6), help="bargaining only; default 6")
    run.add_argument("--results-dir", type=Path, help="trade output root or bargaining run directory")
    run.add_argument("--budget", type=float, help="bargaining execution authorization, not a hard cap")
    run.add_argument("--run-label", help="auction frozen-plan identifier")
    run.add_argument("--estimated-cost", type=float, help="auction planning estimate, not a spending cap")
    run.add_argument("--resume", action="store_true", help="resume an existing auction run")
    return result


def build_command(args, environment=None):
    """Translate the common interface without changing game engines or outputs."""
    env = dict(os.environ if environment is None else environment)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # Shell launchers invoke `python`; select the active interpreter's environment.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    for flag in ("budget", "estimated_cost"):
        value = getattr(args, flag)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"--{flag.replace('_', '-')} must be positive and finite")
    allowed = {
        "trade": {"results_dir"},
        "bargaining": {"messages", "results_dir", "budget"},
        "auction": {"run_label", "estimated_cost", "resume"},
    }[args.game]
    for flag in ("messages", "results_dir", "budget", "run_label", "estimated_cost", "resume"):
        if getattr(args, flag) not in (None, False) and flag not in allowed:
            raise ValueError(f"--{flag.replace('_', '-')} is not supported for {args.game}")
    if args.mode == "freeze" and args.game != "auction":
        raise ValueError("Only auctions use frozen plans; use preview for trade or bargaining")
    output = str(args.results_dir.expanduser().resolve()) if args.results_dir else None
    if args.game == "trade":
        cwd = ROOT / "multiresource_trade"
        env["WORKERS"] = str(args.workers)
        if output:
            env["RESULT_ROOT"] = output
        command = ["bash", "scripts/main/_run.sh", args.model, args.mode]
    elif args.game == "bargaining":
        cwd = ROOT / "single_good_bargaining"
        if args.mode == "execute" and args.budget is None:
            raise ValueError("Bargaining execution requires an explicit --budget")
        command = ["bash", "run.sh", args.model, args.mode,
                   "--messages", str(args.messages or 6), "--condition-set", "main",
                   "--workers", str(args.workers)]
        if output:
            command += ["--results-dir", output]
        if args.budget is not None:
            command += ["--budget", str(args.budget)]
    else:
        cwd = ROOT / "double_auction"
        if args.mode != "preview" and (not args.run_label or args.estimated_cost is None):
            raise ValueError("Auction freeze/execute requires --run-label and --estimated-cost")
        if args.resume and args.mode != "execute":
            raise ValueError("--resume requires --mode execute")
        command = [sys.executable, "auction.py", "study", args.model, "margin", args.mode,
                   "--markets", "30", "--seed", "97617", "--workers", str(args.workers)]
        for flag in ("run_label", "estimated_cost"):
            if getattr(args, flag) is not None:
                command += ["--" + flag.replace("_", "-"), str(getattr(args, flag))]
        if args.resume:
            command.append("--resume")
    return command, cwd, env


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.command == "models":
        for name in MODEL_NAMES:
            model = model_settings(name)
            print(f"{name:18} {model['provider']:12} {model['model']}")
        return 0
    try:
        command, cwd, env = build_command(args)
    except ValueError as exc:
        cli.error(str(exc))
    print(f"Working directory: {cwd}\nCommand: {shlex.join(command)}", flush=True)
    return subprocess.run(command, cwd=cwd, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
