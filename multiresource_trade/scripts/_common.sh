#!/usr/bin/env bash
# Shared setup only; study commands live in main/ and ablations/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${1:?Specify a model preset}"
MODE="${2:-preview}"
case "$MODE" in
  preview) export DRY_RUN=1 ;;
  execute) export DRY_RUN=0 ;;
  *) echo "Use MODEL [preview|execute]" >&2; exit 2 ;;
esac
source "$ROOT/models.sh"
select_model "$MODEL"
cd "$ROOT"

WORKERS="${WORKERS:-4}"
RESULT_BASE="${RESULT_ROOT:-results/new_runs/ablations}"

run_peer() {
  local output="$1"
  shift
  python -m src.trading.run \
    "${MODEL_ARGS[@]}" "${PEER_ACTION_ARGS[@]}" \
    --max-tokens "$GENERATION_MAX_TOKENS" --workers "$WORKERS" "$@" \
    --first-mover A --replications "${REPLICATIONS:-1}" \
    --agent-policy "$PEER_AGENT_POLICY" \
    --results-dir "$RESULT_BASE/$PEER_MODEL_TAG/$output" \
    ${RUN_FLAG:+$RUN_FLAG}
}
