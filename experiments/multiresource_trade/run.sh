#!/usr/bin/env bash
# Compatibility entry point. See scripts/main/ and scripts/ablations/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "prompts" ]]; then
  cd "$ROOT"
  exec python -m src.trading.run \
    --market-panel responder_extraction_crossed \
    --conditions inventory_only rank_only exact_values --horizons 8 \
    --agent-policy plain_dialogue_self_interest --show-example
fi
case "${2:-main}" in
  main) exec bash "$ROOT/scripts/main/_run.sh" "${1:-}" "${3:-preview}" ;;
  dialogue|resources|mediator)
    exec bash "$ROOT/scripts/ablations/$2.sh" "${1:-}" "${3:-preview}" ;;
  *) echo "Study must be main, dialogue, resources, or mediator." >&2; exit 2 ;;
esac
