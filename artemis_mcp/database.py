"""Read-only access to Artemis SQLite signal databases.

Design rules enforced here:

* Databases are opened only through read-only ``file:...?mode=ro`` URIs and
  with ``PRAGMA query_only = ON``. No write path exists in this module.
* Only parameterized queries are used. No user text is concatenated into SQL.
* Discovered database paths are mapped to stable opaque ``database_id``
  tokens; callers never pass raw filesystem paths.
* Every path is validated for containment inside the configured Artemis
  data root before it is opened or exposed.
* Missing optional tables/columns raise a clear ``SchemaError`` rather than
  crashing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import DB_TIMEOUT_SECONDS, ArtemisConfig
from .errors import (
    DatabaseNotFoundError,
    DatabaseUnavailableError,
    PathSecurityError,
    SchemaError,
    SignalNotFoundError,
)
from .models import (
    DatabaseEntry,
    FrequencyMatch,
    MediaItem,
    SignalDetail,
)
from .formatters import bandwidth_reason, frequency_reason

SQL_NAME = "data.sqlite"

# Tables the Artemis schema is expected to expose. ``info`` and ``signals``
# are required; the rest are optional and tolerated if absent.
REQUIRED_TABLES = ("info", "signals")
OPTIONAL_TABLES = (
    "frequency",
    "bandwidth",
    "modulation",
    "mode",
    "location",
    "category",
    "category_label",
    "acf",
    "documents",
)


def _path_is_contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def make_database_id(path: Path) -> str:
    """Return a stable opaque id for a database path.

    The id is derived from the resolved path so it is stable across calls
    but never reveals the path itself.
    """
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return f"db_{digest[:16]}"


class ArtemisDatabaseRegistry:
    """Discovers Artemis databases and maps ids to validated paths."""

    def __init__(self, config: ArtemisConfig) -> None:
        self.config = config
        self.data_root = config.resolved_data_root()
        self._id_to_path: dict[str, Path] = {}

    # ------------------------------------------------------------------ discovery
    def discover(self) -> list[DatabaseEntry]:
        """Find every ``data.sqlite`` under the Artemis data root."""
        entries: list[DatabaseEntry] = []
        self._id_to_path.clear()

        if not self.data_root.exists():
            return entries

        for sqlite_path in sorted(self.data_root.rglob(SQL_NAME)):
            if not sqlite_path.is_file():
                continue
            if not _path_is_contained(sqlite_path, self.data_root):
                continue
            database_id = make_database_id(sqlite_path)
            self._id_to_path[database_id] = sqlite_path.resolve()
            entry = DatabaseEntry(
                database_id=database_id,
                path=str(sqlite_path.resolve()),
            )
            self._augment_info(entry, sqlite_path)
            entries.append(entry)

        return entries

    def _augment_info(self, entry: DatabaseEntry, path: Path) -> None:
        """Best-effort population of name/date/version from the info table."""
        try:
            db = ArtemisDatabase(path, self.config)
            info = db.read_info()
            entry.name = info.get("name", "") or path.parent.name
            entry.date = info.get("date", "")
            entry.version = info.get("version", "")
            # Artemis marks its shipped databases editable=1, but this MCP
            # server is read-only regardless. Report the stored flag.
            entry.editable = bool(info.get("editable", 0))
        except Exception:
            entry.name = path.parent.name

    # ------------------------------------------------------------------ resolution
    def resolve(self, database_id: str) -> Path:
        """Return the validated path for a database id, or raise."""
        if not self._id_to_path:
            self.discover()
        path = self._id_to_path.get(database_id)
        if path is None:
            raise DatabaseNotFoundError(
                "Unknown database_id. Call artemis_list_databases first.",
                database_id=database_id,
            )
        # Re-validate containment unless external paths are explicitly allowed.
        if not self.config.allow_external_database_paths and not _path_is_contained(
            path, self.data_root
        ):
            raise PathSecurityError(
                "Database path is outside the configured Artemis data root."
            )
        if not path.is_file():
            raise DatabaseNotFoundError(
                "The database file no longer exists.", database_id=database_id
            )
        return path

    def open(self, database_id: str) -> "ArtemisDatabase":
        return ArtemisDatabase(self.resolve(database_id), self.config)


class ArtemisDatabase:
    """A single read-only Artemis database connection factory."""

    def __init__(self, path: Path, config: ArtemisConfig) -> None:
        self.path = Path(path).resolve()
        self.config = config
        self.media_dir = self.path.parent / "media"

    # ------------------------------------------------------------------ connection
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a read-only connection. Raises DatabaseUnavailableError."""
        uri = f"file:{self.path.as_posix()}?mode=ro&immutable=0"
        try:
            conn = sqlite3.connect(
                uri, uri=True, timeout=DB_TIMEOUT_SECONDS, check_same_thread=False
            )
        except sqlite3.OperationalError as exc:
            raise DatabaseUnavailableError(
                f"Could not open the Artemis database: {exc}"
            ) from exc
        try:
            conn.row_factory = sqlite3.Row
            # Belt-and-braces: forbid any write even if a URI slipped through.
            conn.execute("PRAGMA query_only = ON;")
            conn.execute(f"PRAGMA busy_timeout = {int(DB_TIMEOUT_SECONDS * 1000)};")
            yield conn
        except sqlite3.DatabaseError as exc:
            raise DatabaseUnavailableError(
                f"Artemis database error: {exc}"
            ) from exc
        finally:
            conn.close()

    def _query(
        self, conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
    ) -> list[sqlite3.Row]:
        try:
            cur = conn.execute(sql, tuple(params))
            return cur.fetchall()
        except sqlite3.DatabaseError as exc:
            raise DatabaseUnavailableError(
                f"Query failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------ schema
    def available_tables(self, conn: sqlite3.Connection | None = None) -> list[str]:
        if conn is not None:
            rows = self._query(
                conn,
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            )
            return [r["name"] for r in rows]
        with self.connect() as c:
            return self.available_tables(c)

    def _require_tables(self, conn: sqlite3.Connection) -> set[str]:
        tables = set(self.available_tables(conn))
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            raise SchemaError(
                "Artemis database is missing required tables: "
                + ", ".join(missing),
                missing_tables=missing,
            )
        return tables

    # ------------------------------------------------------------------ info & stats
    def read_info(self) -> dict[str, Any]:
        with self.connect() as conn:
            self._require_tables(conn)
            rows = self._query(
                conn, "SELECT NAME, DATE, VERSION, EDITABLE FROM info LIMIT 1"
            )
            if not rows:
                return {}
            row = rows[0]
            return {
                "name": row["NAME"],
                "date": str(row["DATE"]) if row["DATE"] is not None else "",
                "version": str(row["VERSION"]) if row["VERSION"] is not None else "",
                "editable": row["EDITABLE"],
            }

    def database_info(self) -> dict[str, Any]:
        with self.connect() as conn:
            tables = self._require_tables(conn)
            info = {}
            rows = self._query(
                conn, "SELECT NAME, DATE, VERSION, EDITABLE FROM info LIMIT 1"
            )
            if rows:
                r = rows[0]
                info = {
                    "name": r["NAME"],
                    "date": str(r["DATE"]) if r["DATE"] is not None else "",
                    "version": str(r["VERSION"]) if r["VERSION"] is not None else "",
                    "editable": bool(r["EDITABLE"]),
                }

            def count(table: str, where: str = "") -> int:
                if table not in tables:
                    return 0
                sql = f"SELECT COUNT(*) AS n FROM {table} {where}"
                return int(self._query(conn, sql)[0]["n"])

            return {
                "name": info.get("name", ""),
                "date": info.get("date", ""),
                "version": info.get("version", ""),
                "editable": info.get("editable", False),
                "read_only": True,
                "signal_count": count("signals"),
                "modulation_count": count("modulation"),
                "image_count": count("documents", "WHERE TYPE = 'Image'"),
                "audio_count": count("documents", "WHERE TYPE = 'Audio'"),
                "document_count": count("documents"),
                "available_tables": sorted(tables),
            }

    # ------------------------------------------------------------------ signal detail
    def get_signal(self, signal_id: int) -> SignalDetail:
        with self.connect() as conn:
            tables = self._require_tables(conn)
            base = self._query(
                conn,
                "SELECT NAME, DESCRIPTION, URL FROM signals WHERE SIG_ID = ?",
                (signal_id,),
            )
            if not base:
                raise SignalNotFoundError(
                    "No signal with that id in this database.",
                    signal_id=signal_id,
                )
            row = base[0]
            detail = SignalDetail(
                signal_id=signal_id,
                name=row["NAME"] or "",
                description=row["DESCRIPTION"] or "",
                url=row["URL"] or "",
            )

            if "frequency" in tables:
                detail.frequencies_hz = [
                    {"value_hz": float(r["VALUE"]), "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM frequency WHERE SIG_ID = ? "
                        "AND VALUE IS NOT NULL ORDER BY VALUE",
                        (signal_id,),
                    )
                ]
            if "bandwidth" in tables:
                detail.bandwidths_hz = [
                    {"value_hz": float(r["VALUE"]), "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM bandwidth WHERE SIG_ID = ? "
                        "AND VALUE IS NOT NULL ORDER BY VALUE",
                        (signal_id,),
                    )
                ]
            if "modulation" in tables:
                detail.modulations = [
                    {"value": r["VALUE"] or "", "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM modulation WHERE SIG_ID = ?",
                        (signal_id,),
                    )
                ]
            if "mode" in tables:
                detail.modes = [
                    {"value": r["VALUE"] or "", "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM mode WHERE SIG_ID = ?",
                        (signal_id,),
                    )
                ]
            if "location" in tables:
                detail.locations = [
                    {"value": r["VALUE"] or "", "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM location WHERE SIG_ID = ?",
                        (signal_id,),
                    )
                ]
            if "acf" in tables:
                detail.acf = [
                    {"value": float(r["VALUE"]) if r["VALUE"] is not None else None,
                     "description": r["DESCRIPTION"] or ""}
                    for r in self._query(
                        conn,
                        "SELECT VALUE, DESCRIPTION FROM acf WHERE SIG_ID = ?",
                        (signal_id,),
                    )
                ]
            if "category" in tables and "category_label" in tables:
                detail.categories = [
                    r["VALUE"] or ""
                    for r in self._query(
                        conn,
                        "SELECT category_label.VALUE AS VALUE FROM category "
                        "INNER JOIN category_label "
                        "ON category.CLB_ID = category_label.CLB_ID "
                        "WHERE category.SIG_ID = ?",
                        (signal_id,),
                    )
                ]
            if "documents" in tables:
                detail.media = self._load_media(conn, signal_id)
                self._assign_default_media(detail)

            return detail

    def _load_media(
        self, conn: sqlite3.Connection, signal_id: int
    ) -> list[MediaItem]:
        rows = self._query(
            conn,
            "SELECT DOC_ID, EXTENSION, NAME, DESCRIPTION, TYPE, PREVIEW "
            "FROM documents WHERE SIG_ID = ? ORDER BY TYPE",
            (signal_id,),
        )
        items: list[MediaItem] = []
        for r in rows:
            doc_id = int(r["DOC_ID"])
            extension = (r["EXTENSION"] or "").lstrip(".")
            filename = f"{doc_id}.{extension}" if extension else str(doc_id)
            validated = self.validate_media_path(filename)
            items.append(
                MediaItem(
                    doc_id=doc_id,
                    media_type=(r["TYPE"] or "").lower() or "document",
                    name=r["NAME"] or "",
                    description=r["DESCRIPTION"] or "",
                    extension=extension,
                    is_preview=bool(r["PREVIEW"]),
                    path=str(validated) if validated else None,
                    exists=bool(validated and validated.is_file()),
                )
            )
        return items

    @staticmethod
    def _assign_default_media(detail: SignalDetail) -> None:
        for item in detail.media:
            if item.media_type == "image" and item.is_preview and detail.spectrum_image_path is None:
                detail.spectrum_image_path = item.path
                detail.spectrum_image_exists = item.exists
            if item.media_type == "audio" and item.is_preview and detail.audio_path is None:
                detail.audio_path = item.path
                detail.audio_exists = item.exists

    # ------------------------------------------------------------------ media paths
    def validate_media_path(self, filename: str) -> Path | None:
        """Resolve a media filename inside this database's media directory.

        Returns the resolved path only if it is contained within the media
        directory (blocks ``..`` traversal and absolute-path injection).
        """
        if not filename:
            return None
        # Reject any path separators or drive/absolute components outright.
        candidate = Path(filename)
        if candidate.is_absolute() or len(candidate.parts) != 1:
            return None
        resolved = (self.media_dir / candidate.name).resolve()
        if not _path_is_contained(resolved, self.media_dir):
            return None
        return resolved

    # ------------------------------------------------------------------ search
    def _signal_frequencies(
        self, conn: sqlite3.Connection, tables: set[str], signal_id: int
    ) -> list[float]:
        if "frequency" not in tables:
            return []
        return [
            float(r["VALUE"])
            for r in self._query(
                conn,
                "SELECT VALUE FROM frequency WHERE SIG_ID = ? AND VALUE IS NOT NULL",
                (signal_id,),
            )
        ]

    def _signal_bandwidths(
        self, conn: sqlite3.Connection, tables: set[str], signal_id: int
    ) -> list[float]:
        if "bandwidth" not in tables:
            return []
        return [
            float(r["VALUE"])
            for r in self._query(
                conn,
                "SELECT VALUE FROM bandwidth WHERE SIG_ID = ? AND VALUE IS NOT NULL",
                (signal_id,),
            )
        ]

    def _signal_values(
        self, conn: sqlite3.Connection, tables: set[str], table: str, signal_id: int
    ) -> list[str]:
        if table not in tables:
            return []
        return [
            r["VALUE"] or ""
            for r in self._query(
                conn,
                f"SELECT VALUE FROM {table} WHERE SIG_ID = ?",
                (signal_id,),
            )
        ]

    def _signal_categories(
        self, conn: sqlite3.Connection, tables: set[str], signal_id: int
    ) -> list[str]:
        if "category" not in tables or "category_label" not in tables:
            return []
        return [
            r["VALUE"] or ""
            for r in self._query(
                conn,
                "SELECT category_label.VALUE AS VALUE FROM category "
                "INNER JOIN category_label "
                "ON category.CLB_ID = category_label.CLB_ID WHERE category.SIG_ID = ?",
                (signal_id,),
            )
        ]

    def search_by_frequency(
        self,
        frequency_hz: float,
        tolerance_hz: float,
        bandwidth_hz: float | None = None,
        bandwidth_tolerance_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        limit: int = 20,
    ) -> list[FrequencyMatch]:
        with self.connect() as conn:
            tables = self._require_tables(conn)
            if "frequency" not in tables:
                raise SchemaError(
                    "This database has no 'frequency' table; frequency "
                    "search is unavailable."
                )
            low = frequency_hz - tolerance_hz
            high = frequency_hz + tolerance_hz
            # Candidate signals whose documented frequency falls in the window.
            rows = self._query(
                conn,
                "SELECT DISTINCT SIG_ID FROM frequency "
                "WHERE VALUE IS NOT NULL AND VALUE BETWEEN ? AND ?",
                (low, high),
            )
            candidate_ids = [int(r["SIG_ID"]) for r in rows]
            matches: list[FrequencyMatch] = []
            for sig_id in candidate_ids:
                match = self._build_match(
                    conn,
                    tables,
                    sig_id,
                    frequency_hz,
                    tolerance_hz,
                    bandwidth_hz,
                    bandwidth_tolerance_hz,
                    modulation,
                    mode,
                )
                if match is not None:
                    matches.append(match)

            matches.sort(key=lambda m: (-m.match_score, abs(m.frequency_error_hz)))
            return matches[:limit]

    def _build_match(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        sig_id: int,
        frequency_hz: float,
        tolerance_hz: float,
        bandwidth_hz: float | None,
        bandwidth_tolerance_hz: float | None,
        modulation: str | None,
        mode: str | None,
    ) -> FrequencyMatch | None:
        freqs = self._signal_frequencies(conn, tables, sig_id)
        if not freqs:
            return None
        # Closest documented frequency to the measurement.
        ref_freq = min(freqs, key=lambda f: abs(f - frequency_hz))
        freq_error = ref_freq - frequency_hz
        if abs(freq_error) > tolerance_hz:
            return None

        base = self._query(
            conn,
            "SELECT NAME, DESCRIPTION, URL FROM signals WHERE SIG_ID = ?",
            (sig_id,),
        )
        if not base:
            return None
        name = base[0]["NAME"] or ""
        description = base[0]["DESCRIPTION"] or ""
        url = base[0]["URL"] or ""

        bandwidths = self._signal_bandwidths(conn, tables, sig_id)
        modulations = self._signal_values(conn, tables, "modulation", sig_id)
        modes = self._signal_values(conn, tables, "mode", sig_id)
        locations = self._signal_values(conn, tables, "location", sig_id)
        categories = self._signal_categories(conn, tables, sig_id)

        reasons: list[str] = []
        # Frequency component: 1.0 at exact match, 0 at tolerance edge.
        freq_component = max(0.0, 1.0 - abs(freq_error) / tolerance_hz)
        reasons.append(frequency_reason(freq_error, tolerance_hz))

        weight_sum = 2.0  # frequency weight
        weighted = freq_component * 2.0

        closest_bw: float | None = None
        bw_error: float | None = None
        if bandwidth_hz is not None and bandwidths:
            bw_tol = bandwidth_tolerance_hz or tolerance_hz
            closest_bw = min(bandwidths, key=lambda b: abs(b - bandwidth_hz))
            bw_error = closest_bw - bandwidth_hz
            bw_component = max(0.0, 1.0 - abs(bw_error) / bw_tol) if bw_tol else 0.0
            weighted += bw_component * 1.0
            weight_sum += 1.0
            reasons.append(bandwidth_reason(bw_error, bw_tol))

        if modulation and modulations:
            weight_sum += 1.0
            if any(modulation.lower() == m.lower() for m in modulations):
                weighted += 1.0
                reasons.append(
                    f"Documented modulation matches supplied modulation "
                    f"{modulation!r}."
                )
            else:
                reasons.append(
                    f"Supplied modulation {modulation!r} not among documented "
                    f"modulations {modulations}."
                )

        if mode and modes:
            weight_sum += 1.0
            if any(mode.lower() == m.lower() for m in modes):
                weighted += 1.0
                reasons.append(
                    f"Documented mode matches supplied mode {mode!r}."
                )
            else:
                reasons.append(
                    f"Supplied mode {mode!r} not among documented modes {modes}."
                )

        score = round(weighted / weight_sum, 4) if weight_sum else 0.0

        return FrequencyMatch(
            signal_id=sig_id,
            name=name,
            description=description,
            reference_frequency_hz=ref_freq,
            frequency_error_hz=freq_error,
            documented_frequencies_hz=sorted(freqs),
            documented_bandwidths_hz=sorted(bandwidths),
            closest_bandwidth_hz=closest_bw,
            bandwidth_error_hz=bw_error,
            modulations=modulations,
            modes=modes,
            locations=locations,
            categories=categories,
            url=url,
            match_score=score,
            match_reasons=reasons,
        )

    def search_signals(
        self,
        text: str | None = None,
        frequency_min_hz: float | None = None,
        frequency_max_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        location: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            tables = self._require_tables(conn)
            # Build an intersection of candidate SIG_IDs across each supplied
            # filter, each using parameterized queries only.
            id_sets: list[set[int]] = []

            if text:
                like = f"%{text}%"
                rows = self._query(
                    conn,
                    "SELECT SIG_ID FROM signals WHERE NAME LIKE ? OR DESCRIPTION LIKE ?",
                    (like, like),
                )
                id_sets.append({int(r["SIG_ID"]) for r in rows})

            if (frequency_min_hz is not None or frequency_max_hz is not None) and (
                "frequency" in tables
            ):
                low = frequency_min_hz if frequency_min_hz is not None else -1e18
                high = frequency_max_hz if frequency_max_hz is not None else 1e18
                rows = self._query(
                    conn,
                    "SELECT DISTINCT SIG_ID FROM frequency "
                    "WHERE VALUE IS NOT NULL AND VALUE BETWEEN ? AND ?",
                    (low, high),
                )
                id_sets.append({int(r["SIG_ID"]) for r in rows})

            for table, value in (
                ("modulation", modulation),
                ("mode", mode),
                ("location", location),
            ):
                if value and table in tables:
                    rows = self._query(
                        conn,
                        f"SELECT DISTINCT SIG_ID FROM {table} "
                        "WHERE LOWER(VALUE) = LOWER(?)",
                        (value,),
                    )
                    id_sets.append({int(r["SIG_ID"]) for r in rows})

            if category and "category" in tables and "category_label" in tables:
                rows = self._query(
                    conn,
                    "SELECT category.SIG_ID AS SIG_ID FROM category "
                    "INNER JOIN category_label "
                    "ON category.CLB_ID = category_label.CLB_ID "
                    "WHERE LOWER(category_label.VALUE) = LOWER(?)",
                    (category,),
                )
                id_sets.append({int(r["SIG_ID"]) for r in rows})

            if id_sets:
                matched = set.intersection(*id_sets) if len(id_sets) > 1 else id_sets[0]
            else:
                rows = self._query(conn, "SELECT SIG_ID FROM signals")
                matched = {int(r["SIG_ID"]) for r in rows}

            results: list[dict[str, Any]] = []
            for sig_id in sorted(matched)[: max(0, limit)]:
                base = self._query(
                    conn,
                    "SELECT NAME, DESCRIPTION, URL FROM signals WHERE SIG_ID = ?",
                    (sig_id,),
                )[0]
                results.append(
                    {
                        "signal_id": sig_id,
                        "name": base["NAME"] or "",
                        "description": base["DESCRIPTION"] or "",
                        "url": base["URL"] or "",
                        "documented_frequencies_hz": sorted(
                            self._signal_frequencies(conn, tables, sig_id)
                        ),
                        "modulations": self._signal_values(
                            conn, tables, "modulation", sig_id
                        ),
                        "modes": self._signal_values(conn, tables, "mode", sig_id),
                        "categories": self._signal_categories(conn, tables, sig_id),
                        "locations": self._signal_values(
                            conn, tables, "location", sig_id
                        ),
                    }
                )
            return results

    def search_frequency_range(
        self,
        start_frequency_hz: float,
        stop_frequency_hz: float,
        center_hz: float | None = None,
        modulation: str | None = None,
        mode: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return documented signals whose frequency falls in [start, stop].

        One row per documented frequency inside the range. Read-only;
        parameterized only. These are reference entries, not detections.
        """
        if start_frequency_hz > stop_frequency_hz:
            start_frequency_hz, stop_frequency_hz = (
                stop_frequency_hz,
                start_frequency_hz,
            )
        if center_hz is None:
            center_hz = (start_frequency_hz + stop_frequency_hz) / 2.0

        with self.connect() as conn:
            tables = self._require_tables(conn)
            if "frequency" not in tables:
                raise SchemaError(
                    "This database has no 'frequency' table; range search "
                    "is unavailable."
                )
            rows = self._query(
                conn,
                "SELECT SIG_ID, VALUE FROM frequency "
                "WHERE VALUE IS NOT NULL AND VALUE BETWEEN ? AND ? "
                "ORDER BY VALUE",
                (start_frequency_hz, stop_frequency_hz),
            )
            results: list[dict[str, Any]] = []
            for row in rows:
                sig_id = int(row["SIG_ID"])
                frequency_hz = float(row["VALUE"])
                modulations = self._signal_values(conn, tables, "modulation", sig_id)
                modes = self._signal_values(conn, tables, "mode", sig_id)
                categories = self._signal_categories(conn, tables, sig_id)
                if modulation and not any(
                    modulation.lower() == m.lower() for m in modulations
                ):
                    continue
                if mode and not any(mode.lower() == m.lower() for m in modes):
                    continue
                if category and not any(
                    category.lower() == c.lower() for c in categories
                ):
                    continue
                base = self._query(
                    conn,
                    "SELECT NAME, DESCRIPTION, URL FROM signals WHERE SIG_ID = ?",
                    (sig_id,),
                )
                if not base:
                    continue
                bandwidths = self._signal_bandwidths(conn, tables, sig_id)
                results.append(
                    {
                        "signal_id": sig_id,
                        "name": base[0]["NAME"] or "",
                        "description": base[0]["DESCRIPTION"] or "",
                        "url": base[0]["URL"] or "",
                        "documented_frequency_hz": frequency_hz,
                        "distance_from_center_hz": frequency_hz - center_hz,
                        "documented_bandwidths_hz": sorted(bandwidths),
                        "modulations": modulations,
                        "modes": modes,
                        "categories": categories,
                        "locations": self._signal_values(
                            conn, tables, "location", sig_id
                        ),
                    }
                )
                if len(results) >= max(1, limit):
                    break
            return results
