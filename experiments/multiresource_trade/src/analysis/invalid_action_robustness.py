"""Matched coarse/full welfare sensitivity; never modify raw episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .metrics import extraction_group

COARSE, FULL = "rank_only", "exact_values"


def invalid_action_counts(rows):
    """Count turns with errors, not the number of error strings on a turn."""
    counts = [sum(bool(turn.get("errors")) for turn in row["transcript"])
              for row in rows]
    return {"negotiations": len(rows), "invalid_actions": sum(counts),
            "negotiations_with_invalid_actions": sum(n > 0 for n in counts)}


def welfare_summary(pairs):
    if not pairs:
        return {"matched_pairs": 0, "coarse_W": None, "full_W": None,
                "full_minus_coarse_W": None}
    coarse, full = [sum(pair[c]["outcome"]["normalized_welfare"]
                        for pair in pairs) / len(pairs) for c in (COARSE, FULL)]
    return {"matched_pairs": len(pairs), "coarse_W": coarse, "full_W": full,
            "full_minus_coarse_W": full - coarse}


def analyze_invalid_actions(episodes):
    """Analyze one same-model panel, keeping horizons and starting roles apart.

    Drop both conditions for a market/replication if either transcript has a
    recorded action error. Least-information errors do not affect this contrast.
    Incomplete runs, duplicates, and unmatched conditions are rejected rather
    than silently dropped. Welfare is averaged equally across retained pairs.
    """
    if not episodes or any(row["status"] != "completed" for row in episodes):
        raise ValueError("Supply a nonempty panel of completed negotiations.")
    cells, groups = defaultdict(list), {}
    for row in episodes:
        cells[(row["max_messages"], row["first_actor"])].append(row)
    result = {}
    for (horizon, first_actor), rows in sorted(cells.items()):
        by_condition = defaultdict(list)
        matched = defaultdict(dict)
        for row in rows:
            condition = row["condition"]
            by_condition[condition].append(row)
            if condition not in (COARSE, FULL):
                continue
            key = (row["market"]["market_id"], row["replication"])
            if condition in matched[key]:
                raise ValueError(f"Duplicate negotiation: {key}, {condition}")
            matched[key][condition] = row
        if not matched or any(set(pair) != {COARSE, FULL} for pair in matched.values()):
            raise ValueError("Every market/replication must have coarse and full results.")
        strata = defaultdict(list)
        excluded = []
        for (market_id, replication), pair in sorted(matched.items()):
            if pair[COARSE]["market"] != pair[FULL]["market"]:
                raise ValueError(f"Market differs across conditions: {market_id}")
            if market_id not in groups:
                groups[market_id] = extraction_group(pair[COARSE]["market"])[0]
            group = groups[market_id]
            bad = [c for c in (COARSE, FULL)
                   if invalid_action_counts([pair[c]])["invalid_actions"]]
            strata["overall"].append((pair, bool(bad)))
            strata[group].append((pair, bool(bad)))
            if bad:
                excluded.append({"market_id": market_id, "replication": replication,
                                 "group": group, "invalid_conditions": bad})
        summaries = {}
        for group, pairs in sorted(strata.items()):
            original = welfare_summary([pair for pair, _ in pairs])
            filtered = welfare_summary([pair for pair, bad in pairs if not bad])
            summaries[group] = {"all_pairs": original, "excluding_invalid_pairs": filtered,
                                "excluded_pairs": len(pairs) - filtered["matched_pairs"]}
        result[f"h{horizon}_first_{first_actor}"] = {
            "counts_by_condition": {c: invalid_action_counts(v)
                                    for c, v in sorted(by_condition.items())},
            "welfare_by_group": summaries, "excluded_pairs": excluded}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main_results_root", type=Path,
                        help="Root containing one folder per model, each with "
                             "least/episodes.jsonl and coarse_and_full/episodes.jsonl")
    parser.add_argument("--output", type=Path, help="New JSON file; existing files are not overwritten")
    args = parser.parse_args()
    report = {"comparison": "full minus coarse; primary results retain all negotiations",
              "weighting": "equal weight per matched negotiation within each reported group",
              "models": {}}
    for model in sorted(args.main_results_root.iterdir()):
        if not model.is_dir():
            continue
        episodes, sources = [], []
        for part in ("least", "coarse_and_full"):
            path = model / part / "episodes.jsonl"
            data = path.read_bytes()
            episodes.extend(json.loads(line) for line in data.splitlines() if line.strip())
            sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()})
        report["models"][model.name] = {"sources": sources,
                                        "results": analyze_invalid_actions(episodes)}
    if not report["models"]:
        raise ValueError("No model folders found.")
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(rendered)
        print(args.output)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
