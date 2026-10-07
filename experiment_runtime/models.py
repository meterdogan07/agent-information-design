"""One model catalog and generation configuration for all three experiments."""

from __future__ import annotations

import argparse
import json
import math
import shlex
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
if not (CONFIG_DIR / "models.json").is_file():
    CONFIG_DIR = Path(str(files("experiment_config")))
CONFIG_FILES = tuple(CONFIG_DIR / name for name in ("models.json", "generation.json"))


def _load(name):
    return json.loads((CONFIG_DIR / name).read_text())


_MODELS = _load("models.json")
_GENERATION = _load("generation.json")
MODEL_NAMES = tuple(_MODELS)
MIXED_MODEL_NAMES = ("gpt51", "claudesonnet5", "gemini", "grok43")


def generation_settings(study, preset=None):
    """Return a fresh settings dictionary; None temperature means omitted."""
    result = {**_GENERATION["defaults"], **_GENERATION["studies"][study]}
    if preset is not None:
        result["reasoning_effort"] = _MODELS[preset].get(
            "reasoning_effort", result["reasoning_effort"])
    if result["max_tokens"] <= 0 or result["timeout_seconds"] <= 0:
        raise ValueError(f"Invalid generation settings for {study}")
    if result["temperature"] is not None:
        raise ValueError("Study presets require provider-default temperature (null)")
    return result


def model_settings(preset):
    """Read a named endpoint without constructing a client or loading secrets."""
    result = deepcopy(_MODELS[preset])
    if result["provider"] not in {"openai", "openrouter", "anthropic"}:
        raise ValueError(f"Unsupported provider for {preset}")
    if result["provider"] == "openrouter" and not result["backend"]:
        raise ValueError(f"OpenRouter backend must be pinned for {preset}")
    return result


def prices(preset, study):
    spec = model_settings(preset)
    result = spec.get("pricing_by_study", {}).get(study, spec["pricing"])
    if any(not math.isfinite(result[k]) or result[k] < 0 for k in ("input", "output")):
        raise ValueError(f"Invalid pricing for {preset}")
    return result


def model_catalog(study):
    """Adapt shared settings to existing run-record schemas, without new fields."""
    names = MIXED_MODEL_NAMES if study == "mixed" else MODEL_NAMES
    catalog = {}
    for name in names:
        spec = model_settings(name)
        entry = {k: spec[k] for k in ("provider", "model", "backend")}
        if study == "auction":
            entry["tag"] = spec["tag"]
            if "reasoning_effort" in spec:
                entry["reasoning_effort"] = spec["reasoning_effort"]
        elif study == "bargaining":
            rate = prices(name, study)
            entry.update(reasoning_effort=generation_settings(study, name)["reasoning_effort"],
                         input_usd_per_million=rate["input"], output_usd_per_million=rate["output"],
                         pricing_verified_on=rate["verified_on"])
        elif study == "mixed":
            rate = prices(name, study)
            entry.update(input_price=rate["input"], output_price=rate["output"])
        else:
            raise ValueError(f"Unknown catalog format: {study}")
        catalog[name] = entry
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", choices=MODEL_NAMES)
    parser.add_argument("--study", choices=tuple(_GENERATION["studies"]), default="trade")
    parser.add_argument("--shell", action="store_true", help="Quoted assignments for shell launchers")
    args = parser.parse_args()
    model = model_settings(args.preset)
    settings = generation_settings(args.study, args.preset)
    if args.shell:
        values = {"MODEL_TAG": model["tag"], "PROVIDER_ID": model["provider"],
                  "MODEL_ID": model["model"], "MODEL_BACKEND": model["backend"] or model["provider"],
                  "OPENROUTER_BACKEND": model["backend"] or "",
                  "REASONING_SETTING": settings["reasoning_effort"],
                  "GENERATION_MAX_TOKENS": settings["max_tokens"]}
        for name, value in values.items():
            print(f"{name}={shlex.quote(str(value))}")
    else:
        print(json.dumps({"model": model, "generation": settings}, indent=2))


if __name__ == "__main__":
    main()
