"""Frozen, prospective utility-combination holdout for disclosure selection.

No model calls unless --execute is supplied. The first preparation freezes the
markets, calibration choices, source hashes and protocol; later calls verify
them rather than silently changing the study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

from .market_core import make_market, extraction_opportunity
from experiment_runtime.models import CONFIG_FILES, generation_settings, model_settings

ROOT = Path(__file__).resolve().parents[2]
PANEL = "calibration_holdout60_v1"
SEED = 20260916
CONDITIONS = ("inventory_only", "rank_only", "exact_values")
MODELS = {name: (model_settings(name)["model"], model_settings(name)["tag"] + "_engineids")
          for name in ("gpt5mini", "gpt51")}
GENERATION = generation_settings("trade")


def calibration_folder(model, suffix):
    """Find the recorded study by its model identity, not a historical folder nickname."""
    name = ("extraction_region_grid_full90_20260831" if suffix == "full90"
            else "extraction_region_grid_least90_20260901")
    matches = [path.parent for path in (ROOT / "results/new_runs/ablations").glob(f"*/{name}/config.json")
               if json.loads(path.read_text()).get("model") == model]
    if len(matches) != 1:
        raise ValueError(f"Expected one calibration source for {model} / {suffix}; found {len(matches)}")
    return matches[0]


def geometry(market):
    a, b = (extraction_opportunity(market, role)["normalized_appropriation_advantage"]
            for role in ("A", "B"))
    return "equal" if abs(a-b) < 1e-12 else "a_higher" if a > b else "b_higher"


def generate_holdout(resource_count=2):
    if resource_count != 2:
        raise ValueError("The frozen calibration holdout uses exactly two resources")
    levels = [n for n in range(5, 46) if n not in {5, 10, 20, 30, 40, 45}]
    rng = random.Random(SEED)
    used = set()
    markets = []
    for group in ("a_higher", "b_higher", "equal"):
        for q in range(2, 7):
            candidates = [(a, b) for a in levels for b in levels
                          if (a == b) == (group == "equal")]
            rng.shuffle(candidates)
            chosen = 0
            for a, b in candidates:
                signature = tuple(sorted((a, b)))
                # Do not count role reversals or new inventory quantities as
                # independent preference combinations. (25,25) is also used
                # by the earlier equal-preference 3/9 utility construction.
                if signature in used or signature == (25, 25):
                    continue
                market = make_market(f"holdout_{a}", f"holdout_{b}",
                                     "xy" if chosen % 2 == 0 else "yx", q, 2,
                                     (a, 100-a), (b, 100-b))
                if geometry(market) != group:
                    continue
                market.update(market_panel=PANEL, market_group=group,
                              profile_index=len(markets)+1, generation_seed=SEED,
                              designed_first_actor="A",
                              normalized_base_values_a=[a, 100-a],
                              normalized_base_values_b=[b, 100-b])
                market["market_id"] = f"cal60_{group}_q{q}_a{a}_b{b}_{market['orientation']}"
                markets.append(market)
                used.add(signature)
                chosen += 1
                if chosen == 4:
                    break
            if chosen != 4:
                raise ValueError(f"Insufficient unused markets for {group}, q={q}")
    return markets


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    from .run import save_markets
    markets = generate_holdout()
    market_path = save_markets(markets, PANEL, 5, 2)
    calibration = {}
    for preset, (model, tag) in MODELS.items():
        scores = {c: [] for c in CONDITIONS}
        ids = {c: set() for c in CONDITIONS}
        sources = {}
        for suffix in ("full90", "least90"):
            folder = calibration_folder(model, suffix)
            config = json.loads((folder / "config.json").read_text())
            for key, value in {"model": model, "provider": "openai", "max_tokens": GENERATION["max_tokens"],
                               "reasoning_effort": GENERATION["reasoning_effort"], "horizons": [8],
                               "first_mover": "A", "agent_policy": "plain_dialogue_self_interest"}.items():
                if config[key] != value:
                    raise ValueError(f"Calibration setting differs: {preset} {key}")
            for name in ("config.json", "episodes.jsonl"):
                sources[str((folder / name).relative_to(ROOT))] = digest(folder / name)
            for line in (folder / "episodes.jsonl").read_text().splitlines():
                e = json.loads(line)
                if e["status"] != "completed":
                    raise ValueError("Calibration contains an incomplete negotiation")
                c, key = e["condition"], e["market"]["market_id"]
                if key in ids[c]:
                    raise ValueError("Duplicate calibration episode")
                ids[c].add(key)
                scores[c].append(e["outcome"]["normalized_welfare"])
        if any(len(v) != 90 for v in scores.values()) or any(v != ids[CONDITIONS[0]] for v in ids.values()):
            raise ValueError("Calibration must contain the same 90 markets at all levels")
        means = {c: sum(v)/len(v) for c, v in scores.items()}
        calibration[preset] = {"model": model, "means": means,
                               "selected_condition": max(CONDITIONS, key=means.get),
                               "source_sha256": sources}
    code_paths = [Path(__file__), ROOT / "src/trading/markets.py", ROOT / "src/trading/game.py",
                  ROOT / "src/trading/p2p_prompts.py", ROOT / "src/trading/provider.py",
                  ROOT / "src/trading/run.py", *CONFIG_FILES,
                  *(ROOT.parents[1] / "experiment_runtime").glob("*.py")]
    plan = {
        "study": PANEL, "seed": SEED, "markets": len(markets),
        "market_file": str(market_path.relative_to(ROOT)), "market_sha256": digest(market_path),
        "calibration": calibration,
        "selection_rule": "Highest mean normalized welfare on the existing 90-market grid; ties use least, coarse, full order.",
        "primary_comparison": "Frozen selection versus full disclosure, separately per model; also report all three levels and paired differences.",
        "test_aggregation": "Equal weight for all 60 markets (20 per utility-defined group).",
        "uncertainty": "Stratified paired bootstrap within market groups, keeping conditions together; 95% intervals.",
        "scope": "New utility combinations within the same two-resource family, not an IID split or cross-mechanism transfer.",
        "settings": {"conditions": list(CONDITIONS), "messages": 8, "first_mover": "A",
                     "reasoning_effort": GENERATION["reasoning_effort"], "max_tokens": GENERATION["max_tokens"], "replications": 1,
                     "agent_policy": "plain_dialogue_self_interest",
                     "action_interface": "engine_owned_offer_ids_20260822"},
        "code_sha256": {str(p.relative_to(ROOT.parents[1])): digest(p) for p in code_paths},
    }
    path = ROOT / "data/calibration" / f"{PANEL}_plan.json"
    payload = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != payload:
        raise ValueError("Frozen plan differs. Do not overwrite or change a study after launch.")
    if not path.exists():
        path.write_text(payload)
    return path, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    path, plan = freeze()
    model, tag = MODELS[args.model]
    selected = plan["calibration"][args.model]["selected_condition"]
    print(f"Frozen plan: {path}\nModel: {model}; selected disclosure: {selected}", flush=True)
    command = [sys.executable, "-m", "src.trading.run", "--provider", "openai", "--model", model,
               "--reasoning-effort", GENERATION["reasoning_effort"], "--max-tokens", str(GENERATION["max_tokens"]), "--workers", str(args.workers),
               "--market-panel", PANEL, "--resources", "2", "--conditions", *CONDITIONS,
               "--horizons", "8", "--first-mover", "A", "--replications", "1",
               "--agent-policy", "plain_dialogue_self_interest", "--action-interface", "engine_owned_offer_ids_20260822",
               "--results-dir", str(ROOT / "results/new_runs/ablations" / tag / PANEL)]
    if args.execute:
        command.append("--execute")
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
