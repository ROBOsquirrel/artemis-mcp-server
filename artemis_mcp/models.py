"""Typed result models for the Artemis MCP server.

Plain dataclasses with ``to_dict`` helpers keep the wire format explicit
and JSON-serializable without pulling extra dependencies into the server.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DatabaseEntry:
    """One discovered Artemis database (a ``data.sqlite`` file)."""

    database_id: str
    path: str
    name: str = ""
    date: str = ""
    version: str = ""
    editable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MediaItem:
    """One document/image/audio reference attached to a signal."""

    doc_id: int
    media_type: str
    name: str = ""
    description: str = ""
    extension: str = ""
    is_preview: bool = False
    path: str | None = None
    exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SignalDetail:
    """Complete documented information for a single Artemis signal."""

    signal_id: int
    name: str = ""
    description: str = ""
    url: str = ""
    frequencies_hz: list[dict[str, Any]] = field(default_factory=list)
    bandwidths_hz: list[dict[str, Any]] = field(default_factory=list)
    modulations: list[dict[str, Any]] = field(default_factory=list)
    modes: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    acf: list[dict[str, Any]] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    spectrum_image_path: str | None = None
    spectrum_image_exists: bool = False
    audio_path: str | None = None
    audio_exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["media"] = [
            item.to_dict() if isinstance(item, MediaItem) else item
            for item in self.media
        ]
        return data


@dataclass
class FrequencyMatch:
    """One ranked candidate returned by a frequency/measurement search.

    A match is always a *hypothesis*. Nothing in this structure claims a
    confirmed identification, and ``identification_status`` says so
    explicitly so downstream models cannot misread the result.
    """

    signal_id: int
    name: str
    description: str
    reference_frequency_hz: float
    frequency_error_hz: float
    documented_frequencies_hz: list[float] = field(default_factory=list)
    documented_bandwidths_hz: list[float] = field(default_factory=list)
    closest_bandwidth_hz: float | None = None
    bandwidth_error_hz: float | None = None
    modulations: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    url: str = ""
    match_score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    identification_status: str = "hypothesis"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
