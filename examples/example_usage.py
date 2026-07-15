"""Minimal example: search Artemis references near a frequency.

Two ways to use this package:

1. In-process (no subprocess) via ArtemisService  -- shown here.
2. Over MCP stdio via ArtemisMCPClient            -- see the second block.

Set ARTEMIS_DATA_ROOT to a folder containing one or more Artemis
``<collection>/data.sqlite`` databases, or rely on the platform default.
"""

from __future__ import annotations


def in_process_example() -> None:
    from artemis_mcp.server import ArtemisService

    service = ArtemisService()
    listing = service.list_databases()
    print("databases:", listing["database_count"])
    if not listing["databases"]:
        print("No Artemis databases found. Set ARTEMIS_DATA_ROOT.")
        return
    database_id = listing["databases"][0]["database_id"]
    result = service.search_by_frequency(database_id, 915_000_000, tolerance_hz=100_000)
    for match in result.get("matches", []):
        print(f"  {match['name']}  err={match['frequency_error_hz']} Hz  "
              f"score={match['match_score']}  ({match['identification_status']})")


def mcp_client_example() -> None:
    from artemis_mcp_client import ArtemisMCPClient

    with ArtemisMCPClient() as client:
        print("tools:", client.list_tools())
        database_id = client.default_database_id()
        if database_id:
            print(client.search_by_frequency(database_id, 915_000_000,
                                              tolerance_hz=100_000))


if __name__ == "__main__":
    in_process_example()
    # mcp_client_example()   # uncomment to exercise the stdio MCP path
