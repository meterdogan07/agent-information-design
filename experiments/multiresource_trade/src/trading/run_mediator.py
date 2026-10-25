#!/usr/bin/env python
"""Run the open-proposal mediator experiment; dry-run unless ``--execute``."""

from __future__ import annotations

from experiment_runtime.models import generation_settings, model_settings

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiment_runtime import run_directory_lock

from ..analysis.metrics import analyze_mediator
from ..analysis.transcripts import write_readable_episodes
from .markets import (
    EXTRACTION_ROLES,
    MARKET_PANELS,
    generate_markets,
    market_panel,
)
from .mediator import (
    CONSULTED_MEDIATOR_ACTION_INTERFACE_VERSION,
    CONSULTED_MEDIATOR_PROMPT_VERSION,
    CONSULTED_MEDIATOR_PROTOCOL_VERSION,
    FEEDBACK_MEDIATOR_ACTION_INTERFACE_VERSION,
    FEEDBACK_MEDIATOR_PROMPT_VERSION,
    FEEDBACK_MEDIATOR_PROTOCOL_VERSION,
    MEDIATOR_ACTION_INTERFACE_VERSION,
    MEDIATOR_CONDITIONS,
    MEDIATOR_MODES,
    MEDIATOR_PROMPT_VERSION,
    MEDIATOR_PROTOCOL_VERSION,
    play_consulted_mediator_episode,
    play_feedback_mediator_episode,
    play_mediator_episode,
)
from .mediator_prompts import (
    build_consultation_prompt,
    build_consulted_mediator_prompt,
    build_feedback_review_prompt,
    build_final_review_prompt,
    build_mediator_prompt,
    build_private_review_prompt,
    build_revision_prompt,
)
from .provider import (
    PROVIDERS,
    REASONING_EFFORTS,
    Model,
    load_env,
    model_uses_temperature,
)
from .run import (
    ROOT,
    execute_specifications,
    filter_extraction_region,
    load_completed_episodes,
    prepare_result_artifact,
    save_markets,
    select_markets,
)

GENERATION = generation_settings("trade")


# ---------------------------------------------------------------------------
# Mediator episode identity and prompt inspection
# ---------------------------------------------------------------------------


MODE_DESIGNS = {
    "one_shot": {
        "mechanism": "mediator",
        "protocol": MEDIATOR_PROTOCOL_VERSION,
        "action_interface": MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt": MEDIATOR_PROMPT_VERSION,
        "maximum_model_calls": 3,
    },
    "consultation": {
        "mechanism": "consulted_mediator",
        "protocol": CONSULTED_MEDIATOR_PROTOCOL_VERSION,
        "action_interface": CONSULTED_MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt": CONSULTED_MEDIATOR_PROMPT_VERSION,
        "maximum_model_calls": 5,
    },
    "feedback": {
        "mechanism": "feedback_mediator",
        "protocol": FEEDBACK_MEDIATOR_PROTOCOL_VERSION,
        "action_interface": FEEDBACK_MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt": FEEDBACK_MEDIATOR_PROMPT_VERSION,
        "maximum_model_calls": 6,
    },
}


def episode_identifier(
    market_id: str,
    condition: str,
    replication: int,
    mediator_mode: str = "one_shot",
) -> str:
    """Stable identifier shared by execution and resume."""
    mechanism = MODE_DESIGNS[mediator_mode]["mechanism"]
    return f"{market_id}__{mechanism}__{condition}__r{replication}"


@dataclass(frozen=True)
class EpisodeSpec:
    market: dict[str, Any]
    condition: str
    replication: int
    mediator_mode: str = "one_shot"

    @property
    def identifier(self) -> str:
        return episode_identifier(
            self.market["market_id"], self.condition, self.replication,
            self.mediator_mode,
        )


