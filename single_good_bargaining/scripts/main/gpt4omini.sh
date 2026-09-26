#!/usr/bin/env bash
# GPT-4o mini: preview by default; execute --budget USD for paid calls.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/run.sh" gpt4omini "$@"
