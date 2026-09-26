"""Source-bound audit, paired analysis, and readable transcript rendering."""

from __future__ import annotations

import json
from pathlib import Path
import random
import shutil
import statistics
from typing import Any

from experiment_runtime import (
    fenced,
    read_jsonl as _read_jsonl,
    sha256_file,
    sha256_text,
)

from . import design, game
from .models import summarize_trace
from experiment_runtime.models import CONFIG_FILES


HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1] / "experiment_runtime"
AUDIT_VERSION = "source_snapshot_endpoint_bound_v2_20260824"
ANALYSIS_VERSION = "strict_ir_population_weighted_v2_20260824"
LOCAL_SOURCE_NAMES = (
    "__init__.py", "design.py", "game.py", "models.py", "run.py", "verify.py",
    "experiment.py",
)
SOURCE_FILES = {
    **{name: HERE / name for name in LOCAL_SOURCE_NAMES},
    "runtime_init.py": RUNTIME / "__init__.py",
    "runtime_provider.py": RUNTIME / "provider.py",
    "runtime_artifacts.py": RUNTIME / "artifacts.py",
    "runtime_models.py": RUNTIME / "models.py",
    **{f"config_{path.name}": path for path in CONFIG_FILES},
}
SOURCE_NAMES = tuple(SOURCE_FILES)


def file_hashes(
    source_dir: Path | None = None,
    names: tuple[str, ...] | None = None,
) -> dict[str, str]:
    selected = names or SOURCE_NAMES
    files = (
        {name: source_dir / name for name in selected}
        if source_dir else {name: SOURCE_FILES[name] for name in selected}
    )
    return {
        name: sha256_file(path)
        for name, path in files.items()
    }


def freeze_source(run_dir: Path, expected: dict[str, str]) -> Path:
    """Create or verify the immutable source bundle for one result artifact."""
    if set(expected) != set(SOURCE_NAMES):
        raise RuntimeError("configured source hash set is incomplete")
    target = run_dir / "source_snapshot"
    if target.exists():
        if file_hashes(target) != expected:
            raise RuntimeError("frozen source snapshot differs from config")
        return target
    current = file_hashes()
    if current != expected:
        raise RuntimeError("current source differs from the run configuration")
    target.mkdir()
    for name, source in SOURCE_FILES.items():
        shutil.copyfile(source, target / name)
    if file_hashes(target) != expected:
        raise RuntimeError("source snapshot verification failed")
    return target


def _same(left: Any, right: Any) -> bool:
    return game.canonical_json(left) == game.canonical_json(right)


