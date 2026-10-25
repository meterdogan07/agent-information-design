#!/usr/bin/env bash
# GPT-4o mini: preview by default; use execute for paid calls.
set -euo pipefail
exec bash "$(dirname "$0")/_run.sh" gpt4omini "$@"
