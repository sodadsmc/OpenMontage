"""Per-API-call logger for pipeline usage tracking.

Sits alongside CostTracker (which tracks tool-level budget governance).
This module logs every individual API call made during a pipeline run,
enabling detailed post-production usage reports.

Usage in tools::

    from tools.api_logger import api_logger

    # Log a call
    api_logger.log_call(
        tool="layer3_gemini",
        api="gemini",
        endpoint="/v1/models/gemini-2.0-pro:generateContent",
        input_units=5,          # clips sent
        input_unit_type="clips",
        output_units=1,
        output_unit_type="ranking",
        cost_usd=0.025,
        latency_ms=3400,
        status=200,
        metadata={"scene_id": "scene_07", "candidates": 5},
    )

    # At pipeline end
    report = api_logger.generate_report()
    api_logger.save_report("projects/my-video/artifacts/api_usage_report.json")
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class APICallRecord:
    """One logged API call."""

    __slots__ = (
        "id", "timestamp", "tool", "api", "endpoint",
        "input_units", "input_unit_type",
        "output_units", "output_unit_type",
        "cost_usd", "latency_ms", "status", "error", "metadata",
    )

    def __init__(
        self,
        tool: str,
        api: str,
        endpoint: str = "",
        input_units: float = 0,
        input_unit_type: str = "tokens",
        output_units: float = 0,
        output_unit_type: str = "tokens",
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        status: int = 200,
        error: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.id = str(uuid.uuid4())[:12]
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.tool = tool
        self.api = api
        self.endpoint = endpoint
        self.input_units = input_units
        self.input_unit_type = input_unit_type
        self.output_units = output_units
        self.output_unit_type = output_unit_type
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.status = status
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "api": self.api,
            "endpoint": self.endpoint,
            "input_units": self.input_units,
            "input_unit_type": self.input_unit_type,
            "output_units": self.output_units,
            "output_unit_type": self.output_unit_type,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "status": self.status,
        }
        if self.error:
            d["error"] = self.error
        if self.metadata:
            d["metadata"] = self.metadata
        return d


class APILogger:
    """Accumulates API call records for a single pipeline run."""

    def __init__(self) -> None:
        self._calls: list[APICallRecord] = []
        self._start_time: str = datetime.now(timezone.utc).isoformat()

    def reset(self) -> None:
        """Clear all recorded calls. Call at the start of a new pipeline run."""
        self._calls.clear()
        self._start_time = datetime.now(timezone.utc).isoformat()

    def log_call(
        self,
        tool: str,
        api: str,
        endpoint: str = "",
        input_units: float = 0,
        input_unit_type: str = "tokens",
        output_units: float = 0,
        output_unit_type: str = "tokens",
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        status: int = 200,
        error: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Log a single API call. Returns the call ID."""
        record = APICallRecord(
            tool=tool,
            api=api,
            endpoint=endpoint,
            input_units=input_units,
            input_unit_type=input_unit_type,
            output_units=output_units,
            output_unit_type=output_unit_type,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            status=status,
            error=error,
            metadata=metadata,
        )
        self._calls.append(record)
        return record.id

    def timed_call(
        self,
        tool: str,
        api: str,
        endpoint: str = "",
        **kwargs: Any,
    ) -> "_TimedCallContext":
        """Context manager that auto-records latency.

        Usage::

            with api_logger.timed_call("layer3_gemini", "gemini") as ctx:
                response = gemini_client.generate(...)
                ctx.set(cost_usd=0.02, status=200, input_units=5)
        """
        return _TimedCallContext(self, tool, api, endpoint, kwargs)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self._calls), 4)

    def generate_report(self) -> dict[str, Any]:
        """Generate a complete usage report for the pipeline run."""
        if not self._calls:
            return {
                "version": "1.0",
                "pipeline_start": self._start_time,
                "pipeline_end": datetime.now(timezone.utc).isoformat(),
                "summary": {"total_calls": 0, "total_cost_usd": 0.0},
                "by_api": {},
                "by_tool": {},
                "by_stage": {},
                "calls": [],
            }

        # Group by API
        by_api: dict[str, dict[str, Any]] = {}
        for call in self._calls:
            if call.api not in by_api:
                by_api[call.api] = {
                    "calls": 0, "cost_usd": 0.0,
                    "total_latency_ms": 0.0, "errors": 0,
                }
            entry = by_api[call.api]
            entry["calls"] += 1
            entry["cost_usd"] = round(entry["cost_usd"] + call.cost_usd, 4)
            entry["total_latency_ms"] += call.latency_ms
            if call.error or call.status >= 400:
                entry["errors"] += 1

        for entry in by_api.values():
            if entry["calls"] > 0:
                entry["avg_latency_ms"] = round(
                    entry["total_latency_ms"] / entry["calls"]
                )
            del entry["total_latency_ms"]

        # Group by tool
        by_tool: dict[str, dict[str, Any]] = {}
        for call in self._calls:
            if call.tool not in by_tool:
                by_tool[call.tool] = {"calls": 0, "cost_usd": 0.0}
            by_tool[call.tool]["calls"] += 1
            by_tool[call.tool]["cost_usd"] = round(
                by_tool[call.tool]["cost_usd"] + call.cost_usd, 4
            )

        # Group by stage (from metadata.stage if present)
        by_stage: dict[str, dict[str, Any]] = {}
        for call in self._calls:
            stage = call.metadata.get("stage", "unknown")
            if stage not in by_stage:
                by_stage[stage] = {"calls": 0, "cost_usd": 0.0}
            by_stage[stage]["calls"] += 1
            by_stage[stage]["cost_usd"] = round(
                by_stage[stage]["cost_usd"] + call.cost_usd, 4
            )

        total_cost = round(sum(c.cost_usd for c in self._calls), 4)
        error_count = sum(
            1 for c in self._calls if c.error or c.status >= 400
        )

        return {
            "version": "1.0",
            "pipeline_start": self._start_time,
            "pipeline_end": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_calls": len(self._calls),
                "total_cost_usd": total_cost,
                "total_errors": error_count,
                "apis_used": list(by_api.keys()),
            },
            "by_api": by_api,
            "by_tool": by_tool,
            "by_stage": by_stage,
            "calls": [c.to_dict() for c in self._calls],
        }

    def save_report(self, path: str | Path) -> Path:
        """Generate and save the usage report to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = self.generate_report()
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        return path

    def print_summary(self) -> str:
        """Return a human-readable summary string."""
        report = self.generate_report()
        s = report["summary"]
        lines = [
            f"API Usage Report",
            f"  Total calls:  {s['total_calls']}",
            f"  Total cost:   ${s['total_cost_usd']:.4f}",
            f"  Total errors: {s.get('total_errors', 0)}",
            f"",
            f"  By API:",
        ]
        for api, data in report["by_api"].items():
            lines.append(
                f"    {api:20s}  {data['calls']:3d} calls  "
                f"${data['cost_usd']:.4f}  "
                f"avg {data.get('avg_latency_ms', 0)}ms"
            )
        lines.append(f"")
        lines.append(f"  By Tool:")
        for tool, data in report["by_tool"].items():
            lines.append(
                f"    {tool:25s}  {data['calls']:3d} calls  "
                f"${data['cost_usd']:.4f}"
            )
        if report["by_stage"]:
            lines.append(f"")
            lines.append(f"  By Stage:")
            for stage, data in report["by_stage"].items():
                lines.append(
                    f"    {stage:20s}  {data['calls']:3d} calls  "
                    f"${data['cost_usd']:.4f}"
                )
        return "\n".join(lines)


class _TimedCallContext:
    """Context manager for auto-timing API calls."""

    def __init__(
        self,
        logger: APILogger,
        tool: str,
        api: str,
        endpoint: str,
        kwargs: dict[str, Any],
    ):
        self._logger = logger
        self._tool = tool
        self._api = api
        self._endpoint = endpoint
        self._kwargs = kwargs
        self._start: float = 0
        self._extra: dict[str, Any] = {}

    def set(self, **kwargs: Any) -> None:
        """Set additional fields (cost_usd, status, input_units, etc.)."""
        self._extra.update(kwargs)

    def __enter__(self) -> "_TimedCallContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        merged = {**self._kwargs, **self._extra}
        if exc_type is not None:
            merged.setdefault("error", str(exc_val))
            merged.setdefault("status", 500)
        self._logger.log_call(
            tool=self._tool,
            api=self._api,
            endpoint=self._endpoint,
            latency_ms=round(elapsed_ms, 1),
            **merged,
        )


# Module-level singleton — tools import this directly.
api_logger = APILogger()
