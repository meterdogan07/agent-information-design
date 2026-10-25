"""Render a double-auction run as readable Markdown files.

The JSON/JSONL files remain authoritative. Derived documents are written to
``readable_episodes/`` inside the completed run, as in the other experiments.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiment_runtime import fenced as _fenced
from experiment_runtime import read_jsonl, safe_name


ROOT = Path(__file__).resolve().parents[2]


def fenced(value: Any, language: str = "json") -> str:
    """Use JSON fences by default in auction reports."""
    return _fenced(value, language)


def load_rows(path: Path) -> list[dict[str, Any]]:
    return sorted(read_jsonl(path), key=lambda row: int(row["idx"]))


def trade_table(trades: list[dict[str, Any]]) -> str:
    if not trades:
        return "No trades."
    lines = [
        "| Buyer | Seller | Bid | Ask | Buyer price | Seller price |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for trade in trades:
        lines.append(
            f"| {trade['buyer']} | {trade['seller']} | {trade['bid']} | "
            f"{trade['ask']} | {trade['buyer_price']} | {trade['seller_price']} |"
        )
    return "\n".join(lines)


def call_document(
    number: int,
    entry: dict[str, Any],
    raw: dict[str, Any] | None,
) -> str:
    title = f"# Call {number}: round {entry.get('round', '?')} — {entry.get('who', '?')}"
    metadata = {
        "round": entry.get("round"),
        "agent": entry.get("who"),
        "role": entry.get("role"),
        "parsed_bid": entry.get("bid"),
        "parsed_ask": entry.get("ask"),
    }
    if raw:
        metadata.update({
            "provider": raw.get("provider"),
            "requested_model": raw.get("requested_model"),
            "returned_model": raw.get("returned_model"),
            "response_id": raw.get("response_id"),
            "finish_reason": raw.get("finish_reason"),
            "attempts": raw.get("attempts"),
            "elapsed_seconds": raw.get("elapsed_seconds"),
            "usage": raw.get("usage"),
            "error": raw.get("error"),
        })
    parts = [
        title,
        "## Metadata",
        fenced(metadata),
        "## Prompt",
        fenced(entry.get("prompt", ""), "text"),
        "## Model response",
        fenced(entry.get("response", ""), "json"),
    ]
    if raw and raw.get("attempt_history"):
        parts.extend(("## Retried attempts", fenced(raw["attempt_history"])))
    return "\n\n".join(parts) + "\n"


def market_document(row: dict[str, Any], call_files: list[tuple[str, str]]) -> str:
    endpoint_table = ["| Agent | Role | Endpoint |", "|---|---|---:|"]
    endpoint_table.extend(
        f"| {buyer} | Buyer | {row['values'][buyer]} |" for buyer in row["buyers"]
    )
    endpoint_table.extend(
        f"| {seller} | Seller | {row['costs'][seller]} |" for seller in row["sellers"]
    )
    history = []
    for round_row in row.get("round_history", []):
        history.extend((
            f"### Round {round_row['round']}",
            fenced({
                "submitted_bids": round_row.get("submitted_bids"),
                "submitted_asks": round_row.get("submitted_asks"),
                "accepted_bids": round_row.get("bids"),
                "accepted_asks": round_row.get("asks"),
                "invalid_orders": round_row.get("invalid_orders"),
                "feedback_released": round_row.get("feedback_released"),
                "provisional_outcome": round_row.get("provisional_outcome"),
            }),
        ))
    calls = "\n".join(f"- [{label}]({filename})" for filename, label in call_files)
    metrics = {
        key: row.get(key)
        for key in (
            "first_best_welfare", "welfare", "efficiency", "trade_count",
            "buyer_surplus", "seller_surplus", "invalid_order_count",
        )
    }
    quantile = row.get("target_marginal_surplus_quantile")
    quantile_text = (
        f" (target quantile {quantile:.3f})" if quantile is not None else ""
    )
    return "\n\n".join((
        f"# {row['condition']} — market {row['idx']}",
        f"Market family: `{row['market_family']}`  \n"
        f"Marginal-surplus level: `{row['surplus_level']}`{quantile_text}  \n"
        f"Protocol: `{row['protocol_version']}`",
        "## Researcher view of private endpoints\n\n" + "\n".join(endpoint_table),
        "## First-best trades\n\n" + fenced(row.get("first_best_trades", [])),
        "## Round history\n\n" + ("\n\n".join(history) if history else "No model rounds recorded."),
        "## Final trades\n\n" + trade_table(row.get("trades", [])),
        "## Outcome metrics\n\n" + fenced(metrics),
        "## Recorded model calls\n\n" + (calls or "No model calls."),
    )) + "\n"


def render(source: Path, output: Path) -> Path:
    source = source.resolve()
    output = output.resolve()
    if not (source / "run_config.json").exists():
        raise FileNotFoundError(f"missing run_config.json: {source}")
    if not (source / "summary.json").exists():
        raise FileNotFoundError(f"run is incomplete; missing summary.json: {source}")
    if output == source:
        raise ValueError("readable output cannot replace the run directory")

    config = json.loads((source / "run_config.json").read_text())
    summary = json.loads((source / "summary.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    (output / "config.md").write_text(
        "# Frozen run configuration\n\n" + fenced(config) + "\n"
    )
    (output / "summary.md").write_text(
        "# Stored run summary\n\n" + fenced(summary) + "\n"
    )

    index = [
        f"# Readable run: {source.name}",
        "",
        "This directory is a derived reading aid. The source `run_config.json`, "
        "condition-level `details.jsonl`, and `summary.json` remain authoritative.",
        "",
        f"- Source artifact: `{source}`",
        "- [Frozen configuration](config.md)",
        "- [Stored summary](summary.md)",
        "",
        "| Condition | Market | Family | Surplus | Efficiency | Trades | Conversation |",
        "|---|---:|---|---|---:|---:|---|",
    ]

    for condition in config["conditions"]:
        details = source / condition / "details.jsonl"
        if not details.exists():
            raise FileNotFoundError(f"missing condition artifact: {details}")
        for row in load_rows(details):
            market_dir = output / safe_name(condition) / f"market_{row['idx']:03d}"
            market_dir.mkdir(parents=True, exist_ok=True)
            call_files = []
            raw_calls = row.get("raw_calls", [])
            for number, entry in enumerate(row.get("log", []), 1):
                raw = raw_calls[number - 1] if number - 1 < len(raw_calls) else None
                filename = f"call_{number:02d}_{safe_name(str(entry.get('who', 'agent')))}.md"
                (market_dir / filename).write_text(call_document(number, entry, raw))
                call_files.append((filename, f"Call {number}: {entry.get('who')}"))
            (market_dir / "README.md").write_text(market_document(row, call_files))
            relative = market_dir.relative_to(output) / "README.md"
            efficiency = row.get("efficiency")
            efficiency_text = "" if efficiency is None else f"{efficiency:.3f}"
            index.append(
                f"| {condition} | {row['idx']} | {row['market_family']} | "
                f"{row['surplus_level']} | "
                f"{efficiency_text} | {row['trade_count']} | [open]({relative}) |"
            )

    (output / "README.md").write_text("\n".join(index) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    artifact = args.artifact if args.artifact.is_absolute() else ROOT / args.artifact
    output = args.output or artifact / "readable_episodes"
    if not output.is_absolute():
        output = ROOT / output
    print(render(artifact, output))


if __name__ == "__main__":
    main()
