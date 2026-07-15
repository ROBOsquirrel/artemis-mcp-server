"""Shared pytest fixtures: temporary Artemis-schema SQLite databases.

These fixtures build small databases that reproduce the real Artemis schema
so the unit tests never depend on a real Artemis installation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ARTEMIS_SCHEMA = """
CREATE TABLE info (NAME TEXT, DATE TEXT, VERSION INTEGER, EDITABLE INTEGER);
CREATE TABLE signals (
    SIG_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    NAME TEXT, DESCRIPTION TEXT, URL TEXT);
CREATE TABLE frequency (
    FREQ_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE INTEGER, DESCRIPTION TEXT);
CREATE TABLE bandwidth (
    BAND_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE INTEGER, DESCRIPTION TEXT);
CREATE TABLE modulation (
    MDL_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE TEXT, DESCRIPTION TEXT);
CREATE TABLE mode (
    MOD_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE TEXT, DESCRIPTION TEXT);
CREATE TABLE location (
    LOC_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE TEXT, DESCRIPTION TEXT);
CREATE TABLE category_label (
    CLB_ID INTEGER PRIMARY KEY AUTOINCREMENT, VALUE TEXT);
CREATE TABLE category (
    CAT_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, CLB_ID INTEGER);
CREATE TABLE acf (
    ACF_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, VALUE FLOAT, DESCRIPTION TEXT);
CREATE TABLE documents (
    DOC_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    SIG_ID INTEGER, EXTENSION TEXT, NAME TEXT,
    DESCRIPTION TEXT, TYPE TEXT, PREVIEW INTEGER);
"""


def _populate(conn: sqlite3.Connection) -> None:
    conn.executescript(ARTEMIS_SCHEMA)
    conn.execute(
        "INSERT INTO info VALUES (?,?,?,?)",
        ("Test SigID DB", "2025-01-01", 42, 1),
    )
    # Signal 1: 915 MHz FSK telemetry, 100 kHz bandwidth, image preview.
    conn.execute(
        "INSERT INTO signals VALUES (?,?,?,?)",
        (1, "ISM 915 Telemetry", "A 915 MHz FSK telemetry signal.",
         "http://example.invalid/915"),
    )
    conn.execute("INSERT INTO frequency VALUES (?,?,?,?)",
                 (1, 1, 915_000_000, "center"))
    conn.execute("INSERT INTO bandwidth VALUES (?,?,?,?)",
                 (1, 1, 100_000, "nominal"))
    conn.execute("INSERT INTO modulation VALUES (?,?,?,?)", (1, 1, "FSK", ""))
    conn.execute("INSERT INTO mode VALUES (?,?,?,?)", (1, 1, "Data", ""))
    conn.execute("INSERT INTO location VALUES (?,?,?,?)",
                 (1, 1, "Worldwide", ""))
    conn.execute("INSERT INTO category_label VALUES (?,?)", (1, "Telemetry"))
    conn.execute("INSERT INTO category VALUES (?,?,?)", (1, 1, 1))
    conn.execute("INSERT INTO acf VALUES (?,?,?,?)", (1, 1, 0.02, "period"))
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
        (1, 1, "png", "spectrum", "", "Image", 1),
    )
    # Signal 2: 153 MHz POCSAG pager.
    conn.execute(
        "INSERT INTO signals VALUES (?,?,?,?)",
        (2, "POCSAG Pager", "153 MHz paging system.",
         "http://example.invalid/pocsag"),
    )
    conn.execute("INSERT INTO frequency VALUES (?,?,?,?)",
                 (2, 2, 153_000_000, ""))
    conn.execute("INSERT INTO modulation VALUES (?,?,?,?)", (2, 2, "FSK", ""))
    conn.commit()


@pytest.fixture()
def artemis_root(tmp_path: Path) -> Path:
    """Create a data root containing one valid Artemis collection."""
    coll = tmp_path / "collection_a"
    (coll / "media").mkdir(parents=True)
    conn = sqlite3.connect(coll / "data.sqlite")
    try:
        _populate(conn)
    finally:
        conn.close()
    # A referenced media file that actually exists on disk.
    (coll / "media" / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    return tmp_path


@pytest.fixture()
def config(artemis_root: Path):
    from artemis_mcp.config import ArtemisConfig

    return ArtemisConfig(artemis_data_root=str(artemis_root))


@pytest.fixture()
def service(config):
    from artemis_mcp.server import ArtemisService

    return ArtemisService(config)


@pytest.fixture()
def database_id(service) -> str:
    result = service.list_databases()
    return result["databases"][0]["database_id"]
