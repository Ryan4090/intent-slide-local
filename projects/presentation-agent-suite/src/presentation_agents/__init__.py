#!/usr/bin/env python3
"""
Presentation Agent Suite - Package API

Exports the stateful three-agent pipeline.

Usage:
    from presentation_agents import AgentPipeline

Dependencies:
    PyMuPDF only when rendering the research analysis PDF.
"""

from presentation_agents.pipeline import AgentPipeline, ContractError

__all__ = ["AgentPipeline", "ContractError"]
