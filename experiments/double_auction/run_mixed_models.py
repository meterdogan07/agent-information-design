#!/usr/bin/env python
"""Launch the mixed-model auction study; preview by default."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT.parents[1], ROOT / "src"):
    sys.path.insert(0, str(path))

from double_auction.mixed import main

if __name__ == "__main__":
    main()
