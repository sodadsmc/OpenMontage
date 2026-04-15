"""Decision engine: final clip selection across scenes.

Two-pass approach:
  Pass 1 — Score each candidate per scene ignoring cross-scene color coherence.
  Pass 2 — Sequential re-ranking considering color flow between adjacent scenes.

Hard filters eliminate clips that fail minimum requirements before scoring.
The engine also enforces uniqueness: no clip appears in two scenes.
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
# Source priority defaults (imported from source_priority.py at call time,
# but also declared here as a fallback constant)
# ---------------------------------------------------------------------------
_DEFAULT_SOURCE_MULTIPLIERS: dict[str, float] = {
    "csb": 1.15,
    "ntsb": 1.15,
    "nara": 1.15,
    "nasa": 1.15,
    "archive_tv_news": 1.10,
}


def _get_source_multiplier(source: str) -> float:
    """Return the priority multiplier for a source name."""
    return _DEFAULT_SOURCE_MULTIPLIERS.get(source.lower(), 1.0)


def _duration_fit(clip_duration: float, preferred_duration: float) -> float:
    """Score how well a clip's duration matches the preferred duration.

    Peaks at 1.0 when exact match, decays linearly to 0.0 at twice the
    distance or zero duration. Never goes below 0.0.
    """
    if preferred_duration <= 0 or clip_duration <= 0:
        return 0.0
    diff = abs(clip_duration - preferred_duration)
    # Full decay when diff equals preferred_duration
    score = max(0.0, 1.0 - diff / preferred_duration)
    return score


def _color_distance(colors_a: list[list[int]], colors_b: list[list[int]]) -> float:
    """Compute average Euclidean distance between two dominant-color palettes.

    Each palette is a list of [R, G, B] triplets. Returns a normalised
    distance in [0, 1] where 0 = identical palettes, 1 = maximally different.
    """
    if not colors_a or not colors_b:
        return 0.5  # neutral when color data is missing

    a = np.array(colors_a, dtype=np.float32)
    b = np.array(colors_b, dtype=np.float32)

    # Use the shorter palette length for comparison
    n = min(len(a), len(b))
    a = a[:n]
    b = b[:n]

    # Euclidean distance per pair, normalised by max possible (sqrt(3*255^2) ~ 441.7)
    dists = np.linalg.norm(a - b, axis=1) / 441.7
    return float(np.mean(dists))


def _color_coherence_score(
    prev_colors: list[list[int]],
    curr_colors: list[list[int]],
) -> float:
    """Score cross-scene color coherence. 1.0 = very similar, 0.0 = very different."""
    dist = _color_distance(prev_colors, curr_colors)
    return 1.0 - dist


class DecisionEngine(BaseTool):
    """Select the best clip for each scene from scored candidates.

    Inputs
    ------
    scenes : list[dict]
        Each scene dict must have:
        - ``scene_id`` (str)
        - ``preferred_duration`` (float, seconds)
        - ``candidates`` (list[dict]), each candidate having:
            - ``clip_id`` (str)
            - ``source`` (str)
            - ``duration`` (float)
            - ``visual_score`` (float, from Layer 2)
            - ``tonal_score`` (float, from Layer 3 or default 0.5)
            - ``motion_score`` (float, 0-1)
            - ``dominant_colors`` (list[list[int]], optional)
            - ``has_shot_change`` (bool, optional, default False)
            - ``min_duration_ok`` (bool, optional, default True)
            - ``quality_ok`` (bool, optional, default True)
    used_clip_ids : list[str]
        Clip IDs already used in other videos (for dedup).

    Returns
    -------
    ToolResult with ``data`` containing:
        selections : list[dict]
            One entry per scene with ``scene_id``, ``selected_clip_id``,
            ``final_score``, and ``score_breakdown``.
        unresolved_scenes : list[str]
            Scene IDs where no candidate survived hard filters.
    """

    name = "decision_engine"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_scoring"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    capabilities = ["footage_scoring", "clip_selection"]
    best_for = [
        "final clip selection across a sequence of scenes",
        "cross-scene color coherence optimization",
    ]
    not_good_for = [
        "individual clip scoring (use layer1/layer2/layer3)",
    ]

    input_schema = {
        "type": "object",
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["scene_id", "preferred_duration", "candidates"],
                },
            },
            "used_clip_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Clip IDs already used in other videos.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=0, network_required=False
    )

    # ------------------------------------------------------------------
    # Scoring weights (Pass 1)
    # ------------------------------------------------------------------
    W_VISUAL = 0.40
    W_TONAL = 0.25
    W_DURATION = 0.15
    W_MOTION = 0.10
    W_SOURCE = 0.05
    W_COLOR = 0.05

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Run two-pass selection across all scenes."""
        start = time.time()

        scenes = inputs.get("scenes")
        if not scenes:
            return ToolResult(success=False, error="No scenes provided.")

        used_clip_ids: set[str] = set(inputs.get("used_clip_ids", []))

        # ==================================================================
        # Pass 1: Score each candidate per scene independently
        # ==================================================================
        scene_rankings: list[dict[str, Any]] = []
        unresolved: list[str] = []

        for scene in scenes:
            scene_id = scene["scene_id"]
            preferred_duration = float(scene.get("preferred_duration", 5.0))
            candidates = scene.get("candidates", [])

            # Apply hard filters
            filtered: list[dict[str, Any]] = []
            for c in candidates:
                clip_id = c["clip_id"]
                # Hard filter: already used
                if clip_id in used_clip_ids:
                    continue
                # Hard filter: minimum duration
                if not c.get("min_duration_ok", True):
                    continue
                # Hard filter: quality threshold
                if not c.get("quality_ok", True):
                    continue
                # Hard filter: internal shot changes
                if c.get("has_shot_change", False):
                    continue
                filtered.append(c)

            if not filtered:
                unresolved.append(scene_id)
                scene_rankings.append({
                    "scene_id": scene_id,
                    "candidates_scored": [],
                })
                continue

            # Score each surviving candidate
            scored: list[dict[str, Any]] = []
            for c in filtered:
                visual = float(c.get("visual_score", 0.0))
                tonal = float(c.get("tonal_score", 0.5))
                duration = float(c.get("duration", 0.0))
                motion = float(c.get("motion_score", 0.0))
                source = str(c.get("source", "")).lower()
                # Normalise source priority to 0-1 range: (multiplier - 1.0) / 0.15
                source_mult = _get_source_multiplier(source)
                source_score = min(1.0, (source_mult - 1.0) / 0.15)
                dur_fit = _duration_fit(duration, preferred_duration)

                base = (
                    self.W_VISUAL * visual
                    + self.W_TONAL * tonal
                    + self.W_DURATION * dur_fit
                    + self.W_MOTION * motion
                    + self.W_SOURCE * source_score
                    + self.W_COLOR * 0.5  # neutral in pass 1
                )
                final = base * source_mult

                scored.append({
                    "clip_id": c["clip_id"],
                    "source": source,
                    "final_score": round(final, 4),
                    "score_breakdown": {
                        "visual": round(visual, 4),
                        "tonal": round(tonal, 4),
                        "duration_fit": round(dur_fit, 4),
                        "motion": round(motion, 4),
                        "source_priority": round(source_score, 4),
                        "color_coherence": 0.5,
                        "source_multiplier": source_mult,
                    },
                    "dominant_colors": c.get("dominant_colors", []),
                })

            scored.sort(key=lambda x: x["final_score"], reverse=True)
            scene_rankings.append({
                "scene_id": scene_id,
                "candidates_scored": scored,
            })

        # ==================================================================
        # Pass 2: Sequential re-ranking with color flow
        # ==================================================================
        selections: list[dict[str, Any]] = []
        assigned_clip_ids: set[str] = set(used_clip_ids)
        prev_colors: list[list[int]] = []

        for ranking in scene_rankings:
            scene_id = ranking["scene_id"]
            candidates_scored = ranking["candidates_scored"]

            if not candidates_scored:
                selections.append({
                    "scene_id": scene_id,
                    "selected_clip_id": None,
                    "final_score": 0.0,
                    "score_breakdown": {},
                    "reason": "No candidates survived hard filters.",
                })
                continue

            # Re-score with color coherence if we have a previous scene
            best: Optional[dict[str, Any]] = None
            best_final: float = -1.0

            for c in candidates_scored:
                if c["clip_id"] in assigned_clip_ids:
                    continue

                breakdown = dict(c["score_breakdown"])
                curr_colors = c.get("dominant_colors", [])

                if prev_colors:
                    color_coh = _color_coherence_score(prev_colors, curr_colors)
                else:
                    color_coh = 0.5  # neutral for first scene

                breakdown["color_coherence"] = round(color_coh, 4)

                # Recompute final score with real color coherence
                base = (
                    self.W_VISUAL * breakdown["visual"]
                    + self.W_TONAL * breakdown["tonal"]
                    + self.W_DURATION * breakdown["duration_fit"]
                    + self.W_MOTION * breakdown["motion"]
                    + self.W_SOURCE * breakdown["source_priority"]
                    + self.W_COLOR * color_coh
                )
                final = base * breakdown["source_multiplier"]

                if final > best_final:
                    best_final = final
                    best = {
                        "scene_id": scene_id,
                        "selected_clip_id": c["clip_id"],
                        "final_score": round(final, 4),
                        "score_breakdown": breakdown,
                    }
                    # Track colors for next scene
                    _best_colors = curr_colors

            if best is not None:
                selections.append(best)
                assigned_clip_ids.add(best["selected_clip_id"])
                prev_colors = _best_colors  # type: ignore[possibly-undefined]
            else:
                # All candidates already assigned to earlier scenes
                selections.append({
                    "scene_id": scene_id,
                    "selected_clip_id": None,
                    "final_score": 0.0,
                    "score_breakdown": {},
                    "reason": "All candidates were already assigned to other scenes.",
                })
                if scene_id not in unresolved:
                    unresolved.append(scene_id)

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "selections": selections,
                "unresolved_scenes": unresolved,
                "scenes_total": len(scenes),
                "scenes_resolved": len(scenes) - len(unresolved),
            },
            duration_seconds=elapsed,
        )
