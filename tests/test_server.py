"""Tests for the ArtemisService tool layer and MCP registration."""

from __future__ import annotations

import asyncio

import pytest

from artemis_mcp.config import ArtemisConfig
from artemis_mcp.server import ArtemisService, build_server


def test_list_databases_tool(service):
    result = service.list_databases()
    assert result["success"] is True
    assert result["database_count"] == 1
    assert result["databases"][0]["database_id"].startswith("db_")


def test_get_database_info_tool(service, database_id):
    info = service.get_database_info(database_id)
    assert info["success"] is True
    assert info["signal_count"] == 2
    assert info["image_count"] == 1
    assert info["read_only"] is True
    assert "signals" in info["available_tables"]


def test_search_by_frequency_tool(service, database_id):
    result = service.search_by_frequency(database_id, 915_000_000, 100_000)
    assert result["success"] is True
    assert result["match_count"] >= 1
    assert result["matches"][0]["identification_status"] == "hypothesis"
    assert "disclaimer" in result


def test_search_by_frequency_invalid_frequency(service, database_id):
    result = service.search_by_frequency(database_id, -5, 100_000)
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_argument"


def test_search_by_frequency_invalid_limit(service, database_id):
    result = service.search_by_frequency(
        database_id, 915_000_000, 100_000, limit=99999
    )
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_argument"


def test_search_signals_tool(service, database_id):
    result = service.search_signals(database_id, text="telemetry")
    assert result["success"] is True
    assert result["result_count"] == 1


def test_get_signal_tool(service, database_id):
    result = service.get_signal(database_id, 1)
    assert result["success"] is True
    assert result["name"] == "ISM 915 Telemetry"


def test_get_signal_media_tool(service, database_id):
    result = service.get_signal_media(database_id, 1, "image")
    assert result["success"] is True
    assert result["media_count"] == 1
    assert result["media"][0]["exists"] is True


def test_get_signal_media_invalid_type(service, database_id):
    result = service.get_signal_media(database_id, 1, "video")
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_argument"


def test_compare_measurement_tool(service, database_id):
    result = service.compare_measurement(
        database_id,
        measured_frequency_hz=915_000_000,
        measured_bandwidth_hz=100_000,
        estimated_modulation="FSK",
        tolerance_hz=100_000,
    )
    assert result["success"] is True
    assert result["measurement"]["measured_frequency_hz"] == 915_000_000
    assert "ranking_method" in result
    assert "uncertainty" in result
    assert result["candidates"][0]["identification_status"] == "hypothesis"


def test_compare_measurement_does_not_invent_modulation(service, database_id):
    # estimated_modulation is labeled as caller-supplied, not measured.
    result = service.compare_measurement(
        database_id, measured_frequency_hz=915_000_000, tolerance_hz=100_000
    )
    assert "not measured by this server" in result["measurement"]["note"]


def test_health_check_tool(service):
    result = service.health_check()
    assert result["success"] is True
    assert result["read_only"] is True
    assert result["transmit_enabled"] is False
    assert result["database_count"] == 1


def test_invalid_database_id_structured_error(service):
    result = service.get_database_info("db_bogus")
    assert result["success"] is False
    assert result["error"]["code"] == "database_not_found"


def test_mcp_tool_registration(config):
    mcp, _service = build_server(config)

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    names = {tool.name for tool in tools}
    expected = {
        "artemis_list_databases",
        "artemis_get_database_info",
        "artemis_search_by_frequency",
        "artemis_search_signals",
        "artemis_get_signal",
        "artemis_get_signal_media",
        "artemis_compare_measurement",
        "artemis_search_frequency_range",
        "artemis_health_check",
    }
    assert expected == names


def test_mcp_tool_call_success(config):
    mcp, _service = build_server(config)

    async def _call():
        return await mcp.call_tool("artemis_health_check", {})

    result = asyncio.run(_call())
    # FastMCP returns (content, structured) for a dict tool result.
    structured = result[1] if isinstance(result, tuple) else result
    payload = structured.get("result", structured)
    assert payload["success"] is True


def test_no_arbitrary_sql_tool_exposed(config):
    mcp, _service = build_server(config)

    async def _list():
        return await mcp.list_tools()

    names = {t.name for t in asyncio.run(_list())}
    for banned in ("execute_sql", "query", "run_sql", "eval", "shell", "read_file"):
        assert banned not in names
