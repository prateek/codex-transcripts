from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = (Path(__file__).resolve().parents[1] / "src").as_posix()
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

