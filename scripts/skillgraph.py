#!/usr/bin/env python3
"""Compatibility entrypoint for the bundled SkillGraph Cartographer CLI."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys


BUNDLED_CLI = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "skillgraph-cartographer"
    / "scripts"
    / "skillgraph.py"
)


if __name__ == "__main__":
    if not BUNDLED_CLI.exists():
        raise SystemExit(f"bundled SkillGraph CLI not found: {BUNDLED_CLI}")
    sys.argv[0] = str(BUNDLED_CLI)
    runpy.run_path(str(BUNDLED_CLI), run_name="__main__")
