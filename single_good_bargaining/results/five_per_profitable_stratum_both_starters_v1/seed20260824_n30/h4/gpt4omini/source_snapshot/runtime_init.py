"""Shared infrastructure for the repository's independent experiments.

Game rules, prompts, markets, and scoring deliberately remain in their own
experiment. This package contains provider, artifact, and run-management helpers.
"""

from .artifacts import (
    append_jsonl,
    fenced,
    read_jsonl,
    run_directory_lock,
    safe_name,
    sha256_file,
    sha256_text,
)
from .provider import (
    ENDPOINTS,
    ProviderModel,
    TracedProviderModel,
    load_env_files,
    token_counts,
)

__all__ = [
    "ENDPOINTS",
    "ProviderModel",
    "TracedProviderModel",
    "append_jsonl",
    "fenced",
    "load_env_files",
    "read_jsonl",
    "run_directory_lock",
    "safe_name",
    "sha256_file",
    "sha256_text",
    "token_counts",
]
