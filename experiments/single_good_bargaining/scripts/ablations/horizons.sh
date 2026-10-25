#!/usr/bin/env bash
# Usage: bash scripts/ablations/horizons.sh MODEL [preview|execute] [--budget USD]
# The budget applies separately to each message limit, not to the whole sweep.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL="${1:?Specify a model preset}"
shift
for horizon in 2 4 6; do
  bash "$ROOT/run.sh" "$MODEL" "$@" --messages "$horizon"
done
