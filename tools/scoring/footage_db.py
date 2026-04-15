"""Footage database: SQLite used-footage cache for deduplication and tracking.

Tracks which clips have been used across videos so the pipeline never
accidentally reuses the same shot. Also caches clip metadata and SigLIP
embeddings for fast re-scoring without re-downloading.

DB file lives at ``data/footage.db`` (auto-created on first use).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)


# Default DB path relative to project root
_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = _DB_DIR / "footage.db"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS footage (
    clip_id         TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT '',
    download_path   TEXT NOT NULL DEFAULT '',
    siglip_embedding BLOB,
    metadata        TEXT NOT NULL DEFAULT '{}',
    first_used_date TEXT NOT NULL DEFAULT '',
    usage_count     INTEGER NOT NULL DEFAULT 0,
    last_used_video TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_footage_source ON footage(source);
CREATE INDEX IF NOT EXISTS idx_footage_last_video ON footage(last_used_video);
"""


class FootageDB(BaseTool):
    """SQLite footage cache for deduplication and clip tracking.

    Inputs
    ------
    operation : str
        One of: ``record_usage``, ``check_used``, ``get_clip``,
        ``search_cache``, ``stats``.

    Operation-specific parameters:

    record_usage
        ``clip_id`` (str), ``source`` (str), ``download_path`` (str),
        ``video_name`` (str), ``metadata`` (dict, optional),
        ``siglip_embedding`` (list[float], optional — 1152-d vector).

    check_used
        ``clip_id`` (str) or ``clip_ids`` (list[str]).

    get_clip
        ``clip_id`` (str).

    search_cache
        ``source`` (str, optional), ``video_name`` (str, optional).

    stats
        No extra parameters.

    Returns
    -------
    ToolResult with operation-specific ``data``.
    """

    name = "footage_db"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_tracking"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []  # sqlite3 is stdlib
    capabilities = ["footage_tracking", "dedup"]
    best_for = [
        "tracking used footage across videos",
        "preventing clip reuse",
        "caching clip metadata and embeddings",
    ]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["record_usage", "check_used", "get_clip", "search_cache", "stats"],
            },
            "clip_id": {"type": "string"},
            "clip_ids": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string"},
            "download_path": {"type": "string"},
            "video_name": {"type": "string"},
            "metadata": {"type": "object"},
            "siglip_embedding": {
                "type": "array",
                "items": {"type": "number"},
                "description": "1152-d SigLIP embedding as a flat list of floats.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=64, vram_mb=0, disk_mb=50, network_required=False
    )

    def __init__(self, db_path: Optional[Path] = None):
        """Initialise with an optional custom DB path (useful for testing)."""
        self._db_path = db_path or _DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        """Return a lazily-opened SQLite connection, creating the schema if needed."""
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        return self._conn

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Dispatch to the requested operation."""
        operation = inputs.get("operation")
        if not operation:
            return ToolResult(success=False, error="'operation' is required.")

        dispatch = {
            "record_usage": self._record_usage,
            "check_used": self._check_used,
            "get_clip": self._get_clip,
            "search_cache": self._search_cache,
            "stats": self._stats,
        }

        handler = dispatch.get(operation)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"Unknown operation '{operation}'. Valid: {list(dispatch.keys())}",
            )

        start = time.time()
        try:
            result = handler(inputs)
            result.duration_seconds = round(time.time() - start, 4)
            return result
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Operation '{operation}' failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _record_usage(self, inputs: dict[str, Any]) -> ToolResult:
        """Record that a clip was used in a video."""
        clip_id = inputs.get("clip_id")
        if not clip_id:
            return ToolResult(success=False, error="clip_id is required for record_usage.")

        source = inputs.get("source", "")
        download_path = inputs.get("download_path", "")
        video_name = inputs.get("video_name", "")
        metadata = inputs.get("metadata", {})
        embedding_list = inputs.get("siglip_embedding")

        # Serialise embedding to bytes if provided
        embedding_blob: Optional[bytes] = None
        if embedding_list is not None:
            arr = np.array(embedding_list, dtype=np.float32)
            if arr.shape != (1152,):
                return ToolResult(
                    success=False,
                    error=f"siglip_embedding must have 1152 elements, got {arr.shape}.",
                )
            embedding_blob = arr.tobytes()

        today = time.strftime("%Y-%m-%d")
        metadata_json = json.dumps(metadata)

        conn = self._get_conn()
        # Check if clip already exists
        row = conn.execute(
            "SELECT clip_id, usage_count FROM footage WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()

        if row:
            # Update existing record
            conn.execute(
                """UPDATE footage
                   SET usage_count = usage_count + 1,
                       last_used_video = ?,
                       download_path = CASE WHEN ? != '' THEN ? ELSE download_path END,
                       metadata = CASE WHEN ? != '{}' THEN ? ELSE metadata END,
                       siglip_embedding = CASE WHEN ? IS NOT NULL THEN ? ELSE siglip_embedding END
                   WHERE clip_id = ?""",
                (
                    video_name,
                    download_path, download_path,
                    metadata_json, metadata_json,
                    embedding_blob, embedding_blob,
                    clip_id,
                ),
            )
            new_count = row["usage_count"] + 1
        else:
            # Insert new record
            conn.execute(
                """INSERT INTO footage
                   (clip_id, source, download_path, siglip_embedding, metadata,
                    first_used_date, usage_count, last_used_video)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (clip_id, source, download_path, embedding_blob,
                 metadata_json, today, video_name),
            )
            new_count = 1

        conn.commit()
        return ToolResult(
            success=True,
            data={
                "clip_id": clip_id,
                "usage_count": new_count,
                "recorded": True,
            },
        )

    def _check_used(self, inputs: dict[str, Any]) -> ToolResult:
        """Check if one or more clip IDs have been used before."""
        clip_id = inputs.get("clip_id")
        clip_ids = inputs.get("clip_ids", [])

        ids_to_check: list[str] = []
        if clip_id:
            ids_to_check.append(clip_id)
        if clip_ids:
            ids_to_check.extend(clip_ids)

        if not ids_to_check:
            return ToolResult(
                success=False,
                error="Provide 'clip_id' or 'clip_ids' for check_used.",
            )

        conn = self._get_conn()
        results: dict[str, dict[str, Any]] = {}

        for cid in ids_to_check:
            row = conn.execute(
                "SELECT clip_id, usage_count, last_used_video FROM footage WHERE clip_id = ?",
                (cid,),
            ).fetchone()
            if row:
                results[cid] = {
                    "used": True,
                    "usage_count": row["usage_count"],
                    "last_used_video": row["last_used_video"],
                }
            else:
                results[cid] = {"used": False, "usage_count": 0}

        return ToolResult(
            success=True,
            data={"results": results},
        )

    def _get_clip(self, inputs: dict[str, Any]) -> ToolResult:
        """Retrieve cached clip metadata by clip_id."""
        clip_id = inputs.get("clip_id")
        if not clip_id:
            return ToolResult(success=False, error="clip_id is required for get_clip.")

        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM footage WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()

        if not row:
            return ToolResult(
                success=True,
                data={"found": False, "clip_id": clip_id},
            )

        clip_data: dict[str, Any] = {
            "clip_id": row["clip_id"],
            "source": row["source"],
            "download_path": row["download_path"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "first_used_date": row["first_used_date"],
            "usage_count": row["usage_count"],
            "last_used_video": row["last_used_video"],
            "has_embedding": row["siglip_embedding"] is not None,
        }

        # Deserialise embedding if requested and present
        if row["siglip_embedding"] is not None:
            arr = np.frombuffer(row["siglip_embedding"], dtype=np.float32)
            clip_data["siglip_embedding_shape"] = list(arr.shape)

        return ToolResult(
            success=True,
            data={"found": True, "clip": clip_data},
        )

    def _search_cache(self, inputs: dict[str, Any]) -> ToolResult:
        """Search the cache by source or video name."""
        source = inputs.get("source")
        video_name = inputs.get("video_name")

        conn = self._get_conn()
        conditions: list[str] = []
        params: list[str] = []

        if source:
            conditions.append("source = ?")
            params.append(source)
        if video_name:
            conditions.append("last_used_video = ?")
            params.append(video_name)

        if not conditions:
            return ToolResult(
                success=False,
                error="Provide at least 'source' or 'video_name' for search_cache.",
            )

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT clip_id, source, download_path, usage_count, last_used_video "
            f"FROM footage WHERE {where} ORDER BY usage_count DESC LIMIT 100",
            params,
        ).fetchall()

        clips = [
            {
                "clip_id": r["clip_id"],
                "source": r["source"],
                "download_path": r["download_path"],
                "usage_count": r["usage_count"],
                "last_used_video": r["last_used_video"],
            }
            for r in rows
        ]

        return ToolResult(
            success=True,
            data={"clips": clips, "count": len(clips)},
        )

    def _stats(self, inputs: dict[str, Any]) -> ToolResult:
        """Return corpus statistics."""
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) as n FROM footage").fetchone()["n"]
        with_embedding = conn.execute(
            "SELECT COUNT(*) as n FROM footage WHERE siglip_embedding IS NOT NULL"
        ).fetchone()["n"]
        total_usages = conn.execute(
            "SELECT COALESCE(SUM(usage_count), 0) as n FROM footage"
        ).fetchone()["n"]

        source_counts = conn.execute(
            "SELECT source, COUNT(*) as n FROM footage GROUP BY source ORDER BY n DESC"
        ).fetchall()
        by_source = {r["source"]: r["n"] for r in source_counts}

        video_counts = conn.execute(
            "SELECT last_used_video, COUNT(*) as n FROM footage "
            "WHERE last_used_video != '' GROUP BY last_used_video ORDER BY n DESC LIMIT 20"
        ).fetchall()
        by_video = {r["last_used_video"]: r["n"] for r in video_counts}

        return ToolResult(
            success=True,
            data={
                "total_clips": total,
                "clips_with_embedding": with_embedding,
                "total_usages": total_usages,
                "by_source": by_source,
                "by_video": by_video,
                "db_path": str(self._db_path),
            },
        )
