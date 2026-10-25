#!/usr/bin/env bash
# Usage: bash run.sh MODEL [preview|execute] [--messages N] [--budget USD]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# Prefer this checkout's runtime and model catalog over another editable install.
export PYTHONPATH="$ROOT/../..${PYTHONPATH:+:$PYTHONPATH}"
PRESET="${1:-}"
if [[ -z "$PRESET" ]]; then
  echo "Usage: bash run.sh MODEL [preview|execute] [--messages N] [--budget USD]" >&2
  exit 2
fi
shift

MODE=(--dry-run)
case "${1:-}" in
  preview) shift ;;
  execute) MODE=(--execute); shift ;;
  [0-9]*) echo "Use --messages N instead of a bare number." >&2; exit 2 ;;
esac
python -m src.run \
  --model-preset "$PRESET" --messages 6 "${MODE[@]}" \
  --panel five_per_profitable_stratum_both_starters_v1 --markets 30 "$@"
