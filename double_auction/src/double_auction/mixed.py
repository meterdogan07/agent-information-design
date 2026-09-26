"""Mixed-model, single-round auctions using the unchanged main-study engine.

All buyers use one model and all sellers use another. Both assignments use
the same margin markets. Preview is the default; paid execution requires a
committed frozen plan and unchanged source. No results are pooled across roles.
"""

from __future__ import annotations

from experiment_runtime.models import generation_settings

import argparse
import hashlib
import json
import math
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from experiment_runtime import run_directory_lock

from .analysis import summarize, summarize_by_market_family
from .audit import audit_run, close
from .cli import MODELS
from .episode import make_mock_llm, run_one
from .market import gen_instances, conditions_for, load_env, protocol_version
from .prompts import build_prompt
from .mixed_budget import BudgetLedger, BudgetedLLM, RATES
from .render import render
from .runner import SOURCE_FILES, completed_indices, require_committed

GENERATION = generation_settings("auction")

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
PAIRS = {
    "sonnet5_gemini": ("claudesonnet5", "gemini"),
    "sonnet5_grok43": ("claudesonnet5", "grok43"),
    "grok43_gemini": ("grok43", "gemini"),
    "gpt51_sonnet5": ("gpt51", "claudesonnet5"),
    "gpt51_gemini": ("gpt51", "gemini"),
    "gpt51_grok43": ("gpt51", "grok43"),
}
FROZEN_SOURCES = tuple(dict.fromkeys([
    *(Path(p) for p in SOURCE_FILES), Path(__file__),
    Path(__file__).with_name("cli.py"), Path(__file__).with_name("render.py"),
    Path(__file__).with_name("__init__.py"), ROOT / "run_mixed_models.py",
    Path(__file__).with_name("mixed_budget.py"),
]))


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    """Create immutable configuration/summary files; never silently overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def append_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")
        stream.flush()


def source_hashes():
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FROZEN_SOURCES}


def make_plan(pair, n=30, seed=97617, max_tokens=GENERATION["max_tokens"],
              reasoning_effort=GENERATION["reasoning_effort"], estimated_cost_usd=None, budget_usd=4.0):
    if pair not in PAIRS or n <= 0 or n % 3 or max_tokens <= 0:
        raise ValueError("Choose a supported pair, positive tokens, and markets divisible by 3")
    if estimated_cost_usd is not None and (
            not math.isfinite(estimated_cost_usd) or estimated_cost_usd < 0):
        raise ValueError("Cost estimate must be finite and nonnegative")
    first, second = PAIRS[pair]
    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("Budget must be positive and finite")
    assignments = []
    for buyer, seller in ((first, second), (second, first)):
        roles = {role: {"preset": name, **MODELS[name]}
                 for role, name in (("buyer", buyer), ("seller", seller))}
        assignments.append({"name": f"buyers-{buyer}__sellers-{seller}", "roles": roles})
    return {
        "study": "mixed_margin_v1", "pair": pair,
        "protocol_version": protocol_version("margin_sweep"),
        "market_design": "margin_sweep", "mechanism": "midpoint",
        "seed": seed, "markets": n, "n_buyers": 3, "n_sellers": 3,
        "condition_set": "endpoint_precision",
        "conditions": list(conditions_for("endpoint_precision")),
        "rounds": 1, "feedback_policy": "none", "temperature": "provider_default",
        "max_tokens": max_tokens, "reasoning_effort": reasoning_effort,
        "assignments": assignments, "planned_auctions": n * 3 * 2,
        "planned_model_calls": n * 3 * 2 * 6,
        "estimated_cost_usd": estimated_cost_usd,
        "budget_usd": budget_usd,
        "price_per_million_tokens": {name: list(RATES[name]) for name in (first, second)},
        "execution_order": "seeded_interleaved_conditions_and_assignments",
        "source_sha256": source_hashes(),
    }


class RoleRouter:
    """Route only exact engine-generated prompts; never infer roles from prose.

    A router is local to one episode. Clients retain per-call raw traces, so
    model identities and failed attempts remain inspectable even on failure.
    """

    def __init__(self, inst, condition, assignment, clients, plan, mock=False):
        self.roles = assignment["roles"]
        self.clients = clients
        self.plan, self.mock, self.calls = plan, mock, []
        self.routes = {}
        for role, names in (("buyer", inst["buyers"]), ("seller", inst["sellers"])):
            for name in names:
                prompt = build_prompt(condition, inst, name, role, 1, 1, "none", None, None)
                if prompt in self.routes:
                    raise ValueError("Agent prompts must uniquely identify a participant")
                self.routes[prompt] = (role, name)

    def start_trace(self):
        self.calls = []

    def end_trace(self):
        return list(self.calls)

    def __call__(self, prompt, max_tokens=None):
        role, agent = self.routes[prompt]  # Unknown prompts fail before any API call.
        spec = self.roles[role]
        client = self.clients[spec["preset"]]
        if self.mock:
            response = client(prompt, max_tokens=max_tokens)
            calls = [{"prompt": prompt, "response": response,
                      "provider": spec["provider"], "requested_model": spec["model"],
                      "returned_model": spec["model"],
                      "returned_provider": spec["backend"] or "OpenAI",
                      "openrouter_provider": spec["backend"],
                      "finish_reason": "stop", "attempts": 1, "attempt_history": [],
                      "usage": {}, "error": None, "mock": True,
                      "max_tokens": max_tokens,
                      "reasoning_effort": self.plan["reasoning_effort"]}]
        else:
            client.start_trace()
            try:
                response = client(prompt, max_tokens=max_tokens)
            finally:
                calls = client.end_trace()
                self.calls.extend({**call, "agent": agent, "role": role} for call in calls)
            if len(calls) != 1 or calls[0].get("error"):
                raise ValueError("Missing successful provider trace")
            raw = calls[0]
            if raw.get("requested_model") != spec["model"]:
                raise ValueError("Requested model differs from frozen role assignment")
            if spec["backend"] and (raw.get("returned_provider") or "").lower() != spec["backend"].lower():
                raise ValueError("Serving provider could not be verified")
            return response
        self.calls.extend({**call, "agent": agent, "role": role} for call in calls)
        return response


def validate_row(row, inst, condition, assignment, plan):
    """Replay the actual responses and verify every call's assigned model."""
    if row.get("role_models") != assignment["roles"]:
        raise ValueError("Episode role-model assignment mismatch")
    log = iter(row["log"])

    def replay(prompt, max_tokens=None):
        entry = next(log)
        if entry["prompt"] != prompt:
            raise ValueError("Saved prompt does not match the frozen market and condition")
        return entry["response"]

    expected = run_one(condition, replay, inst, plan["max_tokens"], 1, "none")
    if next(log, None) is not None:
        raise ValueError("Unexpected additional model calls")
    for field, value in expected.items():
        if field not in {"raw_calls", "usage"} and not close(row.get(field), value):
            raise ValueError(f"Episode replay mismatch: {field}")
    raw_calls = row.get("raw_calls", [])
    if len(raw_calls) != 6:
        raise ValueError("Expected exactly six provider traces")
    for entry, raw in zip(row["log"], raw_calls):
        spec = assignment["roles"][entry["role"]]
        expected_fields = {
            "requested_model": spec["model"], "provider": spec["provider"],
            "openrouter_provider": spec["backend"], "agent": entry["who"],
            "role": entry["role"], "prompt": entry["prompt"],
            "response": entry["response"], "max_tokens": plan["max_tokens"],
            "reasoning_effort": plan["reasoning_effort"], "finish_reason": "stop",
        }
        if raw.get("error") or any(raw.get(k) != v for k, v in expected_fields.items()):
            raise ValueError("Provider trace differs from assigned role or generation settings")
        if spec["backend"] and (raw.get("returned_provider") or "").lower() != spec["backend"].lower():
            raise ValueError("Incorrect or missing serving provider")


