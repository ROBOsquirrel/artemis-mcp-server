"""Synchronous MCP client for the Artemis MCP server.

Wraps the async MCP stdio client in a background event-loop thread so the
existing command-line RF controller can call Artemis tools with plain
blocking method calls.

Design goals:
* Start the server as a child process over stdio (never a network socket).
* List and call tools with per-call timeouts.
* Clean shutdown with no orphaned Python processes.
* Human-readable errors; failures return structured dicts rather than raising
  into the controller loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER_SCRIPT = PROJECT_DIR / "run_artemis_mcp.py"
DEFAULT_TIMEOUT = 30.0
STARTUP_TIMEOUT = 20.0


class ArtemisMCPClientError(RuntimeError):
    pass


class ArtemisMCPClient:
    """Blocking facade over an MCP stdio session running in its own thread."""

    def __init__(
        self,
        server_script=None,
        python_executable=None,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.server_script = Path(server_script or DEFAULT_SERVER_SCRIPT)
        self.python_executable = python_executable or sys.executable
        self.timeout = timeout

        self._loop = None
        self._thread = None
        self._session = None
        self._stack = None
        self._started = False

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self._started:
            return
        if not self.server_script.is_file():
            raise ArtemisMCPClientError(
                "Artemis MCP server script not found: " + str(self.server_script)
            )

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="artemis-mcp-loop", daemon=True
        )
        self._thread.start()

        try:
            self._run(self._async_start(), timeout=STARTUP_TIMEOUT)
        except Exception as exc:
            self.close()
            raise ArtemisMCPClientError(
                "Failed to start Artemis MCP server: " + str(exc)
            ) from exc
        self._started = True

    def _run_loop(self):
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout=None):
        if self._loop is None:
            raise ArtemisMCPClientError("Client event loop is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout or self.timeout)

    async def _async_start(self):
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        child_env = os.environ.copy()
        params = StdioServerParameters(
            command=self.python_executable,
            args=[str(self.server_script)],
            cwd=str(PROJECT_DIR),
            env=child_env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

    def close(self):
        if self._loop is not None and self._loop.is_running():
            if self._stack is not None:
                try:
                    self._run(self._stack.aclose(), timeout=10)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
        self._session = None
        self._stack = None
        self._loop = None
        self._thread = None
        self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ operations
    def list_tools(self):
        self._ensure_started()
        result = self._run(self._session.list_tools())
        return [tool.name for tool in result.tools]

    def call_tool(self, name, arguments=None):
        """Call an Artemis tool and return its structured dict result."""
        self._ensure_started()
        try:
            result = self._run(
                self._session.call_tool(name, arguments or {}),
                timeout=self.timeout,
            )
        except TimeoutError:
            return {
                "success": False,
                "error": {
                    "code": "timeout",
                    "message": "Call to " + name + " timed out.",
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "error": {"code": "client_error", "message": str(exc)},
            }

        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            # FastMCP wraps a plain dict return under a "result" key.
            return structured.get("result", structured)
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"success": True, "text": text}
        return {
            "success": False,
            "error": {"code": "empty_result", "message": "No content returned."},
        }

    def _ensure_started(self):
        if not self._started:
            self.start()

    # ----------------------------------------------------------- convenience
    def list_databases(self):
        return self.call_tool("artemis_list_databases")

    def default_database_id(self):
        result = self.list_databases()
        dbs = result.get("databases") or []
        return dbs[0]["database_id"] if dbs else None

    def search_by_frequency(self, database_id, frequency_hz, **kwargs):
        args = {"database_id": database_id, "frequency_hz": frequency_hz}
        args.update(kwargs)
        return self.call_tool("artemis_search_by_frequency", args)

    def get_signal(self, database_id, signal_id):
        args = {"database_id": database_id, "signal_id": signal_id}
        return self.call_tool("artemis_get_signal", args)

    def search_frequency_range(self, database_id, start_frequency_hz,
                               stop_frequency_hz, **kwargs):
        args = {
            "database_id": database_id,
            "start_frequency_hz": start_frequency_hz,
            "stop_frequency_hz": stop_frequency_hz,
        }
        args.update(kwargs)
        return self.call_tool("artemis_search_frequency_range", args)


if __name__ == "__main__":
    with ArtemisMCPClient() as client:
        print("Tools:", client.list_tools())
        print("Databases:", client.list_databases())
