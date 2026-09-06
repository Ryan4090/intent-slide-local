#!/usr/bin/env python3
"""
Presentation Agent Suite - Supervisor Launcher

Runs user-controlled answer, approval, and visual-review commands. Do not pass
this launcher to delegated agent contexts.

Usage:
    .venv/bin/python projects/presentation-agent-suite/scripts/presentation_supervisor.py --help

Dependencies:
    Same repository-local environment as the agent launcher.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _PROJECT_ROOT / "src"
sys.path.insert(0, str(_SRC_DIR))
os.environ["PRESENTATION_SUITE_ACTOR"] = "supervisor"

from presentation_agents.cli import main  # type: ignore[import-not-found] # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
