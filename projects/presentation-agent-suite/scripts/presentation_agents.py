#!/usr/bin/env python3
"""
Presentation Agent Suite - Launcher

Runs the project-local three-agent CLI from the SlideMaster repository root.

Usage:
    .venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py --help

Dependencies:
    PyMuPDF only for complete-research.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) in sys.path:
    sys.path.remove(str(_SRC_DIR))
sys.path.insert(0, str(_SRC_DIR))
os.environ["PRESENTATION_SUITE_ACTOR"] = "agent"

from presentation_agents.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
