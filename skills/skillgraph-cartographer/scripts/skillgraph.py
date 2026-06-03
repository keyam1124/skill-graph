#!/usr/bin/env python3
"""Read-only SkillGraph viewer runtime.

This file is intentionally kept as a thin compatibility entrypoint. The
implementation lives in ``skillgraph_core`` modules so the collector, graph
enrichment, viewer, and CLI responsibilities can evolve independently.
"""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skillgraph_core import *  # noqa: F401,F403 - compatibility re-exports
from skillgraph_core.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
