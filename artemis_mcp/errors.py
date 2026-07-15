"""Structured errors for the Artemis MCP server.

Every failure surfaced to an MCP caller is converted into a JSON-friendly
dictionary so the calling model receives a clear, machine-readable reason
instead of a Python traceback.
"""

from __future__ import annotations

from typing import Any


class ArtemisError(Exception):
    """Base class for all Artemis MCP errors."""

    code = "artemis_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
            },
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class ConfigurationError(ArtemisError):
    code = "configuration_error"


class DatabaseNotFoundError(ArtemisError):
    code = "database_not_found"


class DatabaseUnavailableError(ArtemisError):
    """The database exists but cannot be opened (locked, corrupt, busy)."""

    code = "database_unavailable"


class SchemaError(ArtemisError):
    """A required table or column is missing from the Artemis database."""

    code = "schema_error"


class SignalNotFoundError(ArtemisError):
    code = "signal_not_found"


class ValidationError(ArtemisError):
    """A tool argument failed validation."""

    code = "invalid_argument"


class PathSecurityError(ArtemisError):
    """A path failed containment validation."""

    code = "path_security_error"


def error_response(exc: Exception) -> dict[str, Any]:
    """Convert any exception into a structured MCP tool error payload."""
    if isinstance(exc, ArtemisError):
        return exc.to_dict()
    return {
        "success": False,
        "error": {
            "code": "internal_error",
            "message": f"{type(exc).__name__}: {exc}",
        },
    }
