#!/usr/bin/env python
"""Run the experiment. Without ``--execute`` this only prints the design."""

from __future__ import annotations

from experiment_runtime.models import generation_settings, model_settings

import argparse
import concurrent.futures
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from experiment_runtime import run_directory_lock

from .. import ACTION_INTERFACE_VERSION, ACTION_INTERFACE_VERSIONS, PROTOCOL_VERSION
from ..analysis.metrics import analyze
from ..analysis.transcripts import write_readable_episodes
from .game import action_schema, play_episode
from .markets import (
    EXTRACTION_ROLES,
    MARKET_CATALOG,
    MARKET_PANELS,
    generate_markets,
    market_panel,
)
from .p2p_prompts import (
    AGENT_POLICIES,
    CONDITIONS,
    build_prompt,
    prompt_version,
)
from .provider import (
    PROVIDERS,
    REASONING_EFFORTS,
    Model,
    load_env,
    model_uses_temperature,
)

GENERATION = generation_settings("trade")


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Market selection and inspectable snapshots
# ---------------------------------------------------------------------------


def show_market_panels() -> None:
    """Print the available panels by purpose."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, definition in MARKET_CATALOG.items():
        groups.setdefault(definition.group, []).append((name, definition.label))
    for group, panels in groups.items():
        print(group.replace("_", " ").upper())
        for name, label in panels:
            print(f"  {name:<40} {label}")
        print()


def select_markets(
    markets: list[dict[str, Any]], market_ids: list[str] | None
) -> list[dict[str, Any]]:
    """Select named markets in request order; keep full-panel behavior by default."""
    if not market_ids:
        return markets
    if len(market_ids) != len(set(market_ids)):
        raise ValueError("market IDs must not contain duplicates")
    by_id = {market["market_id"]: market for market in markets}
    unknown = [market_id for market_id in market_ids if market_id not in by_id]
    if unknown:
        raise ValueError(f"unknown market ID(s): {', '.join(unknown)}")
    return [by_id[market_id] for market_id in market_ids]


def filter_extraction_region(
    markets: list[dict[str, Any]],
    panel: str,
    roles: list[str] | None,
    minimum_pressure: float | None,
    maximum_pressure: float | None,
) -> list[dict[str, Any]]:
    """Select a declared slice of the parameterized grid."""
    if roles is None and minimum_pressure is None and maximum_pressure is None:
        return markets
    if panel != "extraction_region_grid":
        raise ValueError("extraction-region filters require that market panel")
    selected = [
        market
        for market in markets
        if (roles is None or market["extraction_role"] in roles)
        and (
            minimum_pressure is None
            or market["extraction_pressure"] >= minimum_pressure
        )
        and (
            maximum_pressure is None
            or market["extraction_pressure"] <= maximum_pressure
        )
    ]
    if not selected:
        raise ValueError("extraction-region filters selected no markets")
    return selected


def save_markets(
    markets: list[dict[str, Any]],
    panel: str,
    abundant_quantity: int,
    resource_count: int,
) -> Path:
    """Keep one human-inspectable copy of the deterministic market panel."""
    definition = market_panel(panel)
    data_group = Path(definition.data_group)
    quantity_tag = (
        "qvaried_1"
        if definition.varied_quantity
        else f"q{abundant_quantity}_1"
    )
    path = (
        ROOT / "data" / data_group
        / (
            f"{panel}_resources{resource_count}_{quantity_tag}"
            f"__{PROTOCOL_VERSION}.jsonl"
        )
    )
    text = "".join(json.dumps(market, sort_keys=True) + "\n" for market in markets)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != text:
            raise RuntimeError(f"{path} does not match the market generator")
        return path
    path.write_text(text)
    return path


def choose_first_actor(market: dict[str, Any], first_mover: str) -> str:
    """Counterbalance mover identity using the otherwise irrelevant label swap."""
    if first_mover in {"A", "B"}:
        return first_mover
    return "A" if market["orientation"] == "xy" else "B"


# ---------------------------------------------------------------------------
# Result persistence and parallel execution
# ---------------------------------------------------------------------------


def prepare_result_artifact(results_dir: Path, config: dict[str, Any]) -> Path:
    """Freeze one configuration and return its resumable episode artifact."""
    results_dir.mkdir(parents=True, exist_ok=True)
    config_path = results_dir / "config.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise RuntimeError(
            "results directory contains a different experiment configuration"
        )
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    return results_dir / "episodes.jsonl"


def load_completed_episodes(artifact: Path) -> dict[str, dict[str, Any]]:
    """Load the first completed row per episode; failed rows remain retryable.

    Keeping the first row makes interrupted/resumed runs deterministic if two
    overlapping processes happen to finish the same episode.  The append-only
    JSONL remains untouched so both raw trajectories are available for audit.
    """
    completed = {}
    duplicate_ids = set()
    if artifact.exists():
        for line in artifact.read_text().splitlines():
            row = json.loads(line)
            if row.get("status") == "completed":
                episode_id = row["episode_id"]
                if episode_id in completed:
                    duplicate_ids.add(episode_id)
                else:
                    completed[episode_id] = row
    if duplicate_ids:
        print(
            f"Warning: ignored later completed rows for {len(duplicate_ids)} "
            "duplicate episode ID(s); calculations use the first completed "
            "row.",
            file=sys.stderr,
        )
    return completed


def execute_specifications(
    specifications: Sequence[Any],
    completed: dict[str, dict[str, Any]],
    artifact: Path,
    workers: int,
    identifier: Callable[[Any], str],
    run_one: Callable[[int, Any], tuple[int, str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Run independent episodes concurrently while writing results serially.

    Concurrency is across complete negotiations only. Each negotiation still
    executes its messages sequentially inside ``run_one``. Completed episodes
    remain the authoritative resumable artifact; failed attempts are retained
    separately so they cannot pollute analysis or readable transcripts.
    """
    failed_artifact = artifact.with_name("failed_episodes.jsonl")
    pending = [
        (number, specification)
        for number, specification in enumerate(specifications, 1)
        if identifier(specification) not in completed
    ]
    print(
        f"Running {len(pending)} pending of {len(specifications)} negotiations "
        f"with {workers} worker(s).",
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(run_one, number, specification)
            for number, specification in pending
        ]
        for future in concurrent.futures.as_completed(futures):
            number, episode_id, row = future.result()
            if row.get("status") == "completed":
                completed[episode_id] = row
                row_artifact = artifact
            else:
                row_artifact = failed_artifact
            with row_artifact.open("a") as output:
                output.write(json.dumps(row, sort_keys=True) + "\n")
            details = [row["status"]]
            if row.get("termination"):
                details.append(f"termination={row['termination']}")
            welfare = (row.get("outcome") or {}).get("normalized_welfare")
            if welfare is not None:
                details.append(f"welfare={welfare:.3f}")
            print(
                f"[{number}/{len(specifications)}] {episode_id}: "
                + "; ".join(details),
                flush=True,
            )
    return completed