def assignment_config(plan, assignment, mock):
    auctions = plan["markets"] * len(plan["conditions"])
    assigned_plan = {**plan, "assignments": [assignment],
                     "planned_auctions": auctions, "planned_model_calls": auctions * 6}
    return {**assigned_plan, "role_models": assignment["roles"],
            "run_id": assignment["name"], "model": assignment["name"],
            "mock": mock, "frozen_plan": assigned_plan, "paid_run_authorized": not mock}


def run_job(condition, inst, assignment, clients, plan, mock):
    router = RoleRouter(inst, condition, assignment, clients, plan, mock)
    try:
        row = run_one(condition, router, inst, plan["max_tokens"], 1, "none")
        row["role_models"] = assignment["roles"]
        validate_row(row, inst, condition, assignment, plan)
        return row, None
    except Exception as error:
        return None, {"condition": condition, "idx": inst["idx"],
                      "assignment": assignment["name"], "error": str(error),
                      "raw_calls": router.calls}


def execute(plan, destination, *, mock=False, workers=2, resume=False, bootstrap_draws=20000):
    if plan["source_sha256"] != source_hashes():
        raise ValueError("Source changed since the plan was frozen; do not resume with changed code")
    instances = gen_instances(plan["markets"], 3, 3, plan["seed"], "margin_sweep")
    with run_directory_lock(destination, filename=".run.lock"):
        identity = {"plan": plan, "mock": mock}
        identity_file = destination / "study.json"
        if identity_file.exists():
            if not resume or read_json(identity_file) != identity:
                raise ValueError("Use --resume with exactly the original plan and execution mode")
        else:
            if any(p.name != ".run.lock" for p in destination.iterdir()):
                raise ValueError("Result directory is not empty; choose a new directory")
            write_json(identity_file, identity)
            for path in FROZEN_SOURCES:
                target = destination / "source_snapshot" / path.relative_to(REPO)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(path.read_bytes())
            write_json(destination / "markets.json", instances)

        pending = []
        for assignment in plan["assignments"]:
            base = destination / assignment["name"]
            config = assignment_config(plan, assignment, mock)
            if (base / "run_config.json").exists():
                if read_json(base / "run_config.json") != config:
                    raise ValueError("Assignment configuration changed")
            else:
                write_json(base / "run_config.json", config)
            for condition in plan["conditions"]:
                path = base / condition / "details.jsonl"
                done = completed_indices(str(path), condition, instances, config)
                if path.exists():
                    for line in path.read_text().splitlines():
                        if line.strip():
                            row = json.loads(line)
                            validate_row(row, instances[row["idx"]], condition, assignment, plan)
                pending.extend((condition, inst, assignment) for inst in instances if inst["idx"] not in done)

        random.Random(plan["seed"] + 91003).shuffle(pending)
        clients = {}
        ledger = None if mock else BudgetLedger(destination / "budget.jsonl", plan["budget_usd"])
        if pending:
            if not mock:
                load_env()
            for name in PAIRS[plan["pair"]]:
                spec = MODELS[name]
                clients[name] = make_mock_llm() if mock else BudgetedLLM(
                    {"preset": name, **spec}, plan, ledger)
        completed = plan["planned_auctions"] - len(pending)
        print(f"Completed {completed}/{plan['planned_auctions']}; pending {len(pending)}", flush=True)
        # Bound in-flight work; a failure stops submission after this batch.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(pending), workers):
                futures = {pool.submit(run_job, c, i, a, clients, plan, mock): (c, a)
                           for c, i, a in pending[start:start + workers]}
                failed = False
                for future in as_completed(futures):
                    condition, assignment = futures[future]
                    row, error = future.result()
                    if error:
                        append_json(destination / "failures.jsonl", error)
                        failed = True
                    else:
                        append_json(destination / assignment["name"] / condition / "details.jsonl", row)
                        completed += 1
                        print(json.dumps({"completed": completed, "expected": plan["planned_auctions"],
                                          "accounted_cost_usd": ledger.total if ledger else 0,
                                          "assignment": assignment["name"], "condition": condition}), flush=True)
                if failed:
                    raise RuntimeError("Paused after failure. Inspect failures.jsonl before explicit --resume")

        reports = {}
        for assignment in plan["assignments"]:
            base = destination / assignment["name"]
            cells = {c: [json.loads(line) for line in (base / c / "details.jsonl").read_text().splitlines() if line.strip()]
                     for c in plan["conditions"]}
            summary = {"config": assignment_config(plan, assignment, mock),
                       "conditions": {c: summarize(rows) for c, rows in cells.items()},
                       "by_market_family": {c: summarize_by_market_family(rows) for c, rows in cells.items()}}
            if (base / "summary.json").exists():
                if not close(read_json(base / "summary.json"), summary):
                    raise ValueError("Stored summary differs from saved episodes")
            else:
                write_json(base / "summary.json", summary)
            report = audit_run(base, bootstrap_draws=bootstrap_draws)
            if not report["passed"]:
                raise ValueError(f"Audit failed: {report['failures']}")
            if not (base / "audit.json").exists():
                write_json(base / "audit.json", report)
            render(base, base / "readable_episodes")
            reports[assignment["name"]] = report
        if not (destination / "analysis.json").exists():
            write_json(destination / "analysis.json", reports)
        print(f"COMPLETE: {destination}", flush=True)
        return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=PAIRS, required=True)
    parser.add_argument("--mode", choices=("preview", "freeze", "execute", "mock"), default="preview")
    parser.add_argument("--markets", type=int, default=30)
    parser.add_argument("--seed", type=int, default=97617)
    parser.add_argument("--max-tokens", type=int, default=GENERATION["max_tokens"])
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default=GENERATION["reasoning_effort"])
    parser.add_argument("--estimated-cost-usd", type=float, help="Planning estimate, NOT a spending cap")
    parser.add_argument("--budget-usd", type=float, default=4.0,
                        help="Per-pair accounted cost ceiling, including reserved requests and retries")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-paid-run", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    plan = make_plan(args.pair, args.markets, args.seed, args.max_tokens,
                     args.reasoning_effort, args.estimated_cost_usd, args.budget_usd)
    plan_path = args.plan_file or ROOT / "experiments/mixed_models/plans" / f"{args.pair}_v1.json"
    destination = args.results_dir or ROOT / "experiments/mixed_models" / f"{'mock_' if args.mode == 'mock' else ''}{args.pair}_v1"
    if args.mode == "preview":
        print(json.dumps(plan, indent=2))
        return
    if args.mode == "freeze":
        if args.estimated_cost_usd is None:
            parser.error("--estimated-cost-usd is required to freeze a paid-run plan")
        write_json(plan_path, plan)
        print(f"Frozen plan: {plan_path}\nCommit this plan and the auction source before paid execution.")
        return
    if args.mode == "execute":
        if not args.confirm_paid_run or args.estimated_cost_usd is None:
            parser.error("Execution requires --confirm-paid-run and --estimated-cost-usd")
        require_committed(str(plan_path))
        for source in FROZEN_SOURCES:
            require_committed(str(source))
        if read_json(plan_path) != plan:
            parser.error("Frozen plan does not match these settings and source hashes")
    execute(plan, destination, mock=args.mode == "mock", workers=args.workers, resume=args.resume)


if __name__ == "__main__":
    main()
