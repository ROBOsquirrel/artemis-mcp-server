"""Artemis MCP server package.

A read-only Model Context Protocol server that exposes signal-reference
information from AresValley Artemis SQLite databases so a local RF AI
controller can compare live BB60C / HackRF measurements against documented
signals. It never controls a radio and never writes to Artemis data.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
