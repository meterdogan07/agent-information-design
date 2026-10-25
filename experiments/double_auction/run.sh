#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

case "${1:-}" in
  models)
    exec python auction.py models
    ;;
  prompts)
    shift
    exec python auction.py inspect "$@"
    ;;
  help|-h|--help|"")
    exec python auction.py study --help
    ;;
  *)
    exec python auction.py study "$@"
    ;;
esac
