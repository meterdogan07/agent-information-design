"""Run any supported model pair on matched markets; preview by default."""
from __future__ import annotations
import argparse
import math
from pathlib import Path

from experiment_runtime.models import generation_settings, model_catalog
from experiment_runtime import negotiations
from experiment_runtime.negotiations import (
    read_rows,
)

from . import design, game
from .models import ModelClient, load_env, validate_trace

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
ARENA, HORIZON, DEFAULT_BUDGET = "bargaining", 6, 10.0
CONDITIONS = ("hidden", "coarse", "exact")
ASSIGNMENTS = ({"seller": "gpt51", "buyer": "claudesonnet5"},
               {"seller": "claudesonnet5", "buyer": "gpt51"})


def frozen_markets():
    markets = design.generate_markets(30, design.MARKET_SEED, design.PROFITABLE_MIRRORED_PANEL_ID)
    reference = ROOT / "data/main_markets.jsonl"
    previous = {row["market_id"]: row for row in read_rows(reference)}
    if len(markets) != 30 or {m["market_id"]: m for m in markets} != previous:
        raise ValueError("generated markets differ from the saved main study")
    return markets


def actor_sequence(market):
    return game.actor_order(market["first_mover"], HORIZON)


def check_role(prompt, actor):
    # role card is before the public conversation, which may contain role claims.
    private_header = prompt.split("PUBLIC HISTORY")[0]
    if f"You are the {actor.upper()}" not in private_header:
        raise ValueError("active actor and prompt role differ")


def make_client(preset):
    model = MODELS[preset]
    return ModelClient(provider=model["provider"], model=model["model"], backend=model["backend"],
                       reasoning_effort=GENERATION["reasoning_effort"], retries=GENERATION["attempts"])


def load_credentials():
    load_env()


def call_client(client, prompt, kwargs, market, records):
    client.start_trace()
    try:
        return client(prompt, **kwargs)
    finally:
        records.extend(client.end_trace())


def validate_records(records, preset):
    model = MODELS[preset]
    error = validate_trace(records, 1, model["provider"], model["backend"],
                           model["model"], GENERATION["reasoning_effort"], MAX_TOKENS)
    if error:
        raise ValueError(error)


def play(market, condition, respond, episode_id):
    return game.run_episode(market, condition, respond, HORIZON, MAX_TOKENS)


def make_mock():
    return game.make_mock_model()


def measures(row):
    outcome = row["outcome"]
    market = row["market"]
    price = outcome.get("accepted_price")
    executed = outcome["accepted_ir"]
    surplus = market["buyer_value"] - market["seller_cost"]
    seller = (price - market["seller_cost"]) / surplus if executed else 0.0
    buyer = (market["buyer_value"] - price) / surplus if executed else 0.0
    return {"agreement": float(executed), "normalized_nash_product": 4 * seller * buyer,
            "seller_gain_normalized": seller, "buyer_gain_normalized": buyer}

MODEL_CATALOG = model_catalog("mixed")
MODELS = {name: MODEL_CATALOG[name] for name in ("gpt51", "claudesonnet5")}
PAIRS = {"gpt51_sonnet5": ("gpt51", "claudesonnet5"),
         "gpt51_gemini": ("gpt51", "gemini"),
         "sonnet5_gemini": ("claudesonnet5", "gemini"),
         "grok43_gemini": ("grok43", "gemini"),
         "gpt51_grok43": ("gpt51", "grok43"),
         "sonnet5_grok43": ("claudesonnet5", "grok43")}


def configure_pair(name):
    global MODELS, ASSIGNMENTS
    first, second = PAIRS[name]
    MODELS = {model: MODEL_CATALOG[model] for model in (first, second)}
    roles = ("seller", "buyer")
    ASSIGNMENTS = ({roles[0]: first, roles[1]: second},
                   {roles[0]: second, roles[1]: first})


GENERATION = generation_settings("bargaining")
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
