"""Unit tests for Artemis read-only database access and search logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from artemis_mcp.config import ArtemisConfig
from artemis_mcp.database import (
    ArtemisDatabase,
    ArtemisDatabaseRegistry,
    make_database_id,
)
from artemis_mcp.errors import (
    DatabaseNotFoundError,
    DatabaseUnavailableError,
    SchemaError,
    SignalNotFoundError,
)


def test_discovery_finds_database(config):
    registry = ArtemisDatabaseRegistry(config)
    entries = registry.discover()
    assert len(entries) == 1
    assert entries[0].name == "Test SigID DB"
    assert entries[0].version == "42"


def test_stable_database_id(config):
    registry = ArtemisDatabaseRegistry(config)
    first = registry.discover()[0].database_id
    second = registry.discover()[0].database_id
    assert first == second
    assert first.startswith("db_")


def test_resolve_unknown_id_raises(config):
    registry = ArtemisDatabaseRegistry(config)
    registry.discover()
    with pytest.raises(DatabaseNotFoundError):
        registry.resolve("db_does_not_exist")


def test_read_only_connection_blocks_writes(config):
    registry = ArtemisDatabaseRegistry(config)
    entry = registry.discover()[0]
    db = registry.open(entry.database_id)
    with pytest.raises((DatabaseUnavailableError, sqlite3.DatabaseError)):
        with db.connect() as conn:
            conn.execute("INSERT INTO signals (NAME) VALUES ('x')")
            conn.commit()


def test_frequency_search_and_ranking(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    matches = db.search_by_frequency(915_012_500, 100_000)
    assert matches
    top = matches[0]
    assert top.name == "ISM 915 Telemetry"
    assert top.frequency_error_hz == pytest.approx(-12_500)
    assert top.identification_status == "hypothesis"


def test_tolerance_boundary(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    # Just inside tolerance.
    assert db.search_by_frequency(915_100_000, 100_000)
    # Just outside tolerance.
    assert not db.search_by_frequency(915_200_001, 100_000)


def test_bandwidth_matching_boosts_score(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    with_bw = db.search_by_frequency(
        915_000_000, 100_000, bandwidth_hz=100_000, bandwidth_tolerance_hz=50_000
    )[0]
    assert with_bw.closest_bandwidth_hz == 100_000
    assert with_bw.bandwidth_error_hz == 0


def test_modulation_filter_in_ranking(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    match = db.search_by_frequency(915_000_000, 100_000, modulation="FSK")[0]
    assert "FSK" in match.modulations


def test_text_search(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    results = db.search_signals(text="pager")
    assert len(results) == 1
    assert results[0]["name"] == "POCSAG Pager"


def test_signal_detail_retrieval(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    detail = db.get_signal(1)
    assert detail.name == "ISM 915 Telemetry"
    assert detail.frequencies_hz[0]["value_hz"] == 915_000_000
    assert detail.categories == ["Telemetry"]
    assert detail.spectrum_image_exists is True


def test_signal_not_found(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    with pytest.raises(SignalNotFoundError):
        db.get_signal(9999)


def test_missing_optional_table_tolerated(tmp_path: Path):
    coll = tmp_path / "minimal"
    coll.mkdir()
    conn = sqlite3.connect(coll / "data.sqlite")
    conn.executescript(
        "CREATE TABLE info (NAME TEXT, DATE TEXT, VERSION INTEGER, EDITABLE INTEGER);"
        "CREATE TABLE signals (SIG_ID INTEGER PRIMARY KEY, NAME TEXT,"
        " DESCRIPTION TEXT, URL TEXT);"
    )
    conn.execute("INSERT INTO info VALUES ('m','2025',1,0)")
    conn.execute("INSERT INTO signals VALUES (1,'S','d','u')")
    conn.commit()
    conn.close()
    config = ArtemisConfig(artemis_data_root=str(tmp_path))
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    detail = db.get_signal(1)
    assert detail.frequencies_hz == []
    # Frequency search on a database with no frequency table is a clear error.
    with pytest.raises(SchemaError):
        db.search_by_frequency(915_000_000, 100_000)


def test_malformed_database_reports_error(tmp_path: Path):
    coll = tmp_path / "broken"
    coll.mkdir()
    (coll / "data.sqlite").write_bytes(b"this is not a database")
    config = ArtemisConfig(artemis_data_root=str(tmp_path))
    registry = ArtemisDatabaseRegistry(config)
    entries = registry.discover()
    db = registry.open(entries[0].database_id)
    with pytest.raises(DatabaseUnavailableError):
        db.database_info()


def test_sql_injection_is_parameterized(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    results = db.search_signals(text="'; DROP TABLE signals;--")
    assert results == []
    # The table must still exist and be queryable afterwards.
    assert db.search_signals(text="pager")


def test_path_traversal_blocked(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    assert db.validate_media_path("../../etc/passwd") is None
    assert db.validate_media_path("/etc/passwd") is None
    assert db.validate_media_path("subdir/file.png") is None


def test_media_path_validation_ok(config):
    registry = ArtemisDatabaseRegistry(config)
    db = registry.open(registry.discover()[0].database_id)
    valid = db.validate_media_path("1.png")
    assert valid is not None
    assert valid.name == "1.png"


def test_external_path_rejected_by_default(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.sqlite").write_bytes(b"x")
    config = ArtemisConfig(artemis_data_root=str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    registry = ArtemisDatabaseRegistry(config)
    registry._id_to_path["db_fake"] = outside / "data.sqlite"
    from artemis_mcp.errors import PathSecurityError

    with pytest.raises(PathSecurityError):
        registry.resolve("db_fake")
