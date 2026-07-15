# Artemis MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
server that exposes **signal-reference information from
[AresValley Artemis](https://github.com/AresValley/Artemis) SQLite databases**
so an LLM or application can search and retrieve documented signals and compare
them against real measurements.

It is self-contained and dependency-light (only the `mcp` SDK) so it can be
dropped into other projects as an MCP tool provider, imported in-process, or run
as a stdio subprocess.

> **Read-only and safe by design.** Every Artemis database is opened with a
> `file:...?mode=ro` URI *and* `PRAGMA query_only = ON`. No tool exposes
> arbitrary SQL, shell, Python, or unrestricted file reads, and nothing ever
> writes to an Artemis database.

## Features

- Discovers Artemis `data.sqlite` databases and maps them to stable, opaque
  `database_id` tokens (callers never pass raw filesystem paths).
- Ranked frequency search, full-text + structured search, per-signal detail and
  media, frequency-range lookup, and deterministic measurement comparison.
- Tolerant of missing optional tables/columns; clear structured errors.
- stdio transport (safe for local desktop / subprocess integration).

## Install

```bash
python -m pip install -r requirements.txt
# or, as a package:
python -m pip install .
```

Requires Python 3.10+ and the `mcp` SDK.

## Artemis database discovery

By default the server searches the platform Artemis data directory
(on Windows: `%LOCALAPPDATA%\AresValley\Artemis\data`). Override it with the
`ARTEMIS_DATA_ROOT` environment variable or `artemis_data_root` in `config.json`
(copy from `config.example.json`). Each collection folder holds a `data.sqlite`
and a `media/` directory; multiple databases are supported.

## Run the server (stdio)

```bash
python run_artemis_mcp.py
# or via the installed console script:
artemis-mcp
```

The server speaks MCP over stdin/stdout and is meant to be launched by an MCP
client rather than used interactively.

## Integrate into another project

**1. As a stdio MCP subprocess** (any MCP-capable host / agent). Point the host
at `python run_artemis_mcp.py`. A ready-made synchronous client is included:

```python
from artemis_mcp_client import ArtemisMCPClient

with ArtemisMCPClient() as client:
    db = client.default_database_id()
    print(client.search_by_frequency(db, 915_000_000, tolerance_hz=100_000))
```

**2. In-process** (no subprocess) via the service layer:

```python
from artemis_mcp.server import ArtemisService

service = ArtemisService()
db = service.list_databases()["databases"][0]["database_id"]
print(service.search_by_frequency(db, 915_000_000, tolerance_hz=100_000))
```

**3. Register the tools on your own FastMCP server:**

```python
from artemis_mcp.server import build_server
mcp, service = build_server()   # returns a FastMCP instance with all tools
```

See `examples/example_usage.py`.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `artemis_list_databases` | Discover all read-only Artemis databases |
| `artemis_get_database_info` | Metadata + record counts + available tables |
| `artemis_search_by_frequency` | Ranked candidates near a measured frequency |
| `artemis_search_signals` | Text + structured-filter search |
| `artemis_search_frequency_range` | Documented signals within a start/stop range |
| `artemis_get_signal` | Full documented details for one signal |
| `artemis_get_signal_media` | Validated local media paths (image/audio/document) |
| `artemis_compare_measurement` | Deterministic ranking against a measurement |
| `artemis_health_check` | Server status, SDK/Python versions, DB accessibility |

All ranked results are **documentation-based hypotheses, not confirmed signal
identifications**, and every response says so.

## Configuration

Copy `config.example.json` to `config.json` and adjust:

```json
{
  "artemis_data_root": "",
  "frequency_tolerance_hz": 100000,
  "bandwidth_tolerance_hz": 100000,
  "maximum_results": 20,
  "mcp_transport": "stdio",
  "mcp_host": "127.0.0.1",
  "mcp_port": 8765,
  "allow_external_database_paths": false
}
```

Local `config.json` is gitignored. If an HTTP transport is added, it must bind
to `127.0.0.1` only.

## Security design

- Read-only SQLite (`mode=ro` + `PRAGMA query_only = ON`); never insert/update/
  delete/migrate/vacuum.
- Parameterized queries only; no SQL string concatenation of user input.
- No arbitrary SQL / shell / Python / file-read tools are exposed.
- Media access is confined to a validated Artemis `media/` directory with
  path-containment checks (blocks `..` traversal and absolute paths).
- Database paths outside the configured data root are rejected unless
  `allow_external_database_paths` is explicitly enabled.

## Tests

```bash
python -m pytest -v
```

Unit tests build temporary SQLite databases that reproduce the Artemis schema,
so no real Artemis install or hardware is required. To additionally run the
real-database integration test, set `ARTEMIS_INTEGRATION_TEST=1`.

## License

MIT — see `LICENSE`. This package contains only original code and no
GPL-licensed material, so it is safe to embed in permissively- or
proprietarily-licensed projects.
