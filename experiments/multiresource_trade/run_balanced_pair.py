"""Thirty or ninety trade markets, one balanced assignment per market; dry by default.

Uses unchanged negotiation prompts, game engine, clients, budget accounting and replay.
Market selection and role assignment depend only on market attributes, never
on negotiation outcomes. Existing larger runs remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import shutil

from experiment_runtime.negotiations import run_pending
from experiment_runtime import run_directory_lock
from src.trading import run_pair as runner

ROOT = Path(__file__).resolve().parent
SEED = "balanced30_roles_v1_20260920"
PROFILES = {2: (1, 4), 3: (2, 5), 4: (3, 6), 5: (4, 1), 6: (5, 2)}
PAIRS = {
    "gpt51_grok43": ("gpt51", "grok43"),
    "gpt51_sonnet5": ("gpt51", "claudesonnet5"),
    "gpt51_gemini": ("gpt51", "gemini"),
}


def selection(count=30):
    markets = [m for m in runner.frozen_markets()
               if m["profile_index"] in PROFILES[max(m["inventory_a"].values())]]
    slots = {}
    for group in ("proposer", "responder", "symmetric"):
        members = [m for m in markets if m["extraction_role"] == group]
        assert len(members) == 10
        pairs = [sorted((m for m in members if max(m["inventory_a"].values()) == q),
                        key=lambda m: m["market_id"]) for q in range(2, 7)]
        assert all(len(pair) == 2 for pair in pairs)
        # One first-model starter at each inventory size. Also balance its
        # resource-name orientations as closely as five markets permit (2/3).
        candidates = [choice for choice in itertools.product((0, 1), repeat=5)
                      if sum(pair[i]["orientation"] == "xy"
                             for pair, i in zip(pairs, choice)) in (2, 3)]
        chosen = min(candidates, key=lambda c: hashlib.sha256(
            f"{SEED}:{group}:{c}".encode()).hexdigest())
        for pair, index in zip(pairs, chosen):
            for i, market in enumerate(pair):
                slots[market["market_id"]] = "A" if i == index else "B"
        assert Counter(slots[m["market_id"]] for m in members) == {"A": 5, "B": 5}
    assert len(markets) == len(slots) == 30
    if count == 30:
        return markets, slots
    if count != 90:
        raise ValueError("Market count must be 30 or 90")
    # Extend the original assignment without changing any completed market.
    markets = runner.frozen_markets()
    for group in ("proposer", "responder", "symmetric"):
        members = [m for m in markets if m["extraction_role"] == group]
        additions = [sorted((m for m in members if m["market_id"] not in slots
                            and max(m["inventory_a"].values()) == q),
                            key=lambda m: m["market_id"]) for q in range(2, 7)]
        assert all(len(group_) == 4 for group_ in additions)
        options = list(itertools.combinations(range(4), 2))
        old_xy = sum(m["orientation"] == "xy" and slots.get(m["market_id"]) == "A"
                     for m in members)
        candidates = [choices for choices in itertools.product(options, repeat=5)
                      if old_xy + sum(group_[i]["orientation"] == "xy"
                                      for group_, indices in zip(additions, choices)
                                      for i in indices) in (7, 8)]
        chosen = min(candidates, key=lambda c: hashlib.sha256(
            f"{SEED}:extension90:{group}:{c}".encode()).hexdigest())
        for group_, indices in zip(additions, chosen):
            for i, market in enumerate(group_):
                slots[market["market_id"]] = "A" if i in indices else "B"
        assert Counter(slots[m["market_id"]] for m in members) == {"A": 15, "B": 15}
    return markets, slots


def specs_for(markets, slots, pair):
    first, second = PAIRS[pair]
    return [(m, condition, {"A": first, "B": second} if slots[m["market_id"]] == "A"
             else {"A": second, "B": first})
            for m in markets for condition in runner.CONDITIONS]


def freeze(path, data):
    if path.exists():
        if json.loads(path.read_text()) != json.loads(runner.encoded(data)):
            raise RuntimeError(f"Frozen file differs; refusing to overwrite {path}")
    else:
        path.write_text(json.dumps(data, indent=2) + "\n")


def readable(row):
    return ("# " + row["episode_id"] + "\n\n"
            + "## Market\n\n```json\n" + json.dumps(row["market"], indent=2) + "\n```\n"
            + "\n## Models\n\n" + runner.encoded(row["model_by_actor"]) + "\n"
            + "".join("\n## " + t["actor"] + "\n\n" + t["response"] + "\n"
                      for t in row["routed_responses"])
            + "\n## Outcome\n\n```json\n" + json.dumps(row["outcome"], indent=2) + "\n```\n")


def export_existing(markets, slots):
    for pair in ("gpt51_sonnet5", "gpt51_gemini"):
        source = ROOT / "results/mixed_models" / (pair + "_v1")
        wanted = {runner.identifier(s): s for s in specs_for(markets, slots, pair)}
        rows = [r for r in runner.read_rows(source / "episodes.jsonl") if r["episode_id"] in wanted]
        if len(rows) != len(wanted) or len({r["episode_id"] for r in rows}) != len(wanted):
            raise RuntimeError(f"Missing or duplicate selected episodes in {source}")
        fields = ("inventory_a", "inventory_b", "utility_a", "utility_b", "resources")
        for row in rows:
            market, condition, assignment = wanted[row["episode_id"]]
            if (row["condition"] != condition or row["model_by_actor"] != assignment
                    or any(row["market"][k] != market[k] for k in fields)
                    or not row.get("replay_verified")):
                raise RuntimeError("Existing episode does not match frozen comparison")
        folder = ROOT / f"results/mixed_models/balanced{len(markets)}_v1" / pair
        folder.mkdir(parents=True, exist_ok=True)
        freeze(folder / "selected_episodes.json", rows)
        freeze(folder / "selection.json", {"seed": SEED, "first_model_role": slots,
                                            "source": str(source), "episodes": len(wanted)})
        freeze(folder / "analysis.json", runner.summarize(rows))
        (folder / "readable_episodes").mkdir(exist_ok=True)
        for row in rows:
            target = folder / "readable_episodes" / (row["episode_id"] + ".md")
            if not target.exists():
                target.write_text(readable(row))
        print(f"Extracted {len(rows)} existing negotiations: {folder}")


def load_previous(pair, specs, hashes, mock, source_markets=30):
    """Verify an earlier completed panel before reusing or complementing it."""
    source = ROOT / f"results/mixed_models/balanced{source_markets}_v1" / pair
    if mock:
        return [], None
    config = json.loads((source / "config.json").read_text())
    audit = json.loads((source / "audit.json").read_text())
    if config["mock"] or not all(audit.get(k) for k in
                                ("complete", "replay_verified", "frozen_source_verified")):
        raise RuntimeError("Previous run is not a verified complete paid run")
    for key, expected in {"pair": pair, "models": runner.MODELS, "horizon": 8,
                          "max_tokens": runner.MAX_TOKENS, "reasoning_effort": runner.GENERATION["reasoning_effort"],
                          "model_identity_disclosed": False,
                          "conditions": list(runner.CONDITIONS)}.items():
        if config.get(key) != expected:
            raise RuntimeError(f"Previous run has different {key}")
    wrapper = str(Path(__file__).resolve().relative_to(ROOT.parents[1]))
    for name, digest in config["code_sha256"].items():
        if hashlib.sha256((source / "source_snapshot" / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Previous frozen source was modified")
        if name != wrapper and hashes.get(name) != digest:
            raise RuntimeError(f"Experimental code changed since previous run: {name}")
    wanted = {runner.identifier(s): s for s in specs}
    rows = runner.read_rows(source / "episodes.jsonl")
    expected_count = source_markets * 3
    if len(rows) != expected_count or len({r["episode_id"] for r in rows}) != expected_count:
        raise RuntimeError(f"Previous run must contain exactly {expected_count} unique episodes")
    for row in rows:
        spec = wanted.get(row["episode_id"])
        if spec is None or row["market"] != spec[0] or row["model_by_actor"] != spec[2]:
            raise RuntimeError("Previous episode is not part of the expanded assignment")
        responses = iter(row["routed_responses"])
        def replay(prompt, **kwargs):
            response = next(responses)
            if prompt != response["prompt"]:
                raise RuntimeError("Imported episode prompt differs")
            return response["response"]
        result = runner.play(spec[0], spec[1], replay, row["episode_id"])
        if any(result[k] != row[k] for k in result) or next(responses, None) is not None:
            raise RuntimeError("Imported episode fails deterministic replay")
    provenance = {"source": str(source), "episodes": len(rows),
                  "episodes_sha256": hashlib.sha256((source / "episodes.jsonl").read_bytes()).hexdigest(),
                  "config_sha256": hashlib.sha256((source / "config.json").read_bytes()).hexdigest(),
                  "accounted_cost_usd": audit["accounted_cost_usd"]}
    return rows, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=PAIRS, default="gpt51_grok43")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--export-existing", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=8.0)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--markets", type=int, choices=(30, 90), default=30)
    parser.add_argument("--reverse-assignments", action="store_true",
                        help="Run only the opposite assignments to the completed 90-market panel")
    args = parser.parse_args()
    if not 0 < args.budget_usd < float("inf") or args.workers < 1:
        parser.error("Require a finite positive budget and positive workers")
    if args.reverse_assignments and (args.markets != 90 or args.export_existing):
        parser.error("--reverse-assignments requires --markets 90 and cannot export existing runs")
    markets, slots = selection(args.markets)
    original_specs = specs_for(markets, slots, args.pair)
    if args.reverse_assignments:
        slots = {key: "B" if value == "A" else "A" for key, value in slots.items()}
    specs = specs_for(markets, slots, args.pair)
    expected_count = len(specs)
    runner.MODELS = {p: runner.MODEL_CATALOG[p] for p in PAIRS[args.pair]}
    print(f"{args.pair}: {len(markets)} markets x 3 conditions x 1 assignment = {expected_count} negotiations")
    print(f"{len(markets)//3} markets per group; {len(markets)//6} starts per model in each group; A always moves first.")
    print(f"{runner.HORIZON} messages; {runner.GENERATION['reasoning_effort']} reasoning; {runner.MAX_TOKENS}-token cap; budget ceiling ${args.budget_usd:.2f}")
    if args.reverse_assignments:
        assert not ({runner.identifier(s) for s in specs}
                    & {runner.identifier(s) for s in original_specs})
        print("Opposite assignments only: 270 NEW negotiations; budget applies only to this new batch.")
    if args.export_existing:
        export_existing(markets, slots)
        return
    if not args.execute and not args.mock:
        print("DRY RUN: no files or API calls.")
        return
    study_name = f"balanced{args.markets}{'_reverse' if args.reverse_assignments else ''}_v1"
    folder = (args.results_dir or ROOT / "results/mixed_models" / study_name / args.pair).resolve()
    with run_directory_lock(folder):
        sources = runner.source_files()
        sources[str(Path(__file__).resolve().relative_to(ROOT.parents[1]))] = Path(__file__).resolve()
        hashes = {n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in sources.items()}
        reference_provenance = None
        if args.reverse_assignments:
            _, reference_provenance = load_previous(args.pair, original_specs, hashes, args.mock, source_markets=90)
            previous, provenance = [], None
        else:
            previous, provenance = load_previous(args.pair, specs, hashes, args.mock) if args.markets == 90 else ([], None)
        freeze(folder / "config.json", {
            "study": f"mixed_{study_name}", "pair": args.pair, "selection_seed": SEED,
            "first_model_role": slots, "models": runner.MODELS, "markets": markets,
            "expected_episodes": expected_count, "horizon": 8, "reasoning_effort": runner.GENERATION["reasoning_effort"], "max_tokens": runner.MAX_TOKENS,
            "model_identity_disclosed": False, "conditions": runner.CONDITIONS,
            "budget_usd": args.budget_usd, "mock": args.mock, "code_sha256": hashes,
            "reused_results": provenance, "opposite_assignment_reference": reference_provenance})
        for name, source in sources.items():
            target = folder / "source_snapshot" / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashes[name]:
                raise RuntimeError("Source snapshot differs")
        if args.execute:
            runner.load_credentials()
        budget = runner.Budget(folder / "budget.jsonl", args.budget_usd)
        if provenance and "imported_previous_run" not in budget.charges:
            cost = provenance["accounted_cost_usd"]
            if budget.spent + cost > args.budget_usd:
                raise RuntimeError("Budget is below the cost already incurred")
            runner.append_row(folder / "budget.jsonl", {"id": "imported_previous_run",
                                                       "state": "imported", "charge": cost})
            budget.charges["imported_previous_run"] = cost
        rows = runner.read_rows(folder / "episodes.jsonl")
        existing = {r["episode_id"]: r for r in rows}
        (folder / "readable_episodes").mkdir(exist_ok=True)
        for row in previous:
            if row["episode_id"] in existing:
                if existing[row["episode_id"]] != row:
                    raise RuntimeError("Saved copy differs from imported episode")
            else:
                runner.append_row(folder / "episodes.jsonl", row)
                rows.append(row)
            (folder / "readable_episodes" / (row["episode_id"] + ".md")).write_text(readable(row))
        expected = {runner.identifier(s) for s in specs}
        completed = {r["episode_id"] for r in rows}
        if len(completed) != len(rows) or not completed <= expected:
            raise RuntimeError("Duplicate or unexpected saved episodes")
        pending = iter(s for s in specs if runner.identifier(s) not in completed)
        print(f"Reusing {len(previous)} existing episodes; {expected_count-len(completed)} remain. "
              + ("Budget covers this new batch only." if args.reverse_assignments else "Budget includes prior cost."))
        stop = run_pending(
            folder=folder, pending=pending, rows=rows, expected=expected_count,
            budget=budget, mock=args.mock, workers=args.workers,
            run_one=runner.run_one, readable=readable,
        )
        audit = {"complete": len(rows) == expected_count, "episodes": len(rows), "expected_episodes": expected_count,
                 "replay_verified": all(r.get("replay_verified") for r in rows),
                 "frozen_source_verified": True, "accounted_cost_usd": budget.spent}
        (folder / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        (folder / "analysis.json").write_text(json.dumps(runner.summarize(rows), indent=2) + "\n")
        if stop:
            raise SystemExit("Paused on failure or budget limit; inspect failures.jsonl before resuming.")
        print("COMPLETE")


if __name__ == "__main__":
    main()
