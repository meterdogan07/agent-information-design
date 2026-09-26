"""Render JSONL results as one inspectable directory per negotiation."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from experiment_runtime import fenced as _fenced
from experiment_runtime import read_jsonl as _read_jsonl
from experiment_runtime import safe_name as _safe_name


def _document(parts: Iterable[str]) -> str:
    return "\n\n".join(part for part in parts if part != "") + "\n"


def _group_calls(calls: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group retry attempts belonging to the same logical model invocation."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    inferred_id = 0
    for call in calls:
        call_id = call.get("logical_call_id")
        if call_id is None:
            if inferred_id == 0 or call.get("attempt", 1) == 1:
                inferred_id += 1
            call_id = inferred_id
        grouped[int(call_id)].append(call)
    return [grouped[call_id] for call_id in sorted(grouped)]


def _call_attempts(
    attempts: list[dict[str, Any]],
    fallback_prompt: str | None,
    fallback_response: str | None,
) -> list[str]:
    """Render every provider attempt made for one negotiation step."""
    parts = []
    if not attempts:
        parts.extend(("## Model input", _fenced(fallback_prompt or "")))
        parts.extend(("## Raw model output", _fenced(fallback_response or "")))
        return parts

    for position, attempt in enumerate(attempts, 1):
        number = attempt.get("attempt", position)
        parts.append(f"## API attempt {number}")
        if "request" in attempt:
            parts.extend((
                "Exact model-call arguments:",
                _fenced(attempt["request"], "json"),
            ))
        elif fallback_prompt is not None and position == len(attempts):
            parts.extend((
                "Model input reconstructed from the recorded negotiation step. "
                "This older artifact did not retain the full model-call arguments:",
                _fenced(fallback_prompt),
            ))
        else:
            parts.append(
                "Exact request text was not retained by this older artifact."
            )

        if "raw_response" in attempt:
            raw = attempt["raw_response"]
            parts.append(
                "Raw model output:" if raw is not None else "Raw model output: none"
            )
            if raw is not None:
                parts.append(_fenced(raw))
        elif fallback_response is not None and position == len(attempts):
            parts.extend((
                "Raw model output from the recorded negotiation step:",
                _fenced(fallback_response),
            ))
        else:
            parts.append(
                "Raw output was not retained for this attempt by the older artifact."
            )

        metadata = {
            key: value
            for key, value in attempt.items()
            if key not in {"request", "raw_response", "logical_call_id", "attempt"}
        }
        if metadata:
            parts.extend(("Call metadata:", _fenced(metadata, "json")))
    return parts


def _peer_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    call_groups = _group_calls(row.get("model_calls", []))
    steps = []
    for index, step in enumerate(row.get("transcript", [])):
        steps.append({
            "label": f"Agent {step.get('actor', '?')}, turn {step.get('turn', index + 1)}",
            "prompt": step.get("prompt"),
            "raw": step.get("raw_response"),
            "parsed_label": "Parsed and validated action",
            "parsed": step.get("action"),
            "errors": step.get("errors", []),
            "attempts": call_groups[index] if index < len(call_groups) else [],
        })
    for index in range(len(steps), len(call_groups)):
        steps.append({
            "label": f"Unmatched model call {index + 1}",
            "prompt": None,
            "raw": None,
            "parsed_label": "Parsed action",
            "parsed": None,
            "errors": ["call_not_matched_to_a_recorded_negotiation_step"],
            "attempts": call_groups[index],
        })
    return steps


def _mediator_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    recorded = [{
        "label": "Mediator proposal",
        "prompt": row.get("mediator_prompt"),
        "raw": row.get("mediator_raw_response"),
        "parsed_label": "Parsed and validated proposal",
        "parsed": row.get("proposal"),
        "errors": row.get("proposal_errors", []),
    }]
    for review in row.get("reviews", []):
        recorded.append({
            "label": f"Private review by Agent {review.get('actor', '?')}",
            "prompt": review.get("prompt"),
            "raw": review.get("raw_response"),
            "parsed_label": "Parsed review decision",
            "parsed": review.get("review"),
            "errors": review.get("errors", []),
        })

    call_groups = _group_calls(row.get("model_calls", []))
    for index, step in enumerate(recorded):
        step["attempts"] = call_groups[index] if index < len(call_groups) else []
    for index in range(len(recorded), len(call_groups)):
        recorded.append({
            "label": f"Unmatched model call {index + 1}",
            "prompt": None,
            "raw": None,
            "parsed_label": "Parsed decision",
            "parsed": None,
            "errors": ["call_not_matched_to_a_recorded_mediator_step"],
            "attempts": call_groups[index],
        })
    return recorded


def _feedback_mediator_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Render initial proposal, private feedback, revision, and final reviews."""
    recorded = [{
        "label": "Mediator initial proposal",
        "prompt": row.get("mediator_prompt"),
        "raw": row.get("mediator_raw_response"),
        "parsed_label": "Parsed and validated initial proposal",
        "parsed": row.get("initial_proposal"),
        "errors": row.get("initial_proposal_errors", []),
    }]
    for feedback in row.get("feedback", []):
        recorded.append({
            "label": f"Private initial feedback by Agent {feedback.get('actor', '?')}",
            "prompt": feedback.get("prompt"),
            "raw": feedback.get("raw_response"),
            "parsed_label": "Parsed qualitative feedback",
            "parsed": feedback.get("feedback"),
            "errors": feedback.get("errors", []),
        })
    if row.get("revision_prompt") is not None:
        recorded.append({
            "label": "Mediator final revision",
            "prompt": row.get("revision_prompt"),
            "raw": row.get("revision_raw_response"),
            "parsed_label": "Parsed and validated revised proposal",
            "parsed": row.get("revised_proposal"),
            "errors": row.get("revision_errors", []),
        })
    for review in row.get("reviews", []):
        recorded.append({
            "label": f"Final private review by Agent {review.get('actor', '?')}",
            "prompt": review.get("prompt"),
            "raw": review.get("raw_response"),
            "parsed_label": "Parsed final review decision",
            "parsed": review.get("review"),
            "errors": review.get("errors", []),
        })

    call_groups = _group_calls(row.get("model_calls", []))
    for index, step in enumerate(recorded):
        step["attempts"] = call_groups[index] if index < len(call_groups) else []
    for index in range(len(recorded), len(call_groups)):
        recorded.append({
            "label": f"Unmatched model call {index + 1}",
            "prompt": None,
            "raw": None,
            "parsed_label": "Parsed decision",
            "parsed": None,
            "errors": ["call_not_matched_to_a_recorded_feedback_mediator_step"],
            "attempts": call_groups[index],
        })
    return recorded