def show_example(
    market: dict[str, Any],
    conditions: list[str],
    mediator_mode: str,
) -> None:
    """Print all model-facing prompts without making an API call."""
    print("RESEARCHER VIEW OF ONE MARKET\n")
    print(json.dumps(market, indent=2, sort_keys=True))
    example = {
        "a_gives": {
            item: 1
            for item, quantity in market["inventory_a"].items()
            if quantity > market["inventory_b"][item]
        },
        "b_gives": {
            item: 1
            for item, quantity in market["inventory_b"].items()
            if quantity > market["inventory_a"][item]
        },
    }
    for condition in conditions:
        print(f"\n{'=' * 20} MEDIATOR: {condition} {'=' * 20}\n")
        if mediator_mode == "consultation":
            consultation_rows = []
            for actor in ("A", "B"):
                print(f"\n{'=' * 20} CONSULTATION {actor} {'=' * 20}\n")
                print(build_consultation_prompt(actor, market))
                consultation_rows.append({
                    "actor": actor,
                    "consultation": {
                        "action": "seek_trade",
                        "you_give": example["a_gives" if actor == "A" else "b_gives"],
                        "you_receive": example["b_gives" if actor == "A" else "a_gives"],
                        "flexibility": "quantity_flexible",
                        "message": "I prefer this exchange direction.",
                    },
                })
            print(f"\n{'=' * 20} CONSULTED MEDIATOR: {condition} {'=' * 20}\n")
            print(build_consulted_mediator_prompt(
                condition, market, consultation_rows
            ))
            for actor in ("A", "B"):
                print(f"\n{'=' * 20} PRIVATE REVIEWER {actor} {'=' * 20}\n")
                print(build_private_review_prompt(actor, market, example))
        else:
            print(build_mediator_prompt(condition, market))
        if mediator_mode == "feedback":
            feedback_rows = []
            for actor in ("A", "B"):
                print(f"\n{'=' * 20} INITIAL FEEDBACK {actor} {'=' * 20}\n")
                print(build_feedback_review_prompt(actor, market, example))
                feedback_rows.append({
                    "actor": actor,
                    "feedback": {
                        "decision": "reject",
                        "requested_change": "receive_more",
                        "message": "I would prefer to receive more of the offered resource.",
                    },
                })
            print(f"\n{'=' * 20} MEDIATOR REVISION: {condition} {'=' * 20}\n")
            print(build_revision_prompt(condition, market, example, feedback_rows))
            for actor in ("A", "B"):
                print(f"\n{'=' * 20} FINAL REVIEWER {actor} {'=' * 20}\n")
                print(build_final_review_prompt(actor, market, example))
        elif mediator_mode == "one_shot":
            for actor in ("A", "B"):
                print(f"\n{'=' * 20} PRIVATE REVIEWER {actor} {'=' * 20}\n")
                print(build_private_review_prompt(actor, market, example))


