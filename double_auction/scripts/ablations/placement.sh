#!/usr/bin/env bash
# Usage: bash scripts/ablations/placement.sh MODEL [preview|freeze|execute] [options]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL="${1:?Specify a model preset}"
shift
exec bash "$ROOT/run.sh" "$MODEL" placement "${@:-preview}" \
  --markets 15 --seed 97731
