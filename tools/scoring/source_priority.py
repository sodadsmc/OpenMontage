"""Source priority lookup: returns priority multipliers per source name.

Authoritative/government sources (CSB, NTSB, NARA, NASA) get a boost
because their footage is public-domain and carries institutional
credibility. Archive TV news gets a smaller boost. Everything else
defaults to 1.0.

The multiplier table is a class attribute so it can be tuned per-project
without modifying the module.
"""
from __future__ import annotations

import time
from typing import Any

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


class SourcePriority(BaseTool):
    """Look up source priority multipliers.

    Inputs
    ------
    source_name : str, optional
        A single source name to look up.
    source_names : list[str], optional
        Multiple source names to look up in batch.
        At least one of ``source_name`` or ``source_names`` must be provided.

    Returns
    -------
    ToolResult with ``data`` containing:
        multipliers : dict[str, float]
            Mapping of each requested source name to its priority multiplier.
        defaults_table : dict[str, float]
            The full multiplier table for reference.
    """

    name = "source_priority"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_scoring"
    provider = "openmontage"
    stability = ToolStability.PRODUCTION
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    capabilities = ["footage_scoring", "source_lookup"]
    best_for = [
        "looking up source priority multipliers",
        "auditing source weighting before scoring",
    ]

    input_schema = {
        "type": "object",
        "properties": {
            "source_name": {
                "type": "string",
                "description": "A single source name to look up.",
            },
            "source_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple source names to look up.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=32, vram_mb=0, disk_mb=0, network_required=False
    )

    # ------------------------------------------------------------------
    # Multiplier table — class attribute, tunable per-project
    # ------------------------------------------------------------------

    MULTIPLIER_TABLE: dict[str, float] = {
        "csb": 1.15,
        "ntsb": 1.15,
        "nara": 1.15,
        "nasa": 1.15,
        "archive_tv_news": 1.10,
    }

    DEFAULT_MULTIPLIER: float = 1.0

    def get_multiplier(self, source_name: str) -> float:
        """Return the multiplier for a given source name (case-insensitive)."""
        return self.MULTIPLIER_TABLE.get(source_name.lower(), self.DEFAULT_MULTIPLIER)

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Look up multipliers for one or more source names."""
        start = time.time()

        source_name: str | None = inputs.get("source_name")
        source_names: list[str] | None = inputs.get("source_names")

        # Build the list of names to look up
        names: list[str] = []
        if source_name:
            names.append(source_name)
        if source_names:
            names.extend(source_names)

        if not names:
            return ToolResult(
                success=False,
                error="Provide at least one of 'source_name' or 'source_names'.",
            )

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_names: list[str] = []
        for n in names:
            key = n.lower()
            if key not in seen:
                seen.add(key)
                unique_names.append(n)

        multipliers = {n: self.get_multiplier(n) for n in unique_names}

        elapsed = round(time.time() - start, 4)
        return ToolResult(
            success=True,
            data={
                "multipliers": multipliers,
                "defaults_table": dict(self.MULTIPLIER_TABLE),
                "default_multiplier": self.DEFAULT_MULTIPLIER,
            },
            duration_seconds=elapsed,
        )