def audit_run(run_dir: Path) -> dict[str, Any]:
    """Independently replay every response and recompute every outcome."""
    run_dir = run_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    required = [run_dir / name for name in ("config.json", "markets.jsonl", "episodes.jsonl")]
    if not all(path.exists() for path in required):
        return {"passed": False, "errors": ["missing required artifact"], "warnings": []}
    config = json.loads((run_dir / "config.json").read_text())
    markets = _read_jsonl(run_dir / "markets.jsonl")
    rows = _read_jsonl(run_dir / "episodes.jsonl")
    if config.get("protocol_version") != design.PROTOCOL_VERSION:
        errors.append("protocol version mismatch")
    if config.get("prompt_version") != design.PROMPT_VERSION:
        errors.append("prompt version mismatch")
    if config.get("conditions") != list(design.CONDITIONS):
        errors.append("condition list mismatch")
    if config.get("condition_signals") != design.CONDITION_SIGNALS:
        errors.append("condition signal map mismatch")
    if config.get("prior_id") != design.PRIOR_ID:
        errors.append("population prior mismatch")
    panel_id = config.get("panel_id")
    if panel_id not in design.PANEL_IDS:
        errors.append("evaluation panel mismatch")
    if len(markets) != config.get("expected_markets"):
        errors.append("market count differs from config")
    try:
        regenerated = design.generate_markets(
            int(config["expected_markets"]), int(config["seed"]), str(panel_id)
        )
    except Exception as exc:
        errors.append(f"configured market generation failed: {exc}")
        regenerated = []
    if regenerated and not _same(regenerated, markets):
        errors.append("frozen markets differ from the configured generator")
    configured_hashes = config.get("code_sha256") or {}
    configured_names = tuple(configured_hashes)
    snapshot = run_dir / "source_snapshot"
    snapshot_present = snapshot.is_dir()
    frozen_source_verified = False
    if snapshot_present:
        try:
            frozen_source_verified = (
                file_hashes(snapshot, configured_names) == configured_hashes
            )
        except FileNotFoundError:
            frozen_source_verified = False
        if not frozen_source_verified:
            errors.append("frozen source snapshot differs from config")
    current_source_matches = configured_hashes == file_hashes(
        names=configured_names
    )
    if not snapshot_present and not current_source_matches:
        errors.append("current source hashes differ and no frozen snapshot exists")
    elif not snapshot_present:
        warnings.append("run predates frozen source snapshots")
    elif not current_source_matches:
        warnings.append("current source differs; frozen snapshot verified")
    market_text = "".join(json.dumps(value, sort_keys=True) + "\n" for value in markets)
    if sha256_text(market_text) != config.get("markets_sha256"):
        errors.append("market file hash mismatch")
    market_by_id = {market["market_id"]: market for market in markets}
    if len(market_by_id) != len(markets):
        errors.append("duplicate market IDs")
    for market in markets:
        try:
            design.validate_market(market)
        except Exception as exc:
            errors.append(f"market {market.get('market_id')}: {exc}")
    if panel_id == design.PROFITABLE_MIRRORED_PANEL_ID:
        target = len(markets) // len(design.PROFITABLE_FAMILIES)
        counts = {
            family: sum(market.get("family") == family for market in markets)
            for family in design.PROFITABLE_FAMILIES
        }
        if not markets or len(markets) % 6 or any(count != target for count in counts.values()):
            errors.append("mirrored panel is not balanced across profitable families")
        by_base: dict[str, list[dict[str, Any]]] = {}
        for market in markets:
            by_base.setdefault(str(market.get("base_market_id")), []).append(market)
        for base_id, pair in by_base.items():
            if len(pair) != 2:
                errors.append(f"base market {base_id} does not have two starting orders")
                continue
            if {row.get("first_mover") for row in pair} != {"seller", "buyer"}:
                errors.append(f"base market {base_id} does not mirror both starters")
            economics = {
                (row.get("seller_cost"), row.get("buyer_value"), row.get("family"))
                for row in pair
            }
            if len(economics) != 1:
                errors.append(f"base market {base_id} changes economics across starters")
    elif markets and len(markets) % len(design.FAMILIES) == 0:
        target = len(markets) // len(design.FAMILIES)
        counts = {
            family: sum(market.get("family") == family for market in markets)
            for family in design.FAMILIES
        }
        if any(count != target for count in counts.values()):
            errors.append("evaluation panel is not balanced across market families")
    else:
        errors.append("market count cannot be balanced across four families")

    seen: set[str] = set()
    paired = {condition: set() for condition in design.CONDITIONS}
    raw_count = 0
    max_prompt = 0
    terminal_proposals = 0
    unsafe = 0
    parse_errors = 0
    invalid_actions = 0
    is_mock = config.get("provider") == "mock"
    for number, row in enumerate(rows, 1):
        prefix = f"row {number}"
        episode_id = row.get("episode_id")
        if episode_id in seen:
            errors.append(f"{prefix}: duplicate episode ID")
        seen.add(episode_id)
        condition = row.get("condition")
        market = row.get("market") or {}
        market_id = market.get("market_id")
        if condition not in design.CONDITIONS:
            errors.append(f"{prefix}: unknown condition")
            continue
        paired[condition].add(market_id)
        if episode_id != f"{market_id}::{condition}":
            errors.append(f"{prefix}: episode ID mismatch")
        if not _same(market, market_by_id.get(market_id)):
            errors.append(f"{prefix}: embedded market mismatch")
        if row.get("status") != "completed":
            errors.append(f"{prefix}: non-completed row")
        if row.get("signal_by_actor") != design.signals_for(condition):
            errors.append(f"{prefix}: signal assignment mismatch")
        expected_manifest = {
            "market_sha256": sha256_text(game.canonical_json(market)),
            "provider": config.get("provider"),
            "model": config.get("model"),
            "backend": config.get("backend"),
            "code_sha256": configured_hashes,
        }
        if not _same(row.get("manifest"), expected_manifest):
            errors.append(f"{prefix}: manifest mismatch")

        outcome = row.get("outcome") or {}
        logical_calls = outcome.get("calls") or []
        replay = game.ReplayModel([call.get("response", "") for call in logical_calls])
        try:
            reproduced = game.run_episode(
                market, condition, replay,
                horizon=int(config["horizon"]),
                max_tokens=int(config["max_tokens"]),
            )
        except Exception as exc:
            errors.append(f"{prefix}: replay failed: {type(exc).__name__}: {exc}")
            continue
        if replay.responses:
            errors.append(f"{prefix}: unused responses after terminal state")
        for field in (
            "protocol_version", "prompt_version", "signal_by_actor", "outcome"
        ):
            if not _same(reproduced.get(field), row.get(field)):
                errors.append(f"{prefix}: replay mismatch in {field}")
        recomputed_metrics = game.evaluate(market, outcome)
        saved_metrics = row.get("metrics") or {}
        comparable_metrics = {
            key: recomputed_metrics.get(key) for key in saved_metrics
        }
        if not _same(comparable_metrics, saved_metrics):
            errors.append(f"{prefix}: deterministic metric recomputation failed")

        for call, event in zip(logical_calls, outcome.get("events") or []):
            max_prompt = max(max_prompt, len(str(call.get("prompt", ""))))
            parsed, parse_error = game.parse_action(str(call.get("response", "")))
            if not _same(parsed, call.get("parsed_action")) or parse_error != call.get("parse_error"):
                errors.append(f"{prefix}: parser differs from raw response")
            parse_errors += int(bool(parse_error))
            invalid_actions += int(not event.get("valid"))
            if event.get("turn") == int(config["horizon"]) and event.get("decision") == "propose":
                terminal_proposals += 1
                if event.get("valid"):
                    errors.append(f"{prefix}: terminal proposal was treated as legal")

        api_calls = row.get("raw_api_calls") or []
        raw_count += len(api_calls)
        successful = [call for call in api_calls if not call.get("error")]
        recomputed_usage = summarize_trace(api_calls)
        if not _same(row.get("usage"), recomputed_usage):
            errors.append(f"{prefix}: usage summary mismatch")
        prices = config.get("cost_estimate") or {}
        recomputed_cost = (
            recomputed_usage["input_tokens"]
            * float(prices.get("input_usd_per_million", 0))
            + recomputed_usage["output_tokens"]
            * float(prices.get("output_usd_per_million", 0))
        ) / 1_000_000
        if abs(float(row.get("actual_cost_usd", 0)) - recomputed_cost) > 1e-12:
            errors.append(f"{prefix}: recorded cost mismatch")
        if is_mock and api_calls:
            errors.append(f"{prefix}: mock row has API traces")
        if not is_mock and len(successful) != len(logical_calls):
            errors.append(f"{prefix}: logical/API call count mismatch")
        for index, (logical, api) in enumerate(zip(logical_calls, successful), 1):
            if api.get("prompt") != logical.get("prompt"):
                errors.append(f"{prefix}: API call {index} prompt mismatch")
            if api.get("response") != logical.get("response"):
                errors.append(f"{prefix}: API call {index} response mismatch")
            if not _same(api.get("response_format"), logical.get("response_format")):
                errors.append(f"{prefix}: API call {index} schema mismatch")
            if not api.get("response_id") or not api.get("returned_model"):
                errors.append(f"{prefix}: API call {index} lacks provenance")
            if api.get("provider") != config.get("provider"):
                errors.append(f"{prefix}: API call {index} provider mismatch")
            if api.get("requested_model") != config.get("model"):
                errors.append(f"{prefix}: API call {index} requested-model mismatch")
            if api.get("returned_model") != config.get("model"):
                errors.append(f"{prefix}: API call {index} returned-model mismatch")
            if api.get("requested_backend") != config.get("backend"):
                errors.append(f"{prefix}: API call {index} requested-backend mismatch")
            if api.get("reasoning_effort") != config.get("reasoning_effort"):
                errors.append(f"{prefix}: API call {index} reasoning mismatch")
            if api.get("max_tokens") != config.get("max_tokens"):
                errors.append(f"{prefix}: API call {index} output-limit mismatch")
            if api.get("temperature") is not None:
                errors.append(f"{prefix}: API call {index} temperature was not provider default")
            backend = config.get("backend")
            if backend and str(api.get("returned_provider") or "").lower() != str(backend).lower():
                errors.append(f"{prefix}: API call {index} backend mismatch")
            if str(api.get("finish_reason") or "").lower() in {"length", "max_tokens", "incomplete"}:
                errors.append(f"{prefix}: API call {index} hit output limit")
        unsafe += int(bool((row.get("metrics") or {}).get("unsafe_acceptance")))

    expected_ids = set(market_by_id)
    for condition, ids in paired.items():
        if ids != expected_ids:
            errors.append(f"condition {condition} is incomplete or unpaired")
    if len(rows) != config.get("expected_episodes"):
        errors.append("episode count differs from config")
    if unsafe:
        warnings.append(f"{unsafe} non-IR acceptances were blocked")
    if terminal_proposals:
        warnings.append(f"{terminal_proposals} terminal proposal attempts were invalidated")
    return {
        "audit_version": AUDIT_VERSION,
        "passed": not errors,
        "run_dir": str(run_dir),
        "markets": len(markets),
        "episodes": len(rows),
        "raw_api_calls": raw_count,
        "maximum_prompt_characters": max_prompt,
        "terminal_proposal_attempts": terminal_proposals,
        "unsafe_acceptances": unsafe,
        "parse_errors": parse_errors,
        "invalid_actions": invalid_actions,
        "errors": errors,
        "warnings": warnings,
        "source_snapshot_present": snapshot_present,
        "frozen_source_verified": frozen_source_verified,
        "current_source_matches": current_source_matches,
    }


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _sd(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _bootstrap(values: list[float], draws: int = 10_000) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(20260824)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values) for _ in range(draws)
    )
    return [means[int(.025 * (draws - 1))], means[int(.975 * (draws - 1))]]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [row["metrics"] for row in rows]
    positive = [item for item in metrics if item["potential_surplus"] > 0]
    shares = [item["seller_surplus_share"] for item in metrics if item["seller_surplus_share"] is not None]
    potential_total = sum(float(item["potential_surplus"]) for item in metrics)
    welfare_total = sum(float(item["welfare"]) for item in metrics)
    return {
        "episodes": len(rows),
        "mean_normalized_welfare": _mean([float(item["normalized_welfare"]) for item in metrics]),
        "mean_welfare": _mean([float(item["welfare"]) for item in metrics]),
        "total_potential_surplus": potential_total,
        "total_realized_welfare": welfare_total,
        "surplus_weighted_efficiency": (
            welfare_total / potential_total if potential_total else 1.0
        ),
        "mean_decision_quality": _mean([float(item["decision_quality"]) for item in metrics]),
        "agreement_rate": _mean([float(item["behavioral_agreement"]) for item in metrics]),
        "positive_market_execution_rate": _mean([float(item["weak_ir_execution"]) for item in positive]),
        "positive_market_ever_ir_offer_rate": _mean([float(item["ever_ir_offer"]) for item in positive]),
        "positive_market_ever_strict_ir_offer_rate": _mean([
            float(item["ever_strict_ir_offer"]) for item in positive
        ]),
        "positive_market_ever_endpoint_offer_rate": _mean([
            float(item["ever_endpoint_offer"]) for item in positive
        ]),
        "positive_market_failed_after_ir_offer_rate": _mean([float(item["failed_after_ir_offer"]) for item in positive]),
        "positive_market_failed_after_strict_ir_offer_rate": _mean([
            float(item["failed_after_strict_ir_offer"]) for item in positive
        ]),
        "positive_market_failed_after_endpoint_offer_rate": _mean([
            float(item["failed_after_endpoint_offer"]) for item in positive
        ]),
        "accepted_endpoint_rate": _mean([
            float(item["accepted_at_endpoint"]) for item in metrics
            if item["weak_ir_execution"]
        ]),
        "mean_seller_gain_unconditional": _mean([
            float(item["seller_gain"]) for item in metrics
        ]),
        "mean_buyer_gain_unconditional": _mean([
            float(item["buyer_gain"]) for item in metrics
        ]),
        "seller_surplus_share_mean": _mean([float(value) for value in shares]),
        "seller_surplus_share_sd": _sd([float(value) for value in shares]),
        "split_inequality_mean": _mean([
            float(item["split_inequality"]) for item in metrics
            if item["split_inequality"] is not None
        ]),
        "mean_turns_used": _mean([float(item["turns_used"]) for item in metrics]),
        "mean_agreement_turn": _mean([
            float(item["agreement_turn"]) for item in metrics
            if item["agreement_turn"] is not None
        ]),
        "invalid_actions": sum(int(item["invalid_action_count"]) for item in metrics),
        "parse_errors": sum(int(item["parse_error_count"]) for item in metrics),
        "unsafe_acceptances": sum(int(item["unsafe_acceptance"]) for item in metrics),
    }


