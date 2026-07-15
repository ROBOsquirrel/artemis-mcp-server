"""Human-readable formatting helpers for Artemis MCP results."""

from __future__ import annotations


def format_frequency(frequency_hz: float) -> str:
    """Format a frequency in Hz using an appropriate SI unit."""
    magnitude = abs(frequency_hz)
    if magnitude >= 1e9:
        return f"{frequency_hz / 1e9:.6f} GHz"
    if magnitude >= 1e6:
        return f"{frequency_hz / 1e6:.6f} MHz"
    if magnitude >= 1e3:
        return f"{frequency_hz / 1e3:.3f} kHz"
    return f"{frequency_hz:.1f} Hz"


def frequency_reason(error_hz: float, tolerance_hz: float) -> str:
    return (
        f"Documented frequency is within {format_frequency(abs(error_hz))} "
        f"of the measured value (search tolerance "
        f"{format_frequency(tolerance_hz)})."
    )


def bandwidth_reason(error_hz: float, tolerance_hz: float) -> str:
    if abs(error_hz) <= tolerance_hz:
        return (
            f"Measured bandwidth is within {format_frequency(abs(error_hz))} "
            "of a documented bandwidth."
        )
    return (
        f"Closest documented bandwidth differs by "
        f"{format_frequency(abs(error_hz))} (outside the "
        f"{format_frequency(tolerance_hz)} tolerance)."
    )


def text_match_reason(field_name: str, supplied: str, matched: str) -> str:
    return (
        f"Supplied {field_name} {supplied!r} matches documented "
        f"{field_name} {matched!r}."
    )


def redact_home(path: str) -> str:
    """Replace the user's home directory prefix with ``~`` in a path string.

    Keeps tool output useful without echoing the full home path.
    """
    from pathlib import Path

    home = str(Path.home())
    if home and path.startswith(home):
        return "~" + path[len(home):]
    return path
