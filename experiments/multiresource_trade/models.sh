#!/usr/bin/env bash

# Model settings for run.sh. This file is sourced, not executed directly.

RUN_FLAG=""
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  RUN_FLAG="--execute"
fi

select_model() {
  local preset="${1:-}"
  local repo_root settings
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  settings="$(PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" python -m experiment_runtime.models "$preset" --study trade --shell)" || return
  eval "$settings"  # Assignments are shell-quoted by the shared configuration loader.

  MODEL_ARGS=(
    --provider "$PROVIDER_ID"
    --model "$MODEL_ID"
    --reasoning-effort "$REASONING_SETTING"
  )
  if [[ -n "$OPENROUTER_BACKEND" ]]; then
    MODEL_ARGS+=(--openrouter-provider "$OPENROUTER_BACKEND")
  fi

  if [[ "${LEGACY_ACTION_INTERFACE:-0}" == "1" ]]; then
    echo "These scripts support only prompts with engine-owned offer IDs." >&2
    return 2
  fi
  if [[ -n "${PEER_PROMPT:-}" ]]; then
    echo "PEER_PROMPT is no longer configurable; these scripts use the study prompt." >&2
    return 2
  fi
  PEER_ACTION_ARGS=(--action-interface engine_owned_offer_ids_20260822)
  ACTION_INTERFACE_LABEL="engine-owned offer IDs"
  PEER_MODEL_TAG="${MODEL_TAG}_engineids"
  PEER_AGENT_POLICY="plain_dialogue_self_interest"

  printf 'Model preset: %s | model: %s | fixed backend: %s | peer interface: %s | peer policy: %s\n' \
    "$preset" "$MODEL_ID" "$MODEL_BACKEND" "$ACTION_INTERFACE_LABEL" "$PEER_AGENT_POLICY"
}