CONTRASTS = (
    ("exact", "hidden", "exact_minus_hidden"),
    ("coarse", "hidden", "coarse_minus_hidden"),
    ("exact", "coarse", "exact_minus_coarse"),
    ("seller_informed", "hidden", "seller_informed_minus_hidden"),
    ("buyer_informed", "hidden", "buyer_informed_minus_hidden"),
    ("exact", "seller_informed", "exact_minus_seller_informed"),
    ("exact", "buyer_informed", "exact_minus_buyer_informed"),
)


def analyze_run(run_dir: Path) -> dict[str, Any]:
    audit = audit_run(run_dir)
    if not audit["passed"]:
        raise ValueError("audit failed")
    config = json.loads((run_dir / "config.json").read_text())
    rows = _read_jsonl(run_dir / "episodes.jsonl")
    # Analysis always uses the current deterministic metric definition. This
    # adds newly introduced diagnostics to old immutable episode records.
    rows = [
        {**row, "metrics": game.evaluate(row["market"], row["outcome"])}
        for row in rows
    ]
    by_key = {(row["market"]["market_id"], row["condition"]): row for row in rows}
    market_lookup = {row["market"]["market_id"]: row["market"] for row in rows}
    all_ids = sorted(market_lookup)

    def cells(ids: list[str]) -> dict[str, Any]:
        return {
            condition: summarize([by_key[(market_id, condition)] for market_id in ids])
            for condition in design.CONDITIONS
        }

    def contrasts(ids: list[str]) -> dict[str, Any]:
        result = {}
        for high, low, name in CONTRASTS:
            result[name] = {}
            for field in ("normalized_welfare", "welfare", "decision_quality"):
                values = [
                    float(by_key[(market_id, high)]["metrics"][field])
                    - float(by_key[(market_id, low)]["metrics"][field])
                    for market_id in ids
                ]
                result[name][field] = {
                    "effect": _mean(values),
                    "bootstrap_ci95": _bootstrap(values),
                    "positive": sum(value > 0 for value in values),
                    "negative": sum(value < 0 for value in values),
                    "tied": sum(value == 0 for value in values),
                    "paired_values": values,
                }
        return result

    by_first_mover = {}
    for mover in ("seller", "buyer"):
        ids = [mid for mid in all_ids if market_lookup[mid]["first_mover"] == mover]
        by_first_mover[mover] = {
            "markets": len(ids), "cells": cells(ids), "paired_contrasts": contrasts(ids)
        }
    mirrored = config.get("panel_id") == design.PROFITABLE_MIRRORED_PANEL_ID
    analysis_families = (
        design.PROFITABLE_FAMILIES if mirrored else design.FAMILIES
    )
    population_weights = (
        design.PROFITABLE_FAMILY_POPULATION_WEIGHTS
        if mirrored else design.FAMILY_POPULATION_WEIGHTS
    )
    by_family = {}
    for family in analysis_families:
        ids = [mid for mid in all_ids if market_lookup[mid]["family"] == family]
        if ids:
            by_family[family] = {
                "markets": len(ids), "cells": cells(ids), "paired_contrasts": contrasts(ids)
            }
    population_reweighted_cells = {}
    for condition in design.CONDITIONS:
        population_reweighted_cells[condition] = {
            "normalized_welfare": sum(
                population_weights[family]
                * by_family[family]["cells"][condition]["mean_normalized_welfare"]
                for family in analysis_families
            ),
            "decision_quality": sum(
                population_weights[family]
                * by_family[family]["cells"][condition]["mean_decision_quality"]
                for family in analysis_families
            ),
            "expected_welfare": sum(
                population_weights[family]
                * by_family[family]["cells"][condition]["mean_welfare"]
                for family in analysis_families
            ),
        }
    first_mover_interactions: dict[str, Any] = {}
    if mirrored:
        paired_ids: dict[str, dict[str, str]] = {}
        for market_id, market in market_lookup.items():
            paired_ids.setdefault(market["base_market_id"], {})[
                market["first_mover"]
            ] = market_id
        for high, low, name in CONTRASTS:
            first_mover_interactions[name] = {}
            for field in ("normalized_welfare", "welfare", "decision_quality"):
                values = []
                for movers in paired_ids.values():
                    seller_id = movers["seller"]
                    buyer_id = movers["buyer"]
                    seller_effect = (
                        float(by_key[(seller_id, high)]["metrics"][field])
                        - float(by_key[(seller_id, low)]["metrics"][field])
                    )
                    buyer_effect = (
                        float(by_key[(buyer_id, high)]["metrics"][field])
                        - float(by_key[(buyer_id, low)]["metrics"][field])
                    )
                    values.append(seller_effect - buyer_effect)
                first_mover_interactions[name][field] = {
                    "effect": _mean(values),
                    "bootstrap_ci95": _bootstrap(values),
                    "positive": sum(value > 0 for value in values),
                    "negative": sum(value < 0 for value in values),
                    "tied": sum(value == 0 for value in values),
                    "paired_values": values,
                    "direction": "seller-first effect minus buyer-first effect",
                }
    usage = {
        key: sum(int((row.get("usage") or {}).get(key, 0)) for row in rows)
        for key in ("call_count", "input_tokens", "output_tokens", "reasoning_tokens")
    }
    return {
        "analysis_version": ANALYSIS_VERSION,
        "protocol_version": design.PROTOCOL_VERSION,
        "horizon": config["horizon"],
        "audit": audit,
        "cells": cells(all_ids),
        "paired_contrasts": contrasts(all_ids),
        "by_first_mover": by_first_mover,
        "first_mover_interactions": first_mover_interactions,
        "by_market_family": by_family,
        "population_reweighted_cells": population_reweighted_cells,
        "estimand_notes": {
            "panel_mean": "Equal weight on the four ex-post market families.",
            "population_reweighted": (
                "Analytic family probabilities conditional on profitable trade under the "
                "stated independent-uniform prior; within-family means come from paired bases."
                if mirrored else
                "Analytic family probabilities under the stated independent-uniform prior; "
                "within-family means come from five markets."
            ),
            "bootstrap": (
                "Paired market bootstrap only. It does not include LLM sampling variance; "
                "there is one trajectory per market-condition cell."
            ),
        },
        "usage": usage,
    }


