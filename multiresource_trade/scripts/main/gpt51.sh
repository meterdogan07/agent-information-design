#!/usr/bin/env bash
# gpt51: main trade panel. Preview by default; use execute for paid calls.
set -euo pipefail
exec bash "$(dirname "$0")/_run.sh" gpt51 "${@:-preview}"
