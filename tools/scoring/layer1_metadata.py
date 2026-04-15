"""Layer 1 scoring: metadata text matching via sentence-transformers.

Lightweight first-pass filter that compares a scene's visual description
against each candidate clip's source tags using all-MiniLM-L6-v2.
Runs on CPU, requires no GPU or API key — just sentence_transformers.

This is the cheapest gate in the three-layer scoring pipeline. Its job
is to reject obvious mismatches before expensive SigLIP or Gemini calls.
"""
from __future__ import annotations

import time
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


# ---------------------------------------------------------------------------
# Lazy-loaded singleton for the sentence-transformers model
# ---------------------------------------------------------------------------

_MODEL = None
_MODEL_NAME = "all-MiniLM-L6-v2"


def _load_model():
    """Load the sentence-transformers model exactly once per process."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from sentence_transformers import SentenceTransformer  # type: ignore

    _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


class LayerOneMetadata(BaseTool):
    """Score candidates by cosine similarity between source tags and a visual description.

    Inputs
    ------
    candidates : list[dict]
        Each dict must have ``clip_id`` (str) and ``source_tags`` (str).
        May optionally include ``source`` (str) for quality-weight lookup.
    visual_description : str
        The target scene description to match against.
    source_quality_weight : dict[str, float], optional
        Per-source multiplier applied to confidence thresholds.
        Example: ``{"pexels": 1.0, "nara": 0.7}``.
        Sources not listed default to 1.0.

    Returns
    -------
    ToolResult with ``data`` containing:
        scored : list[dict]
            Each entry has ``clip_id``, ``score``, ``category``
            ("high", "medium", "discard"), and ``adjusted_score``.
        high / medium / discard : list[str]
            Clip IDs grouped by category for quick filtering.
    """

    name = "layer1_metadata"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_scoring"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:sentence_transformers"]
    install_instructions = (
        "Install sentence-transformers:\n"
        "  pip install sentence-transformers\n"
        "The all-MiniLM-L6-v2 model (~80 MB) downloads on first use."
    )

    capabilities = ["footage_scoring", "text_matching"]
    best_for = [
        "fast first-pass clip filtering",
        "metadata-level relevance screening",
    ]
    not_good_for = [
        "visual similarity (use layer2_siglip)",
        "subjective quality judgment (use layer3_gemini)",
    ]

    input_schema = {
        "type": "object",
        "required": ["candidates", "visual_description"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["clip_id", "source_tags"],
                    "properties": {
                        "clip_id": {"type": "string"},
                        "source_tags": {"type": "string"},
                        "source": {"type": "string"},
                    },
                },
            },
            "visual_description": {"type": "string"},
            "source_quality_weight": {
                "type": "object",
                "additionalProperties": {"type": "number"},
                "description": "Per-source weight that adjusts confidence thresholds.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=False
    )

    # Threshold constants
    HIGH_THRESHOLD = 0.80
    MEDIUM_THRESHOLD = 0.45

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Local-only model, no monetary cost."""
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Rough estimate: ~50ms per candidate on CPU after model warm-up."""
        n = len(inputs.get("candidates", []))
        return max(1.0, n * 0.05)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Score each candidate's source_tags against the visual description."""
        start = time.time()

        # ---- Validate inputs ------------------------------------------------
        candidates = inputs.get("candidates")
        visual_description = inputs.get("visual_description")

        if not candidates:
            return ToolResult(
                success=False,
                error="No candidates provided.",
            )
        if not visual_description or not visual_description.strip():
            return ToolResult(
                success=False,
                error="visual_description is required and must be non-empty.",
            )

        source_quality_weight: dict[str, float] = inputs.get(
            "source_quality_weight", {}
        )

        # ---- Load model & encode --------------------------------------------
        try:
            model = _load_model()
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to load sentence-transformers model: {exc}",
            )

        try:
            tag_texts = [c.get("source_tags", "") or "" for c in candidates]
            # Encode the visual description and all tag texts in a single batch
            all_texts = [visual_description] + tag_texts
            embeddings = model.encode(all_texts, normalize_embeddings=True)

            query_vec = embeddings[0]  # (dim,)
            tag_vecs = embeddings[1:]  # (N, dim)

            # Cosine similarity (vectors are already L2-normalised)
            similarities = tag_vecs @ query_vec  # (N,)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Encoding or similarity computation failed: {exc}",
            )

        # ---- Categorise ------------------------------------------------------
        scored: list[dict[str, Any]] = []
        high_ids: list[str] = []
        medium_ids: list[str] = []
        discard_ids: list[str] = []

        for i, candidate in enumerate(candidates):
            clip_id: str = candidate["clip_id"]
            raw_score = float(similarities[i])
            source_name = candidate.get("source", "").lower()
            weight = source_quality_weight.get(source_name, 1.0)

            # The weight adjusts confidence thresholds, not the score itself.
            # A weight < 1.0 means lower-quality source, so thresholds are
            # effectively raised (harder to qualify).
            adj_high = self.HIGH_THRESHOLD / max(weight, 0.01)
            adj_medium = self.MEDIUM_THRESHOLD / max(weight, 0.01)

            if raw_score >= adj_high:
                category = "high"
                high_ids.append(clip_id)
            elif raw_score >= adj_medium:
                category = "medium"
                medium_ids.append(clip_id)
            else:
                category = "discard"
                discard_ids.append(clip_id)

            scored.append({
                "clip_id": clip_id,
                "score": round(raw_score, 4),
                "adjusted_thresholds": {
                    "high": round(adj_high, 4),
                    "medium": round(adj_medium, 4),
                },
                "source_weight": weight,
                "category": category,
            })

        # Sort by score descending for convenience
        scored.sort(key=lambda x: x["score"], reverse=True)

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "scored": scored,
                "high": high_ids,
                "medium": medium_ids,
                "discard": discard_ids,
                "model": _MODEL_NAME,
                "candidates_total": len(candidates),
                "candidates_passed": len(high_ids) + len(medium_ids),
            },
            duration_seconds=elapsed,
        )
