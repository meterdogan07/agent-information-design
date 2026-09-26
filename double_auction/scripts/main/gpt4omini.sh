#!/usr/bin/env bash
# GPT-4o mini: preview by default; freeze and execute follow the auction guide.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/run.sh" gpt4omini margin "$@" \
  --markets 30 --seed 97617
