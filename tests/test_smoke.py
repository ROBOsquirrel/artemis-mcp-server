"""Top-level Artemis MCP smoke tests (deliverable entry point).

The full unit suite lives under tests/. This module provides a quick,
self-contained check that the package imports and its core tools work against
an in-memory Artemis-schema database, plus a guard confirming no write or
transmit capability is exposed.

Run everything with:  python -m pytest -v
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from artemis_mcp.config import ArtemisConfig
from artemis_mcp.server import ArtemisService


def _make_db(root: Path) -> None:
    coll = root / "coll"
    (coll / "media").mkdir(parents=True)
    conn = sqlite3.connect(coll / "data.sqlite")
    conn.executescript(
        "CREATE TABLE info (NAME TEXT, DATE TEXT, VERSION INTEGER, EDITABLE INTEGER);"
        "CREATE TABLE signals (SIG_ID INTEGER PRIMARY KEY, NAME TEXT,"
        " DESCRIPTION TEXT, URL TEXT);"
        "CREATE TABLE frequency (FREQ_ID INTEGER PRIMARY KEY, SIG_ID INTEGER,"
        " VALUE INTEGER, DESCRIPTION TEXT);"
    )
    conn.execute("INSERT INTO info VALUES ('DB','2025',1,0)")
    conn.execute("INSERT INTO signals VALUES (1,'Test 433','desc','u')")
    conn.execute("INSERT INTO frequency VALUES (1,1,433920000,'')")
    conn.commit()
    conn.close()


@pytest.fixture()
def service(tmp_path: Path) -> ArtemisService:
    _make_db(tmp_path)
    return ArtemisService(ArtemisConfig(artemis_data_root=str(tmp_path)))


def test_package_imports():
    import artemis_mcp

    assert artemis_mcp.__version__


def test_smoke_list_and_search(service):
    listing = service.list_databases()
    assert listing["success"] is True
    database_id = listing["databases"][0]["database_id"]
    result = service.search_by_frequency(database_id, 433_920_000, 50_000)
    assert result["success"] is True
    assert result["matches"][0]["name"] == "Test 433"


def test_smoke_no_write_or_transmit(service):
    health = service.health_check()
    assert health["read_only"] is True
    assert health["transmit_enabled"] is False
