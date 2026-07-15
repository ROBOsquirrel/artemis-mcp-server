"""Launcher for the Artemis MCP server (stdio transport).

Usage (Windows PowerShell):

    cd C:\\Users\\jhawk\\Desktop\\RF-AI-Controller
    .\\.venv\\Scripts\\Activate.ps1
    python .\\run_artemis_mcp.py

The server speaks the Model Context Protocol over stdin/stdout. It is meant
to be launched by an MCP client (see artemis_mcp_client.py) rather than used
interactively.
"""

from __future__ import annotations

from artemis_mcp.server import main

if __name__ == "__main__":
    raise SystemExit(main())
