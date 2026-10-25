"""Run any supported model pair on matched markets; preview by default."""
from __future__ import annotations
import argparse
import math
from pathlib import Path

from experiment_runtime.models import generation_settings, model_catalog
from experiment_runtime import negotiations
from experiment_runtime.negotiations import (
    append_row, encoded, identifier, read_rows,
)

from .game import play_episode, action_schema
from .provider import Model, load_env
from .markets import make_extraction_region_grid
from .. import ACTION_INTERFACE_VERSION

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src"
ARENA, HORIZON, DEFAULT_BUDGET = "trade", 8, 40.0
CONDITIONS = ("inventory_only", "rank_only", "exact_values")
ASSIGNMENTS = ({"A": "gpt51", "B": "claudesonnet5"},
               {"A": "claudesonnet5", "B": "gpt51"})


def frozen_markets():
    markets = make_extraction_region_grid(2)
    # Match the actual paper markets, not merely a newly generated panel.
    reference = ROOT / "data/main_markets.jsonl"
    previous = {row["market_id"]: row for row in read_rows(reference)}
    fields = ("inventory_a", "inventory_b", "utility_a", "utility_b", "resources")
    if len(markets) != 90 or len(previous) != 90 or any(
        any(m[key] != previous.get(m["market_id"], {}).get(key) for key in fields)
        for m in markets
    ):
        raise ValueError("generated markets differ from the saved main study")
    return [previous[m["market_id"]] for m in markets]


def actor_sequence(market):
    return ["A", "B"] * 4


def check_role(prompt, actor):
    if not prompt.startswith(f"You are Agent {actor} in "):
        raise ValueError("active actor and prompt header differ")


def make_client(preset):
    model = MODELS[preset]
    return Model(provider=model["provider"], model=model["model"], reasoning_effort=GENERATION["reasoning_effort"],
                 max_tokens=MAX_TOKENS, openrouter_provider=model["backend"])


def load_credentials():
    load_env(ROOT / ".env")
    load_env(ROOT.parents[1] / ".env")


def call_client(client, prompt, kwargs, market, records):
    start = len(client.call_log)
    try:
        return client(prompt, action_schema(tuple(market["inventory_a"]), ACTION_INTERFACE_VERSION))
    finally:
        records.extend(client.call_log[start:])


def validate_records(records, preset):
    successful = [r for r in records if not r.get("error")]
    if len(successful) != 1:
        raise ValueError("unexpected successful call count")
    record, model = successful[0], MODELS[preset]
    if record["reported_model"] != model["model"]:
        raise ValueError("returned model differs")
    if model["backend"] and record["reported_provider"].lower() != model["backend"].lower():
        raise ValueError("returned backend differs")


def play(market, condition, respond, episode_id):
    return play_episode(episode_id, condition, market, HORIZON, respond,
                        agent_policy="plain_dialogue_self_interest", first_actor="A",
                        action_interface=ACTION_INTERFACE_VERSION)


def make_mock():
    return lambda prompt, **kwargs: '{"action":"wait","you_give":{"X":0,"Y":0},"you_receive":{"X":0,"Y":0},"message":"Wait."}'


def measures(row):
    return {"normalized_welfare": row["outcome"]["normalized_welfare"],
            "agreement": float(row["agreement"] is not None)}

MODEL_CATALOG = model_catalog("mixed")
MODELS = {name: MODEL_CATALOG[name] for name in ("gpt51", "claudesonnet5")}
PAIRS = {"gpt51_sonnet5": ("gpt51", "claudesonnet5"),
         "gpt51_gemini": ("gpt51", "gemini"),
         "sonnet5_gemini": ("claudesonnet5", "gemini"),
         "grok43_gemini": ("grok43", "gemini"),
         "sonnet5_grok43": ("claudesonnet5", "grok43")}


def configure_pair(name):
    global MODELS, ASSIGNMENTS
    first, second = PAIRS[name]
    MODELS = {model: MODEL_CATALOG[model] for model in (first, second)}
    roles = ("A", "B")
    ASSIGNMENTS = ({roles[0]: first, roles[1]: second},
                   {roles[0]: second, roles[1]: first})


GENERATION = generation_settings("trade")
MAX_TOKENS = GENERATION["max_tokens"]


class Budget(negotiations.Budget):
    """Use this study's model rates and output limit."""

    def __init__(self, path, ceiling):
        super().__init__(path, ceiling, MODELS, MAX_TOKENS)


def source_files():
    return negotiations.source_files(ROOT, SOURCE_ROOT)


def run_one(spec, budget, mock):
    return negotiations.run_negotiation(
        spec, budget, mock, actor_sequence=actor_sequence, check_role=check_role,
        make_client=make_client, call_client=call_client,
        validate_records=validate_records, play=play, make_mock=make_mock,
    )


def specs_for(markets):
    return [(market, condition, assignment)
            for market in markets for condition in CONDITIONS for assignment in ASSIGNMENTS]


def summarize(rows):
    return negotiations.summarize(rows, measures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=PAIRS, default="gpt51_sonnet5")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--mock", action="store_true")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Run only the first N pending episodes, for smoke tests")
    args = parser.parse_args()
    configure_pair(args.pair)
    if not math.isfinite(args.budget_usd) or args.budget_usd <= 0 or args.workers <= 0:
        parser.error("budget and workers must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("limit must be positive")
    markets = frozen_markets()
    specs = specs_for(markets)
    print(f"{ARENA}: {len(markets)} markets x 3 conditions x 2 assignments = {len(specs)} episodes; horizon={HORIZON}", flush=True)
    if not args.execute and not args.mock:
        print("DRY RUN: no files or API calls.")
        return
    negotiations.run_study(
        args, markets=markets, specs=specs,
        config_fields={"arena": ARENA, "models": MODELS, "conditions": CONDITIONS,
                       "assignments": ASSIGNMENTS, "horizon": HORIZON,
                       "max_tokens": MAX_TOKENS},
        source_files=source_files, load_credentials=load_credentials,
        make_budget=Budget, run_one=run_one, summarize=summarize,
    )


if __name__ == "__main__":
    main()
