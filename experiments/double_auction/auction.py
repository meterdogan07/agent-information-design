#!/usr/bin/env python
"""Compatibility launcher for the package in ``src/double_auction``."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
for path in (ROOT.parents[1], ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from double_auction.cli import main  # noqa: E402


if __name__ == "__main__":
    main()