# ---------------------------------------------------------------------------
# Command-line configuration
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, default="openai")
    parser.add_argument("--model", default=model_settings("gpt51")["model"])
    parser.add_argument(
        "--mediator-mode", choices=MEDIATOR_MODES, default="one_shot"
    )
    parser.add_argument(
        "--reasoning-effort", choices=REASONING_EFFORTS, default=GENERATION["reasoning_effort"]
    )
    parser.add_argument(
        "--openrouter-provider",
        help="Pin one OpenRouter backend and disable fallback.",
    )
    parser.add_argument("--abundant-quantity", type=int, default=5)
    parser.add_argument("--resources", type=int, choices=(2, 4, 6), default=2)
    parser.add_argument(
        "--market-panel",
        choices=MARKET_PANELS,
        default="responder_extraction_crossed",
    )
    parser.add_argument(
        "--market-ids",
        nargs="+",
        help="Run or inspect only these exact IDs from the selected market panel.",
    )
    parser.add_argument(
        "--extraction-roles",
        nargs="+",
        choices=EXTRACTION_ROLES,
        help="Select roles from extraction_region_grid only.",
    )
    parser.add_argument("--min-extraction-pressure", type=float)
    parser.add_argument("--max-extraction-pressure", type=float)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=MEDIATOR_CONDITIONS,
        default=list(MEDIATOR_CONDITIONS),
    )
    parser.add_argument("--replications", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=GENERATION["runner_max_tokens"])
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature; omit to use the model/provider default.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "results" / "new_runs" / "mediator",
    )
    parser.add_argument("--show-example", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.replications < 1:
        parser.error("replications must be positive")
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.max_tokens < 1:
        parser.error("max tokens must be positive")
    if args.abundant_quantity <= 1:
        parser.error("abundant quantity must be greater than 1")
    if args.temperature is not None and not 0.0 <= args.temperature <= 2.0:
        parser.error("temperature must be between 0 and 2")
    if len(set(args.conditions)) != len(args.conditions):
        parser.error("conditions must not contain duplicates")
    if args.openrouter_provider and args.provider != "openrouter":
        parser.error("--openrouter-provider requires --provider openrouter")
    if args.extraction_roles and len(set(args.extraction_roles)) != len(
        args.extraction_roles
    ):
        parser.error("extraction roles must not contain duplicates")
    for pressure in (args.min_extraction_pressure, args.max_extraction_pressure):
        if pressure is not None and not 0.0 <= pressure <= 1.0:
            parser.error("extraction pressure bounds must be between 0 and 1")
    if (
        args.min_extraction_pressure is not None
        and args.max_extraction_pressure is not None
        and args.min_extraction_pressure > args.max_extraction_pressure
    ):
        parser.error("minimum extraction pressure must not exceed maximum")
    return args


# ---------------------------------------------------------------------------
# Mediator experiment execution
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    try:
        all_markets = generate_markets(
            args.abundant_quantity, args.resources, args.market_panel
        )
        region_markets = filter_extraction_region(
            all_markets,
            args.market_panel,
            args.extraction_roles,
            args.min_extraction_pressure,
            args.max_extraction_pressure,
        )
        markets = select_markets(region_markets, args.market_ids)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    market_path = save_markets(
        all_markets, args.market_panel, args.abundant_quantity, args.resources
    )
    if args.show_example:
        show_example(markets[0], args.conditions, args.mediator_mode)
        return

    episode_count = len(markets) * len(args.conditions) * args.replications
    mode_design = MODE_DESIGNS[args.mediator_mode]
    design = {
        "mechanism": mode_design["mechanism"],
        "protocol": mode_design["protocol"],
        "action_interface": mode_design["action_interface"],
        "prompt": mode_design["prompt"],
        "markets": len(markets),
        "market_file": str(market_path),
        "market_panel": args.market_panel,
        "abundant_quantity": (
            None
            if market_panel(args.market_panel).varied_quantity
            else args.abundant_quantity
        ),
        "resources": args.resources,
        "conditions": args.conditions,
        "replications": args.replications,
        "episodes": episode_count,
        "maximum_model_calls": (
            episode_count * mode_design["maximum_model_calls"]
        ),
        "requested_temperature": args.temperature,
        "temperature_sent": (
            args.temperature
            if args.temperature is not None and model_uses_temperature(
                args.provider, args.model, args.reasoning_effort
            )
            else None
        ),
        "workers": args.workers,
    }
    if args.market_ids:
        design["selected_market_ids"] = args.market_ids
    if args.extraction_roles:
        design["extraction_roles"] = args.extraction_roles
    if args.min_extraction_pressure is not None:
        design["min_extraction_pressure"] = args.min_extraction_pressure
    if args.max_extraction_pressure is not None:
        design["max_extraction_pressure"] = args.max_extraction_pressure
    print(json.dumps(design, indent=2))
    if not args.execute:
        print("DRY RUN: add --execute to call the model.")
        return

    load_env(ROOT / ".env")
    load_env(ROOT.parents[1] / ".env")
    config = {
        **design,
        "provider": args.provider,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "openrouter_provider": args.openrouter_provider,
        "max_tokens": args.max_tokens,
        "temperature": (
            args.temperature
            if args.temperature is not None and model_uses_temperature(
                args.provider, args.model, args.reasoning_effort
            )
            else None
        ),
    }
    # These are runtime estimates, not protocol settings.
    config.pop("maximum_model_calls")
    config.pop("workers")
    with run_directory_lock(args.results_dir):
        artifact = prepare_result_artifact(args.results_dir, config)
        completed = load_completed_episodes(artifact)

        specifications = [
            EpisodeSpec(market, condition, replication, args.mediator_mode)
            for market in markets
            for condition in args.conditions
            for replication in range(args.replications)
        ]

        def run_specification(
            number: int,
            specification: EpisodeSpec,
        ) -> tuple[int, str, dict[str, Any]]:
            episode_id = specification.identifier
            episode_model: Model | None = None
            prefix = f"[{number}/{len(specifications)}] {episode_id}"
            print(
                f"{prefix}: started; condition={specification.condition}; "
                f"mode={specification.mediator_mode}",
                flush=True,
            )
            try:
                episode_model = Model(
                    provider=args.provider,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    max_tokens=args.max_tokens,
                    openrouter_provider=args.openrouter_provider,
                    temperature=args.temperature,
                    progress=lambda message: print(
                        f"{prefix}: {message}", flush=True
                    ),
                )
                if specification.mediator_mode == "feedback":
                    row = play_feedback_mediator_episode(
                        episode_id,
                        specification.condition,
                        specification.market,
                        episode_model,
                    )
                elif specification.mediator_mode == "consultation":
                    row = play_consulted_mediator_episode(
                        episode_id,
                        specification.condition,
                        specification.market,
                        episode_model,
                    )
                else:
                    row = play_mediator_episode(
                        episode_id,
                        specification.condition,
                        specification.market,
                        episode_model,
                    )
                row.update(
                    status="completed",
                    replication=specification.replication,
                    model_calls=episode_model.call_log,
                )
            except Exception as error:
                row = {
                    "episode_id": episode_id,
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                    "model_calls": (
                        episode_model.call_log if episode_model is not None else []
                    ),
                }
            return number, episode_id, row

        completed = execute_specifications(
            specifications,
            completed,
            artifact,
            args.workers,
            lambda specification: specification.identifier,
            run_specification,
        )

        analysis = analyze_mediator(list(completed.values()))
        (args.results_dir / "analysis.json").write_text(
            json.dumps(analysis, indent=2, sort_keys=True) + "\n"
        )
        write_readable_episodes(args.results_dir)
        print(json.dumps(analysis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