def render_readable(run_dir: Path) -> Path:
    rows = _read_jsonl(run_dir / "episodes.jsonl")
    target = run_dir / "readable_episodes"
    target.mkdir(exist_ok=True)
    for row in rows:
        lines = [
            f"# {row['episode_id']}", "", f"Condition: {row['condition']}",
            f"Market: `{json.dumps(row['market'], sort_keys=True)}`", "",
        ]
        api_success = [call for call in row.get("raw_api_calls", []) if not call.get("error")]
        for index, call in enumerate(row["outcome"]["calls"], 1):
            lines += [
                f"## Turn {index}: {call['actor']}", "", "### Prompt", "",
                fenced(call["prompt"], "text"), "", "### Raw response", "",
                fenced(call["response"], "text"), "",
                "Parsed: `" + json.dumps(call.get("parsed_action"), sort_keys=True) + "`",
                f"Legal: `{call.get('legal')}`; error: `{call.get('validation_error')}`", "",
            ]
            if index <= len(api_success):
                api = api_success[index - 1]
                lines.append(
                    "Endpoint: `" + json.dumps({
                        "provider": api.get("provider"),
                        "returned_model": api.get("returned_model"),
                        "returned_provider": api.get("returned_provider"),
                        "response_id": api.get("response_id"),
                        "usage": api.get("usage"),
                    }, sort_keys=True) + "`\n"
                )
        lines += ["## Outcome", "", fenced({
            "outcome": row["outcome"],
            "metrics": game.evaluate(row["market"], row["outcome"]),
        }, "json"), ""]
        safe = row["episode_id"].replace("::", "__")
        (target / f"{safe}.md").write_text("\n".join(lines))
    return target
