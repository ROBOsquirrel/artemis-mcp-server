"""Artemis MCP server.

Exposes read-only signal-reference tools over the Model Context Protocol.
The pure tool logic lives in :class:`ArtemisService` so it can be unit
tested without an MCP transport; ``build_server`` wires that service into a
FastMCP server using stdio transport by default.

Security posture:
* No tool executes arbitrary SQL, shell, or Python.
* No tool reads arbitrary files; media access is confined to a validated
  Artemis media directory.
* All databases are opened read-only.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from .config import (
    MAX_FREQUENCY_HZ,
    MAX_RESULT_LIMIT,
    MAX_TOLERANCE_HZ,
    ArtemisConfig,
    load_config,
)
from .database import ArtemisDatabaseRegistry
from .errors import ArtemisError, ValidationError, error_response
from .formatters import redact_home

DISCLAIMER = (
    "Artemis matches are documentation-based hypotheses, not confirmed "
    "signal identifications. Only measured values are authoritative."
)


def _validate_frequency(name: str, value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number.")
    if not 0 < value <= MAX_FREQUENCY_HZ:
        raise ValidationError(
            f"{name} must be between 0 and {MAX_FREQUENCY_HZ:.0f} Hz."
        )
    return value


def _validate_tolerance(name: str, value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number.")
    if not 0 < value <= MAX_TOLERANCE_HZ:
        raise ValidationError(
            f"{name} must be between 0 and {MAX_TOLERANCE_HZ:.0f} Hz."
        )
    return value


def _validate_limit(value: int, default: int) -> int:
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValidationError("limit must be an integer.")
    if not 1 <= value <= MAX_RESULT_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_RESULT_LIMIT}.")
    return value


class ArtemisService:
    """Transport-independent implementation of every Artemis MCP tool."""

    def __init__(self, config: ArtemisConfig | None = None) -> None:
        self.config = config or load_config()
        self.registry = ArtemisDatabaseRegistry(self.config)

    # ------------------------------------------------------------------ tools
    def list_databases(self) -> dict[str, Any]:
        try:
            entries = self.registry.discover()
            return {
                "success": True,
                "database_count": len(entries),
                "artemis_data_root": redact_home(str(self.registry.data_root)),
                "databases": [e.to_dict() for e in entries],
            }
        except Exception as exc:  # pragma: no cover - defensive
            return error_response(exc)

    def get_database_info(self, database_id: str) -> dict[str, Any]:
        try:
            db = self.registry.open(database_id)
            info = db.database_info()
            info["success"] = True
            info["database_id"] = database_id
            info["path"] = redact_home(str(db.path))
            return info
        except Exception as exc:
            return error_response(exc)

    def search_by_frequency(
        self,
        database_id: str,
        frequency_hz: float,
        tolerance_hz: float | None = None,
        bandwidth_hz: float | None = None,
        bandwidth_tolerance_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            frequency_hz = _validate_frequency("frequency_hz", frequency_hz)
            tol = _validate_tolerance(
                "tolerance_hz",
                tolerance_hz
                if tolerance_hz is not None
                else self.config.frequency_tolerance_hz,
            )
            if bandwidth_hz is not None:
                bandwidth_hz = _validate_frequency("bandwidth_hz", bandwidth_hz)
            if bandwidth_tolerance_hz is not None:
                bandwidth_tolerance_hz = _validate_tolerance(
                    "bandwidth_tolerance_hz", bandwidth_tolerance_hz
                )
            limit = _validate_limit(limit, self.config.maximum_results)

            db = self.registry.open(database_id)
            matches = db.search_by_frequency(
                frequency_hz=frequency_hz,
                tolerance_hz=tol,
                bandwidth_hz=bandwidth_hz,
                bandwidth_tolerance_hz=bandwidth_tolerance_hz,
                modulation=modulation,
                mode=mode,
                limit=limit,
            )
            return {
                "success": True,
                "database_id": database_id,
                "measured_frequency_hz": frequency_hz,
                "tolerance_hz": tol,
                "match_count": len(matches),
                "matches": [m.to_dict() for m in matches],
                "disclaimer": DISCLAIMER,
            }
        except Exception as exc:
            return error_response(exc)

    def search_signals(
        self,
        database_id: str,
        text: str | None = None,
        frequency_min_hz: float | None = None,
        frequency_max_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        location: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            if frequency_min_hz is not None:
                frequency_min_hz = _validate_frequency(
                    "frequency_min_hz", frequency_min_hz
                )
            if frequency_max_hz is not None:
                frequency_max_hz = _validate_frequency(
                    "frequency_max_hz", frequency_max_hz
                )
            if (
                frequency_min_hz is not None
                and frequency_max_hz is not None
                and frequency_min_hz > frequency_max_hz
            ):
                raise ValidationError(
                    "frequency_min_hz must not exceed frequency_max_hz."
                )
            limit = _validate_limit(limit, self.config.maximum_results)

            db = self.registry.open(database_id)
            results = db.search_signals(
                text=text,
                frequency_min_hz=frequency_min_hz,
                frequency_max_hz=frequency_max_hz,
                modulation=modulation,
                mode=mode,
                category=category,
                location=location,
                limit=limit,
            )
            return {
                "success": True,
                "database_id": database_id,
                "result_count": len(results),
                "results": results,
            }
        except Exception as exc:
            return error_response(exc)

    def get_signal(self, database_id: str, signal_id: int) -> dict[str, Any]:
        try:
            try:
                signal_id = int(signal_id)
            except (TypeError, ValueError):
                raise ValidationError("signal_id must be an integer.")
            db = self.registry.open(database_id)
            detail = db.get_signal(signal_id)
            payload = detail.to_dict()
            # Redact home directory in exposed paths.
            for key in ("spectrum_image_path", "audio_path"):
                if payload.get(key):
                    payload[key] = redact_home(payload[key])
            for item in payload.get("media", []):
                if item.get("path"):
                    item["path"] = redact_home(item["path"])
            payload["success"] = True
            payload["database_id"] = database_id
            return payload
        except Exception as exc:
            return error_response(exc)

    def get_signal_media(
        self, database_id: str, signal_id: int, media_type: str = "all"
    ) -> dict[str, Any]:
        try:
            media_type = (media_type or "all").lower()
            if media_type not in ("image", "audio", "document", "all"):
                raise ValidationError(
                    "media_type must be one of: image, audio, document, all."
                )
            try:
                signal_id = int(signal_id)
            except (TypeError, ValueError):
                raise ValidationError("signal_id must be an integer.")
            db = self.registry.open(database_id)
            detail = db.get_signal(signal_id)
            items = []
            for item in detail.media:
                if media_type != "all" and item.media_type != media_type:
                    continue
                data = item.to_dict()
                if data.get("path"):
                    data["path"] = redact_home(data["path"])
                items.append(data)
            return {
                "success": True,
                "database_id": database_id,
                "signal_id": signal_id,
                "media_type": media_type,
                "media_count": len(items),
                "media": items,
            }
        except Exception as exc:
            return error_response(exc)

    def compare_measurement(
        self,
        database_id: str,
        measured_frequency_hz: float,
        measured_bandwidth_hz: float | None = None,
        estimated_modulation: str | None = None,
        estimated_mode: str | None = None,
        spectral_features: dict[str, Any] | None = None,
        tolerance_hz: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            measured_frequency_hz = _validate_frequency(
                "measured_frequency_hz", measured_frequency_hz
            )
            tol = _validate_tolerance(
                "tolerance_hz",
                tolerance_hz
                if tolerance_hz is not None
                else self.config.frequency_tolerance_hz,
            )
            if measured_bandwidth_hz is not None:
                measured_bandwidth_hz = _validate_frequency(
                    "measured_bandwidth_hz", measured_bandwidth_hz
                )
            limit = _validate_limit(limit, self.config.maximum_results)

            db = self.registry.open(database_id)
            matches = db.search_by_frequency(
                frequency_hz=measured_frequency_hz,
                tolerance_hz=tol,
                bandwidth_hz=measured_bandwidth_hz,
                bandwidth_tolerance_hz=self.config.bandwidth_tolerance_hz,
                modulation=estimated_modulation,
                mode=estimated_mode,
                limit=limit,
            )
            return {
                "success": True,
                "database_id": database_id,
                "measurement": {
                    "measured_frequency_hz": measured_frequency_hz,
                    "measured_bandwidth_hz": measured_bandwidth_hz,
                    "estimated_modulation": estimated_modulation,
                    "estimated_mode": estimated_mode,
                    "spectral_features": spectral_features or {},
                    "note": (
                        "Estimated modulation/mode are caller-supplied "
                        "hypotheses; they were not measured by this server."
                    ),
                },
                "ranking_method": (
                    "Deterministic Python scoring: weighted frequency error, "
                    "optional bandwidth agreement, and optional modulation/mode "
                    "agreement. No LLM is used inside the server."
                ),
                "match_count": len(matches),
                "candidates": [m.to_dict() for m in matches],
                "uncertainty": (
                    "Ranking reflects agreement with documentation only. A high "
                    "score is not proof of identity."
                ),
                "disclaimer": DISCLAIMER,
            }
        except Exception as exc:
            return error_response(exc)

    def search_frequency_range(
        self,
        database_id: str,
        start_frequency_hz: float,
        stop_frequency_hz: float,
        center_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            start_frequency_hz = _validate_frequency(
                "start_frequency_hz", start_frequency_hz
            )
            stop_frequency_hz = _validate_frequency(
                "stop_frequency_hz", stop_frequency_hz
            )
            if center_hz is not None:
                center_hz = _validate_frequency("center_hz", center_hz)
            limit = _validate_limit(limit, self.config.maximum_results)
            db = self.registry.open(database_id)
            entries = db.search_frequency_range(
                start_frequency_hz=start_frequency_hz,
                stop_frequency_hz=stop_frequency_hz,
                center_hz=center_hz,
                modulation=modulation,
                mode=mode,
                category=category,
                limit=limit,
            )
            return {
                "success": True,
                "database_id": database_id,
                "start_frequency_hz": start_frequency_hz,
                "stop_frequency_hz": stop_frequency_hz,
                "entry_count": len(entries),
                "entries": entries,
                "note": (
                    "Reference entries in current range - not measured "
                    "detections. A documented frequency being in range does "
                    "not mean the signal is physically present."
                ),
            }
        except Exception as exc:
            return error_response(exc)

    def health_check(self) -> dict[str, Any]:
        try:
            import importlib.metadata as importlib_metadata

            try:
                mcp_version = importlib_metadata.version("mcp")
            except Exception:
                mcp_version = "unknown"

            entries = self.registry.discover()
            accessible = 0
            for entry in entries:
                try:
                    self.registry.open(entry.database_id).database_info()
                    accessible += 1
                except Exception:
                    pass

            return {
                "success": True,
                "server_status": "ok",
                "mcp_sdk_version": mcp_version,
                "python_version": platform.python_version(),
                "artemis_data_root": redact_home(str(self.registry.data_root)),
                "artemis_data_root_exists": self.registry.data_root.exists(),
                "database_count": len(entries),
                "accessible_database_count": accessible,
                "read_only": True,
                "transmit_enabled": False,
            }
        except Exception as exc:
            return error_response(exc)


def build_server(config: ArtemisConfig | None = None):
    """Construct a FastMCP server bound to an :class:`ArtemisService`."""
    from mcp.server.fastmcp import FastMCP

    service = ArtemisService(config)
    mcp = FastMCP("artemis-mcp")

    @mcp.tool()
    def artemis_list_databases() -> dict[str, Any]:
        """Discover all read-only Artemis data.sqlite databases."""
        return service.list_databases()

    @mcp.tool()
    def artemis_get_database_info(database_id: str) -> dict[str, Any]:
        """Return metadata and record counts for one Artemis database."""
        return service.get_database_info(database_id)

    @mcp.tool()
    def artemis_search_by_frequency(
        database_id: str,
        frequency_hz: float,
        tolerance_hz: float | None = None,
        bandwidth_hz: float | None = None,
        bandwidth_tolerance_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Rank documented signals near a measured frequency (hypotheses only)."""
        return service.search_by_frequency(
            database_id,
            frequency_hz,
            tolerance_hz,
            bandwidth_hz,
            bandwidth_tolerance_hz,
            modulation,
            mode,
            limit,
        )

    @mcp.tool()
    def artemis_search_signals(
        database_id: str,
        text: str | None = None,
        frequency_min_hz: float | None = None,
        frequency_max_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        location: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search Artemis signals by text and optional structured filters."""
        return service.search_signals(
            database_id,
            text,
            frequency_min_hz,
            frequency_max_hz,
            modulation,
            mode,
            category,
            location,
            limit,
        )

    @mcp.tool()
    def artemis_get_signal(database_id: str, signal_id: int) -> dict[str, Any]:
        """Return complete documented details for one Artemis signal."""
        return service.get_signal(database_id, signal_id)

    @mcp.tool()
    def artemis_get_signal_media(
        database_id: str, signal_id: int, media_type: str = "all"
    ) -> dict[str, Any]:
        """Return validated local media paths for a signal (image/audio/document/all)."""
        return service.get_signal_media(database_id, signal_id, media_type)

    @mcp.tool()
    def artemis_compare_measurement(
        database_id: str,
        measured_frequency_hz: float,
        measured_bandwidth_hz: float | None = None,
        estimated_modulation: str | None = None,
        estimated_mode: str | None = None,
        spectral_features: dict[str, Any] | None = None,
        tolerance_hz: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Rank Artemis references against a measurement using deterministic logic."""
        return service.compare_measurement(
            database_id,
            measured_frequency_hz,
            measured_bandwidth_hz,
            estimated_modulation,
            estimated_mode,
            spectral_features,
            tolerance_hz,
            limit,
        )

    @mcp.tool()
    def artemis_search_frequency_range(
        database_id: str,
        start_frequency_hz: float,
        stop_frequency_hz: float,
        center_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List documented signals whose frequency falls within a range (references, not detections)."""
        return service.search_frequency_range(
            database_id,
            start_frequency_hz,
            stop_frequency_hz,
            center_hz,
            modulation,
            mode,
            category,
            limit,
        )

    @mcp.tool()
    def artemis_health_check() -> dict[str, Any]:
        """Report server status, SDK/Python versions, and database accessibility."""
        return service.health_check()

    return mcp, service


def main() -> int:
    """Entry point: run the Artemis MCP server over stdio."""
    try:
        config = load_config()
    except ArtemisError as exc:
        print(error_response(exc), file=sys.stderr)
        return 1
    mcp, _ = build_server(config)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
