"""Optional integration test against a real installed Artemis database.

Skipped unless ARTEMIS_INTEGRATION_TEST=1 is set. When enabled it discovers
the real Artemis databases (default location or ARTEMIS_DATA_ROOT) and runs a
few read-only queries.
"""

from __future__ import annotations

import os

import pytest

RUN = os.environ.get("ARTEMIS_INTEGRATION_TEST") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="Set ARTEMIS_INTEGRATION_TEST=1 to run against a real DB."
)


def test_real_database_discovery_and_read():
    from artemis_mcp.server import ArtemisService

    service = ArtemisService()
    listing = service.list_databases()
    assert listing["success"] is True
    if listing["database_count"] == 0:
        pytest.skip("No real Artemis databases were found on this machine.")

    database_id = listing["databases"][0]["database_id"]
    info = service.get_database_info(database_id)
    assert info["success"] is True
    assert info["read_only"] is True

    health = service.health_check()
    assert health["read_only"] is True
    assert health["transmit_enabled"] is False