# ---------------------------------------------------------------------------
# Peer episode identity and prompt inspection
# ---------------------------------------------------------------------------


def episode_identifier(
    market_id: str,
    first_actor: str,
    horizon: int,
    condition: str,
    replication: int,
    discovery_messages: int = 0,
) -> str:
    """Create the one canonical ID used by both execution and resume logic."""
    discovery_tag = f"__d{discovery_messages}" if discovery_messages else ""
    return (
        f"{market_id}__first{first_actor}__h{horizon}{discovery_tag}"
        f"__{condition}__r{replication}"
    )


@dataclass(frozen=True)
class EpisodeSpec:
    """One cell of the market × horizon × disclosure × replication design."""

    market: dict[str, Any]
    horizon: int
    condition: str
    replication: int
    first_actor: str

    def identifier(self, discovery_messages: int) -> str:
        return episode_identifier(
            self.market["market_id"],
            self.first_actor,
            self.horizon,
            self.condition,
            self.replication,
            discovery_messages,
        )


def show_example(
    market: dict[str, Any],
    horizon: int,
    agent_policy: str,
    conditions: list[str],
    first_mover: str,
    discovery_messages: int,
    action_interface: str,
) -> None:
    print("RESEARCHER VIEW OF ONE MARKET\n")
    print(json.dumps(market, indent=2, sort_keys=True))
    actor = choose_first_actor(market, first_mover)
    for condition in conditions:
        print(f"\n{'=' * 20} AGENT {actor}: {condition} {'=' * 20}\n")
        proposal_limit = max(1, horizon // 2 - 1)
        print(build_prompt(
            condition, actor, market, [], None, 1, horizon, 0, proposal_limit,
            agent_policy, actor,
            discovery_messages,
            action_interface,
        ))


# ---------------------------------------------------------------------------
# Peer command-line experiment
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse and validate peer-experiment command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, default="openai")
    parser.add_argument("--model", default=model_settings("gpt51")["model"])
    parser.add_argument(
        "--action-interface",
        choices=ACTION_INTERFACE_VERSIONS,
        default=ACTION_INTERFACE_VERSION,
        help="Model-facing trade action format; legacy mode is for exact resume only.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=GENERATION["reasoning_effort"],
    )
    parser.add_argument(
        "--openrouter-provider",
        help="Pin one OpenRouter backend and disable fallback, for example DeepInfra",
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=[4])
    parser.add_argument(
        "--discovery-messages",
        type=int,
        default=0,
        help="Even number of pre-bargaining cheap-talk messages; zero disables it.",
    )
    parser.add_argument("--abundant-quantity", type=int, default=5)
    parser.add_argument(
        "--resources", type=int, choices=(2, 4, 6, 8, 10), default=2
    )
    parser.add_argument(
        "--market-panel",
        choices=MARKET_PANELS,
        default="responder_extraction_crossed",
    )
    parser.add_argument(
        "--list-market-panels",
        action="store_true",
        help="Explain the market-panel groups and exit without generating files.",
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
    parser.add_argument(
        "--min-extraction-pressure",
        type=float,
        help="Inclusive lower bound for the grid's theoretical pressure metric.",
    )
    parser.add_argument(
        "--max-extraction-pressure",
        type=float,
        help="Inclusive upper bound for the grid's theoretical pressure metric.",
    )
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=CONDITIONS,
        default=None,
    )
    parser.add_argument(
        "--first-mover",
        choices=("counterbalanced", "A", "B"),
        default="A",
    )
    parser.add_argument("--replications", type=int, default=1)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of episodes to run concurrently; one preserves sequential execution.",
    )
    parser.add_argument("--max-tokens", type=int, default=GENERATION["runner_max_tokens"])
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature; omit to use the model/provider default.",
    )
    parser.add_argument(
        "--agent-policy",
        choices=AGENT_POLICIES,
        default="plain_dialogue_self_interest",
        help="Peer decision instructions.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "results" / "new_runs" / "peer",
    )
    parser.add_argument("--show-example", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if any(horizon < 2 or horizon % 2 for horizon in args.horizons):
        parser.error("horizons must be even integers of at least 2")
    if args.discovery_messages < 0 or args.discovery_messages % 2:
        parser.error("discovery messages must be a nonnegative even integer")
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
    if len(set(args.horizons)) != len(args.horizons):
        parser.error("horizons must not contain duplicates")
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
    if (
        market_panel(args.market_panel).requires_first_mover_a
        and args.first_mover != "A"
    ):
        parser.error("the role-specific extraction panels require --first-mover A")
    if args.conditions is None:
        args.conditions = ["inventory_only", "rank_only", "exact_values"]
    if len(set(args.conditions)) != len(args.conditions):
        parser.error("conditions must not contain duplicates")
    return args


def main() -> None:
    args = parse_args()
    if args.list_market_panels:
        show_market_panels()
        return

    recorded_abundant_quantity = (
        None
        if market_panel(args.market_panel).varied_quantity
        else args.abundant_quantity
    )
    conditions = args.conditions
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
        all_markets,
        args.market_panel,
        args.abundant_quantity,
        args.resources,
    )
    if args.show_example:
        show_example(
            markets[0], args.horizons[0], args.agent_policy, conditions,
            args.first_mover, args.discovery_messages,
            args.action_interface,
        )
        return

    episode_count = len(markets) * len(conditions) * len(args.horizons) * args.replications
    maximum_calls = len(markets) * len(conditions) * sum(
        horizon + args.discovery_messages for horizon in args.horizons
    ) * args.replications
    design = {
        "protocol": PROTOCOL_VERSION,
        "action_interface": args.action_interface,
        "prompt": prompt_version(args.agent_policy),
        "markets": len(markets),
        "market_file": str(market_path),
        "agent_policy": args.agent_policy,
        "requested_temperature": args.temperature,
        "temperature_sent": (
            args.temperature
            if args.temperature is not None and model_uses_temperature(
                args.provider, args.model, args.reasoning_effort
            )
            else None
        ),
        "market_panel": args.market_panel,
        "abundant_quantity": recorded_abundant_quantity,
        "resources": args.resources,
        "conditions": conditions,
        "first_mover": args.first_mover,
        "horizons": args.horizons,
        "discovery_messages": args.discovery_messages,
        "episodes": episode_count,
        "maximum_model_calls": maximum_calls,
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
    load_env(ROOT.parent / ".env")
    config = {
        "protocol": PROTOCOL_VERSION,
        "action_interface": args.action_interface,
        "prompt": prompt_version(args.agent_policy),
        "provider": args.provider,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "openrouter_provider": args.openrouter_provider,
        "max_tokens": args.max_tokens,
        # The direct OpenAI request path does not send temperature together
        # with explicit reasoning.  Record the parameter actually transmitted,
        # while retaining the CLI value separately for reproducibility.
        "temperature": (
            args.temperature
            if args.temperature is not None and model_uses_temperature(
                args.provider, args.model, args.reasoning_effort
            )
            else None
        ),
        "requested_temperature": args.temperature,
        "horizons": args.horizons,
        "discovery_messages": args.discovery_messages,
        "replications": args.replications,
        "agent_policy": args.agent_policy,
        "market_panel": args.market_panel,
        "abundant_quantity": recorded_abundant_quantity,
        "resources": args.resources,
        "conditions": conditions,
        "first_mover": args.first_mover,
    }
    if args.market_ids:
        config["selected_market_ids"] = args.market_ids
    if args.extraction_roles:
        config["extraction_roles"] = args.extraction_roles
    if args.min_extraction_pressure is not None:
        config["min_extraction_pressure"] = args.min_extraction_pressure
    if args.max_extraction_pressure is not None:
        config["max_extraction_pressure"] = args.max_extraction_pressure
    with run_directory_lock(args.results_dir):
        artifact = prepare_result_artifact(args.results_dir, config)
        completed = load_completed_episodes(artifact)

        specifications = [
            EpisodeSpec(
                market=market,
                horizon=horizon,
                condition=condition,
                replication=replication,
                first_actor=choose_first_actor(market, args.first_mover),
            )
            for market in markets
            for horizon in args.horizons
            for condition in conditions
            for replication in range(args.replications)
        ]
        def run_specification(
            number: int,
            specification: EpisodeSpec,
        ) -> tuple[int, str, dict[str, Any]]:
            episode_id = specification.identifier(args.discovery_messages)
            if episode_id in completed:
                return number, episode_id, completed[episode_id]
            episode_model: Model | None = None
            prefix = f"[{number}/{len(specifications)}] {episode_id}"
            print(
                f"{prefix}: started; condition={specification.condition}; "
                f"horizon={specification.horizon}",
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
                resources = tuple(specification.market["inventory_a"])
                row = play_episode(
                    episode_id,
                    specification.condition,
                    specification.market,
                    specification.horizon,
                    lambda prompt: episode_model(
                        prompt, action_schema(resources, args.action_interface)
                    ),
                    args.agent_policy,
                    specification.first_actor,
                    args.discovery_messages,
                    args.action_interface,
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
                    # Provider charges can accrue before a later turn fails. Keep
                    # those successful-call records so interrupted experiments do
                    # not understate cost.
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
            lambda specification: specification.identifier(args.discovery_messages),
            run_specification,
        )

        analysis = analyze(list(completed.values()))
        (args.results_dir / "analysis.json").write_text(
            json.dumps(analysis, indent=2, sort_keys=True) + "\n"
        )
        write_readable_episodes(args.results_dir)
        print(json.dumps(analysis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