def _consulted_mediator_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Render consultations, the mediator proposal, and binding reviews."""
    recorded = []
    for consultation in row.get("consultations", []):
        recorded.append({
            "label": f"Private consultation by Agent {consultation.get('actor', '?')}",
            "prompt": consultation.get("prompt"),
            "raw": consultation.get("raw_response"),
            "parsed_label": "Parsed preferred trade report",
            "parsed": consultation.get("consultation"),
            "errors": consultation.get("errors", []),
        })
    recorded.append({
        "label": "Mediator proposal after consultation",
        "prompt": row.get("mediator_prompt"),
        "raw": row.get("mediator_raw_response"),
        "parsed_label": "Parsed and validated proposal",
        "parsed": row.get("proposal"),
        "errors": row.get("proposal_errors", []),
    })
    for review in row.get("reviews", []):
        recorded.append({
            "label": f"Private review by Agent {review.get('actor', '?')}",
            "prompt": review.get("prompt"),
            "raw": review.get("raw_response"),
            "parsed_label": "Parsed review decision",
            "parsed": review.get("review"),
            "errors": review.get("errors", []),
        })

    call_groups = _group_calls(row.get("model_calls", []))
    for index, step in enumerate(recorded):
        step["attempts"] = call_groups[index] if index < len(call_groups) else []
    for index in range(len(recorded), len(call_groups)):
        recorded.append({
            "label": f"Unmatched model call {index + 1}",
            "prompt": None,
            "raw": None,
            "parsed_label": "Parsed decision",
            "parsed": None,
            "errors": ["call_not_matched_to_a_recorded_consulted_mediator_step"],
            "attempts": call_groups[index],
        })
    return recorded


def _episode_summary(row: dict[str, Any], step_files: list[tuple[str, str]]) -> str:
    parts = [
        f"# {row.get('episode_id', 'Unknown episode')}",
        "This directory contains one recorded negotiation.",
        "## Summary",
        _fenced({
            key: row.get(key)
            for key in (
                "status", "condition", "mechanism", "first_actor",
                "max_messages", "discovery_messages", "termination", "error"
            )
            if key in row
        }, "json"),
    ]
    if "market" in row:
        parts.extend((
            "## Researcher-side market definition",
            _fenced(row["market"], "json"),
        ))
    if step_files:
        parts.append("## Model calls and negotiation steps")
        parts.append("\n".join(
            f"{number}. [{label}]({filename})"
            for number, (filename, label) in enumerate(step_files, 1)
        ))
    parts.extend((
        "## Outcome",
        _fenced({
            "agreement": row.get("agreement"),
            "termination": row.get("termination"),
            "outcome": row.get("outcome"),
            **(
                {"proposed_outcome": row.get("proposed_outcome")}
                if "proposed_outcome" in row else {}
            ),
        }, "json"),
    ))
    return _document(parts)


def _step_document(number: int, step: dict[str, Any]) -> str:
    parts = [f"# Step {number}: {step['label']}"]
    parts.extend(_call_attempts(
        step["attempts"], step.get("prompt"), step.get("raw")
    ))
    parts.extend((
        f"## {step['parsed_label']}",
        _fenced(step.get("parsed"), "json"),
        "## Parse or rule errors",
        _fenced(step.get("errors", []), "json"),
    ))
    return _document(parts)


def write_readable_episodes(results_dir: Path) -> Path:
    """Create one readable directory for every row in ``episodes.jsonl``."""
    config_path = results_dir / "config.json"
    episodes_path = results_dir / "episodes.jsonl"
    if not config_path.exists():
        raise FileNotFoundError(f"missing experiment config: {config_path}")
    if not episodes_path.exists():
        raise FileNotFoundError(f"missing episode artifact: {episodes_path}")

    config = json.loads(config_path.read_text())
    rows = _read_jsonl(episodes_path)
    output = results_dir / "readable_episodes"
    output.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(len(rows))))
    index_rows = [
        "| # | Negotiation | Condition | Status | Termination | Welfare |",
        "|---:|---|---|---|---|---:|",
    ]

    for number, row in enumerate(rows, 1):
        episode_id = str(row.get("episode_id", "unknown"))
        directory_name = f"{number:0{width}d}__{_safe_name(episode_id)}"
        episode_dir = output / directory_name
        episode_dir.mkdir(parents=True, exist_ok=True)
        if row.get("mechanism") == "feedback_mediator":
            steps = _feedback_mediator_steps(row)
        elif row.get("mechanism") == "consulted_mediator":
            steps = _consulted_mediator_steps(row)
        elif row.get("mechanism") == "mediator" or "mediator_prompt" in row:
            steps = _mediator_steps(row)
        else:
            steps = _peer_steps(row)
        step_files = []
        for step_number, step in enumerate(steps, 1):
            filename = f"step_{step_number:02d}.md"
            (episode_dir / filename).write_text(
                _step_document(step_number, step)
            )
            step_files.append((filename, step["label"]))
        (episode_dir / "README.md").write_text(
            _episode_summary(row, step_files)
        )

        outcome = row.get("outcome") or {}
        welfare = outcome.get("normalized_welfare", "")
        if isinstance(welfare, float):
            welfare = f"{welfare:.3f}"
        index_rows.append(
            f"| {number} | [{episode_id}]({directory_name}/README.md) | "
            f"{row.get('condition', '')} | {row.get('status', '')} | "
            f"{row.get('termination', '')} | {welfare} |"
        )

    (output / "README.md").write_text(_document((
        "# Readable negotiations",
        "Each numbered directory contains exactly one recorded negotiation. "
        "Each model call or game step has its own file.",
        "The source `config.json` and `episodes.jsonl` in the parent directory "
        "remain authoritative.",
        "## Experiment configuration",
        _fenced(config, "json"),
        "## Negotiation index",
        "\n".join(index_rows),
    )))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create one readable directory per recorded negotiation."
    )
    parser.add_argument("results_dir", type=Path)
    args = parser.parse_args()
    print(write_readable_episodes(args.results_dir))


if __name__ == "__main__":
    main()
