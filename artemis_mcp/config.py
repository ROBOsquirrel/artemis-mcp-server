"""Configuration handling for the Artemis MCP server.

Resolution order for the Artemis data root:

1. Explicit ``artemis_data_root`` value in the loaded configuration file.
2. ``ARTEMIS_DATA_ROOT`` environment variable.
3. The default Artemis data directory for the current platform
   (on Windows: ``%LOCALAPPDATA%\\AresValley\\Artemis\\data``).

No secrets are stored here. ``config.json`` / ``config.local.json`` are
gitignored; only ``config.example.json`` is committed.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULT_FREQUENCY_TOLERANCE_HZ = 100_000.0
DEFAULT_BANDWIDTH_TOLERANCE_HZ = 100_000.0
DEFAULT_MAXIMUM_RESULTS = 20

# Hard safety limits, independent of user configuration.
MAX_FREQUENCY_HZ = 1e12          # 1 THz: far above any supported receiver.
MAX_TOLERANCE_HZ = 1e9           # 1 GHz search window maximum.
MAX_RESULT_LIMIT = 200
DB_TIMEOUT_SECONDS = 5.0

def default_artemis_root() -> Path:
    """Return the platform default Artemis data directory (may not exist)."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "AresValley" / "Artemis" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AresValley" / "Artemis" / "data"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "AresValley" / "Artemis" / "data"


@dataclass
class ArtemisConfig:
    artemis_data_root: str = ""
    default_database_id: str = ""
    frequency_tolerance_hz: float = DEFAULT_FREQUENCY_TOLERANCE_HZ
    bandwidth_tolerance_hz: float = DEFAULT_BANDWIDTH_TOLERANCE_HZ
    maximum_results: int = DEFAULT_MAXIMUM_RESULTS
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    allow_external_database_paths: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_data_root(self) -> Path:
        """Resolve the Artemis data root using config, env, then defaults."""
        if self.artemis_data_root:
            return Path(self.artemis_data_root).expanduser().resolve()
        env_root = os.environ.get("ARTEMIS_DATA_ROOT", "").strip()
        if env_root:
            return Path(env_root).expanduser().resolve()
        return default_artemis_root().resolve()

    def validate(self) -> None:
        if self.mcp_host not in ("127.0.0.1", "localhost", "::1"):
            raise ConfigurationError(
                "mcp_host must be a loopback address; refusing to bind "
                f"to {self.mcp_host!r}."
            )
        if not 0 < self.frequency_tolerance_hz <= MAX_TOLERANCE_HZ:
            raise ConfigurationError(
                "frequency_tolerance_hz must be between 0 and "
                f"{MAX_TOLERANCE_HZ:.0f} Hz."
            )
        if not 0 < self.bandwidth_tolerance_hz <= MAX_TOLERANCE_HZ:
            raise ConfigurationError(
                "bandwidth_tolerance_hz must be between 0 and "
                f"{MAX_TOLERANCE_HZ:.0f} Hz."
            )
        if not 1 <= self.maximum_results <= MAX_RESULT_LIMIT:
            raise ConfigurationError(
                f"maximum_results must be between 1 and {MAX_RESULT_LIMIT}."
            )


def load_config(path: Path | None = None) -> ArtemisConfig:
    """Load configuration from JSON, falling back to safe defaults.

    Searches ``config.local.json`` then ``config.json`` in the project
    directory when no explicit path is given.
    """
    candidates: list[Path]
    if path is not None:
        candidates = [Path(path)]
    else:
        candidates = [
            PROJECT_DIR / "config.local.json",
            PROJECT_DIR / "config.json",
        ]

    raw: dict[str, Any] = {}
    for candidate in candidates:
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ConfigurationError(
                    f"Could not read configuration file {candidate.name}: {exc}"
                ) from exc
            break

    known = {f.name for f in fields(ArtemisConfig) if f.name != "extra"}
    kwargs = {key: value for key, value in raw.items() if key in known}
    extra = {key: value for key, value in raw.items() if key not in known}
    config = ArtemisConfig(**kwargs, extra=extra)
    config.validate()
    return config
