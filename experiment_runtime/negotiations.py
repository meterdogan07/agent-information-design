"""Shared execution for role-assigned trade and bargaining studies.

Game rules, prompts, clients, and metrics stay in their experiment packages.
This module handles only routing, replay, budgets, and saved run artifacts.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import os
import shutil
import threading
import uuid

from .provider import token_counts
from .artifacts import run_directory_lock


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def read_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_row(path, value):
    with path.open("a") as stream:
        stream.write(encoded(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class BudgetStop(RuntimeError):
    pass


class Budget:
    """Persist reservations before calls; unresolved calls remain charged.

    Reserve for all transport retries using an intentionally conservative input
    bound (UTF-8 bytes plus schema overhead) and the full output cap. Completed
    calls are charged at uncached list prices or reported cost, whichever is
    higher. Missing usage is charged at the reservation.
    """
    def __init__(self, path, ceiling, models, max_tokens):
        self.models, self.max_tokens = models, max_tokens
        self.path, self.ceiling = path, ceiling
        self.lock = threading.Lock()
        self.charges = {}
        for event in read_rows(path):
            self.charges[event["id"]] = event["charge"]

    @property
    def spent(self):
        return sum(self.charges.values())

    def reserve(self, prompt, preset):
        prices = self.models[preset]
        amount = 3 * ((len(prompt.encode("utf-8")) + 4096) * prices["input_price"]
                      + self.max_tokens * prices["output_price"]) / 1_000_000
        with self.lock:
            if self.spent + amount > self.ceiling:
                raise BudgetStop("remaining budget cannot cover another call")
            key = str(uuid.uuid4())
            append_row(self.path, {"id": key, "charge": amount, "state": "reserved"})
            self.charges[key] = amount
        return key, amount

    def settle(self, key, reservation, records, preset):
        prices = self.models[preset]
        amount = 0.0
        for record in records:
            usage = record.get("usage")
            if not usage:
                amount += reservation / 3
                continue
            counts = token_counts(usage)
            calculated = (counts["input_tokens"] * prices["input_price"]
                          + counts["output_tokens"] * prices["output_price"]) / 1_000_000
            amount += max(calculated, float(usage.get("cost") or 0))
        if not records:
            amount = reservation
        with self.lock:
            append_row(self.path, {"id": key, "charge": amount, "state": "settled"})
            self.charges[key] = amount


def source_files(root, source_root):
    from .models import CONFIG_FILES
    local = {str(p.relative_to(root.parent)): p for p in source_root.rglob("*.py")
             if not {"archive", "__pycache__", "results", "output"}.intersection(p.parts)}
    local.update({str(p.relative_to(root.parent)): p
                  for p in (root.parent / "experiment_runtime").glob("*.py")})
    local.update({str(p.relative_to(root.parent)): p for p in CONFIG_FILES})
    launcher = root / "run_mixed_models.py"
    local[str(launcher.relative_to(root.parent))] = launcher
    return local


def run_negotiation(spec, budget, mock, *, actor_sequence, check_role,
                    make_client, call_client, validate_records, play, make_mock):
    market, condition, assignment = spec
    episode_id = identifier(spec)
    calls = []
    responses = []
    expected_actors = actor_sequence(market)
    mock_responder = make_mock() if mock else None
    clients = {} if mock else {role: make_client(preset) for role, preset in assignment.items()}

    def respond(prompt, **kwargs):
        actor = expected_actors[len(responses)]
        # Only inspect the fixed role header, never counterpart-controlled history.
        check_role(prompt, actor)
        preset = assignment[actor]
        if mock:
            answer = mock_responder(prompt, **kwargs)
        else:
            key, reservation = budget.reserve(prompt, preset)
            records = []
            try:
                answer = call_client(clients[actor], prompt, kwargs, market, records)
            finally:
                for record in records:
                    record.update(actor=actor, model_preset=preset, episode_id=episode_id)
                calls.extend(records)
                budget.settle(key, reservation, records, preset)
            validate_records(records, preset)
        responses.append({"actor": actor, "prompt": prompt, "response": answer})
        return answer

    try:
        row = play(market, condition, respond, episode_id)
        # Deterministic replay checks every prompt and all computed outcomes.
        replay_responses = iter(responses)
        def replay(prompt, **kwargs):
            response = next(replay_responses)
            if response["prompt"] != prompt:
                raise ValueError("prompt changed during replay")
            return response["response"]
        replayed = play(market, condition, replay, episode_id)
        if encoded(row) != encoded(replayed) or next(replay_responses, None) is not None:
            raise ValueError("deterministic replay mismatch")
        row["status"] = "completed"
        row["replay_verified"] = True
    except Exception as error:
        row = {"status": "failed", "error": f"{type(error).__name__}: {error}",
               "market": market, "condition": condition}
    row.update(episode_id=episode_id, model_by_actor=assignment,
               raw_api_calls=calls, routed_responses=responses)
    return row


def identifier(spec):
    market, condition, assignment = spec
    roles = "__".join(f"{role}-{preset}" for role, preset in assignment.items())
    return f"{market['market_id']}__{condition}__{roles}"


def summarize(rows, measures):
    groups = {}
    for row in rows:
        name = encoded(row["model_by_actor"]) + "__" + row["condition"]
        groups.setdefault(name, []).append(row)
    return {name: {"episodes": len(group),
                   **{metric: sum(measures(row)[metric] for row in group) / len(group)
                      for metric in measures(group[0])}}
            for name, group in groups.items()}


def readable_episode(row):
    text = "# " + row["episode_id"] + "\n\n" + encoded(row["model_by_actor"]) + "\n"
    for turn in row["routed_responses"]:
        text += "\n## " + turn["actor"] + "\n\n" + turn["response"] + "\n"
    return text


def run_pending(*, folder, pending, rows, expected, budget, mock, workers,
                run_one, readable=readable_episode):
    """Save each completed episode and stop scheduling after the first failure."""
    stop = False
    specs_iter = iter(pending)
    (folder / "readable_episodes").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        while True:
            while not stop and len(futures) < workers:
                spec = next(specs_iter, None)
                if spec is None:
                    break
                futures[pool.submit(run_one, spec, budget, mock)] = spec
            if not futures:
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                row = future.result()
                if row["status"] == "completed":
                    append_row(folder / "episodes.jsonl", row)
                    rows.append(row)
                    (folder / "readable_episodes" / (row["episode_id"] + ".md")).write_text(readable(row))
                else:
                    append_row(folder / "failures.jsonl", row)
                    # Stop scheduling on any failure; preserve partial paid traces.
                    stop = True
                status = {"completed": len(rows), "expected": expected,
                          "accounted_cost_usd": budget.spent, "stopped_on_failure": stop}
                (folder / "status.json").write_text(json.dumps(status, indent=2) + "\n")
                print(encoded({**status, "episode": row["episode_id"], "error": row.get("error")}), flush=True)
    return stop


def run_study(args, *, markets, specs, config_fields, source_files,
              load_credentials, make_budget, run_one, summarize):
    """Run a frozen study without changing the experiment's episode format."""
    folder = args.results_dir.resolve()
    with run_directory_lock(folder):
        sources = source_files()
        hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for name, path in sources.items()}
        from .models import generation_settings
        generation = generation_settings(config_fields["arena"])
        config = {"study": f"mixed_{args.pair}_v1", **config_fields,
                  "reasoning_effort": generation["reasoning_effort"], "temperature": generation["temperature"],
                  "model_identity_disclosed": False,
                  "expected_episodes": len(specs), "markets": markets,
                  "code_sha256": hashes, "mock": args.mock, "budget_usd": args.budget_usd}
        config_path = folder / "config.json"
        if config_path.exists():
            if json.loads(config_path.read_text()) != json.loads(encoded(config)):
                raise RuntimeError("existing frozen config differs; do not overwrite")
        else:
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            for name, path in sources.items():
                target = folder / "source_snapshot" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            (folder / "markets.jsonl").write_text("".join(encoded(m) + "\n" for m in markets))
        for name, digest in hashes.items():
            if hashlib.sha256((folder / "source_snapshot" / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("source snapshot differs")
        if args.execute:
            load_credentials()
        budget = make_budget(folder / "budget.jsonl", args.budget_usd)
        rows = read_rows(folder / "episodes.jsonl")
        completed = {row["episode_id"] for row in rows}
        if len(completed) != len(rows):
            raise RuntimeError("duplicate completed episodes")
        pending = [spec for spec in specs if identifier(spec) not in completed]
        if args.limit:
            pending = pending[:args.limit]
        stop = run_pending(
            folder=folder, pending=pending, rows=rows, expected=len(specs),
            budget=budget, mock=args.mock, workers=args.workers, run_one=run_one,
        )
        audit = {"complete": len(rows) == len(specs),
                 "replay_verified": all(row.get("replay_verified") for row in rows),
                 "frozen_source_verified": True,
                 "episodes": len(rows), "expected_episodes": len(specs),
                 "accounted_cost_usd": budget.spent}
        (folder / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        (folder / "analysis.json").write_text(json.dumps(summarize(rows), indent=2) + "\n")
        if stop:
            raise SystemExit("Paused after a failure or budget stop; inspect failures.jsonl.")
        print("COMPLETE" if audit["complete"] else "SMOKE TEST COMPLETE; resume without --limit", flush=True)
