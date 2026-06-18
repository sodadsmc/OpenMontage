"""Pacing-aware assembly engine.

Takes scenes with selected clips and sentence boundaries, applies
pacing rules from a JSON config, and produces an assembly manifest
compatible with Remotion Explainer composition props and the
edit_decisions schema.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)

# Default pacing config (used if config file is missing)
_DEFAULT_PACING: dict[str, Any] = {
    "establishing": {
        "pre_roll_seconds": 0.5,
        "post_roll_seconds": 0.3,
        "animation": "ken-burns",
        "clip_count": 1,
    },
    "escalation": {
        "pre_roll_seconds": 0.3,
        "post_roll_seconds": 0.2,
        "animation": "zoom-in",
        "speed_factors": [1.0, 0.95, 0.90],
        "cuts_per_sentence": True,
    },
    "crisis": {
        "pre_roll_seconds": 0.1,
        "post_roll_seconds": 0.1,
        "animation": "zoom-in",
        "max_cut_interval_seconds": 3.0,
        "mid_sentence_cuts": True,
    },
    "resolution": {
        "pre_roll_seconds": 0.8,
        "post_roll_seconds": 0.5,
        "animation": "static",
        "clip_count": 1,
    },
    "speed_ramp_minimum": 0.85,
    "freeze_drift_max_seconds": 3.0,
    "l_cut_default_carry_seconds": 1.2,
    "l_cut_volume": 0.6,
    "default_clip_volume": 0,
    "blur_pad_blur_radius": 20,
    "blur_pad_scale": 1.3,
}

# Aspect ratio thresholds
_AR_16_9 = 16 / 9  # ~1.778


class PacingEngine(BaseTool):
    """Generates pacing-aware assembly decisions for video production."""

    name = "pacing_engine"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "assembly"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = []
    install_instructions = "No external dependencies required."

    capabilities = ["assembly", "pacing"]
    supports = {
        "pacing_types": ["establishing", "escalation", "crisis", "resolution"],
        "framing_strategies": ["direct", "blur-pad", "center-crop", "slight-crop"],
        "l_j_cuts": True,
        "speed_ramping": True,
        "freeze_drift": True,
    }
    best_for = [
        "generating assembly manifests from scene plans with clip selections",
        "pacing-aware cut decisions for documentary production",
    ]
    not_good_for = [
        "final rendering (use video_compose instead)",
        "clip selection (use clip_search or direct_clip_search instead)",
    ]

    input_schema = {
        "type": "object",
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "description": (
                    "Scenes with selected clips, sentence boundaries, and "
                    "pacing type. Each scene needs: scene_id, pacing, clips "
                    "(list with path, width, height, duration), "
                    "sentence_boundaries (list with start_seconds, end_seconds), "
                    "duration_seconds."
                ),
                "items": {
                    "type": "object",
                    "required": ["scene_id", "pacing"],
                    "properties": {
                        "scene_id": {"type": "string"},
                        "pacing": {
                            "type": "string",
                            "enum": [
                                "establishing",
                                "escalation",
                                "crisis",
                                "resolution",
                            ],
                        },
                        "clips": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "width": {"type": "integer"},
                                    "height": {"type": "integer"},
                                    "duration": {"type": "number"},
                                },
                            },
                        },
                        "sentence_boundaries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start_seconds": {"type": "number"},
                                    "end_seconds": {"type": "number"},
                                },
                            },
                        },
                        "duration_seconds": {"type": "number"},
                    },
                },
            },
            "pacing_config_path": {
                "type": "string",
                "default": "config/pacing_config.json",
                "description": "Path to pacing config JSON file",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10
    )
    idempotency_key_fields = ["scenes"]
    side_effects = []
    user_visible_verification = [
        "Review assembly manifest for timing accuracy and pacing feel",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Generate pacing-aware assembly decisions."""
        scenes = inputs.get("scenes", [])
        if not scenes:
            return ToolResult(success=False, error="No scenes provided")

        config_path = Path(
            inputs.get("pacing_config_path", "config/pacing_config.json")
        )

        start = time.time()

        # Load pacing config
        config = self._load_config(config_path)

        try:
            manifest = self._build_manifest(scenes, config)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Assembly manifest generation failed: {exc}",
            )

        elapsed = round(time.time() - start, 2)

        return ToolResult(
            success=True,
            data={
                "assembly_manifest": manifest,
                "total_cuts": len(manifest.get("cuts", [])),
                "total_duration_seconds": manifest.get("total_duration_seconds", 0),
                "l_j_cuts": manifest.get("l_j_cuts", []),
                "warnings": manifest.get("warnings", []),
            },
            duration_seconds=elapsed,
        )

    def _load_config(self, config_path: Path) -> dict[str, Any]:
        """Load pacing config from JSON file, falling back to defaults."""
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return dict(_DEFAULT_PACING)

    def _build_manifest(
        self, scenes: list[dict[str, Any]], config: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the full assembly manifest from scenes and config."""
        cuts: list[dict[str, Any]] = []
        l_j_cuts: list[dict[str, Any]] = []
        warnings: list[str] = []
        timeline_cursor = 0.0
        cut_index = 0
        default_volume = config.get("default_clip_volume", 0)

        for scene in scenes:
            scene_id = scene.get("scene_id", f"scene_{len(cuts)}")
            pacing = scene.get("pacing", "establishing")
            pacing_config = config.get(pacing, _DEFAULT_PACING.get(pacing, {}))
            clips = scene.get("clips", [])
            boundaries = scene.get("sentence_boundaries", [])
            scene_duration = scene.get("duration_seconds", 10.0)

            if not clips:
                warnings.append(
                    f"{scene_id}: no retrieved clips — routed to AI generation."
                )
                # Emit a placeholder cut routed to AI generation. A scene with no
                # retrieved clips is an AI-generation job (the primary path), not a
                # retrieval 'gap' — the source token is kept for consumer compat.
                cuts.append({
                    "id": f"cut_{cut_index:03d}",
                    "source": "__gap_fill__",
                    "in_seconds": 0,
                    "out_seconds": scene_duration,
                    "speed": 1.0,
                    "layer": "primary",
                    "transform": {
                        "animation": pacing_config.get("animation", "static"),
                    },
                    "reason": f"{scene_id}: no retrieved clips — route to AI generation",
                    "_scene_id": scene_id,
                    "_pacing": pacing,
                    "_timeline_start": timeline_cursor,
                })
                timeline_cursor += scene_duration
                cut_index += 1
                continue

            # Generate cuts based on pacing type
            if pacing == "establishing":
                scene_cuts = self._assemble_establishing(
                    clips, pacing_config, scene_duration, cut_index, scene_id,
                    default_volume, config
                )
            elif pacing == "escalation":
                scene_cuts = self._assemble_escalation(
                    clips, boundaries, pacing_config, scene_duration,
                    cut_index, scene_id, default_volume, config
                )
            elif pacing == "crisis":
                scene_cuts = self._assemble_crisis(
                    clips, pacing_config, scene_duration, cut_index, scene_id,
                    default_volume, config
                )
            elif pacing == "resolution":
                scene_cuts = self._assemble_resolution(
                    clips, pacing_config, scene_duration, cut_index, scene_id,
                    default_volume, config
                )
            else:
                scene_cuts = self._assemble_establishing(
                    clips, pacing_config, scene_duration, cut_index, scene_id,
                    default_volume, config
                )

            # Apply "too short" fallback chain
            scene_cuts, scene_warnings = self._apply_fallback_chain(
                scene_cuts, scene_duration, scene_id, config
            )
            warnings.extend(scene_warnings)

            # Set timeline positions
            for cut in scene_cuts:
                cut["_timeline_start"] = timeline_cursor
                effective_duration = (
                    (cut["out_seconds"] - cut["in_seconds"]) / cut.get("speed", 1.0)
                )
                timeline_cursor += effective_duration

            cuts.extend(scene_cuts)
            cut_index += len(scene_cuts)

        # Generate L/J-cut decisions for the hardest transitions (3-4 max)
        l_j_cuts = self._generate_lj_cuts(cuts, config)

        manifest = {
            "version": "1.0",
            "cuts": cuts,
            "l_j_cuts": l_j_cuts,
            "total_duration_seconds": round(timeline_cursor, 2),
            "warnings": warnings,
            "renderer_family": "documentary-montage",
        }

        return manifest

    def _determine_framing(
        self,
        clip: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Determine framing strategy from clip aspect ratio."""
        width = clip.get("width", 1920)
        height = clip.get("height", 1080)

        if height == 0:
            return {"position": "center"}

        ar = width / height
        blur_radius = config.get("blur_pad_blur_radius", 20)
        blur_scale = config.get("blur_pad_scale", 1.3)

        if abs(ar - _AR_16_9) < 0.05:
            # 16:9 — direct, no transformation needed
            return {"position": "center"}
        elif abs(ar - 4 / 3) < 0.1:
            # 4:3 — blur-pad
            return {
                "position": "center",
                "scale": blur_scale,
                "_framing": "blur-pad",
                "_blur_radius": blur_radius,
            }
        elif ar < 1.0:
            # Vertical — center-crop + ken burns
            return {
                "position": "center",
                "animation": "ken-burns",
                "_framing": "center-crop",
                "crop": {
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": int(width / _AR_16_9),
                },
            }
        else:
            # Near-16:9 — slight crop
            target_height = int(width / _AR_16_9)
            crop_y = max(0, (height - target_height) // 2)
            return {
                "position": "center",
                "_framing": "slight-crop",
                "crop": {
                    "x": 0,
                    "y": crop_y,
                    "width": width,
                    "height": target_height,
                },
            }

    def _make_cut(
        self,
        clip: dict[str, Any],
        cut_index: int,
        scene_id: str,
        pacing: str,
        in_seconds: float,
        out_seconds: float,
        speed: float,
        animation: str,
        volume: int | float,
        config: dict[str, Any],
        pre_roll: float = 0.0,
        post_roll: float = 0.0,
    ) -> dict[str, Any]:
        """Create a single cut entry."""
        transform = self._determine_framing(clip, config)
        transform["animation"] = animation

        return {
            "id": f"cut_{cut_index:03d}",
            "source": clip.get("path", ""),
            "in_seconds": round(max(0, in_seconds - pre_roll), 3),
            "out_seconds": round(out_seconds + post_roll, 3),
            "speed": speed,
            "layer": "primary",
            "transform": transform,
            "reason": f"{scene_id} [{pacing}]",
            "_scene_id": scene_id,
            "_pacing": pacing,
            "_volume": volume,
        }

    def _assemble_establishing(
        self,
        clips: list[dict[str, Any]],
        pacing_config: dict[str, Any],
        scene_duration: float,
        cut_index: int,
        scene_id: str,
        default_volume: int | float,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Establishing: single clip, Ken Burns, gentle pre/post roll."""
        clip = clips[0]
        pre_roll = pacing_config.get("pre_roll_seconds", 0.5)
        post_roll = pacing_config.get("post_roll_seconds", 0.3)
        animation = pacing_config.get("animation", "ken-burns")

        return [
            self._make_cut(
                clip, cut_index, scene_id, "establishing",
                in_seconds=0,
                out_seconds=scene_duration,
                speed=1.0,
                animation=animation,
                volume=default_volume,
                config=config,
                pre_roll=pre_roll,
                post_roll=post_roll,
            )
        ]

    def _assemble_escalation(
        self,
        clips: list[dict[str, Any]],
        boundaries: list[dict[str, Any]],
        pacing_config: dict[str, Any],
        scene_duration: float,
        cut_index: int,
        scene_id: str,
        default_volume: int | float,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Escalation: cuts at sentence boundaries, accelerating speed."""
        pre_roll = pacing_config.get("pre_roll_seconds", 0.3)
        post_roll = pacing_config.get("post_roll_seconds", 0.2)
        animation = pacing_config.get("animation", "zoom-in")
        speed_factors = pacing_config.get("speed_factors", [1.0, 0.95, 0.90])

        cuts: list[dict[str, Any]] = []

        if boundaries:
            # Cut at each sentence boundary
            for i, boundary in enumerate(boundaries):
                clip = clips[i % len(clips)]
                speed = speed_factors[min(i, len(speed_factors) - 1)]

                cuts.append(
                    self._make_cut(
                        clip, cut_index + i, scene_id, "escalation",
                        in_seconds=boundary.get("start_seconds", 0),
                        out_seconds=boundary.get("end_seconds", scene_duration),
                        speed=speed,
                        animation=animation,
                        volume=default_volume,
                        config=config,
                        pre_roll=pre_roll if i == 0 else 0,
                        post_roll=post_roll if i == len(boundaries) - 1 else 0,
                    )
                )
        else:
            # No boundaries — split scene evenly across available clips
            clip_count = min(len(clips), 3)
            segment_duration = scene_duration / clip_count
            for i in range(clip_count):
                clip = clips[i]
                speed = speed_factors[min(i, len(speed_factors) - 1)]
                seg_start = i * segment_duration
                seg_end = (i + 1) * segment_duration

                cuts.append(
                    self._make_cut(
                        clip, cut_index + i, scene_id, "escalation",
                        in_seconds=seg_start,
                        out_seconds=seg_end,
                        speed=speed,
                        animation=animation,
                        volume=default_volume,
                        config=config,
                        pre_roll=pre_roll if i == 0 else 0,
                        post_roll=post_roll if i == clip_count - 1 else 0,
                    )
                )

        return cuts

    def _assemble_crisis(
        self,
        clips: list[dict[str, Any]],
        pacing_config: dict[str, Any],
        scene_duration: float,
        cut_index: int,
        scene_id: str,
        default_volume: int | float,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Crisis: rapid cuts at 2-3s intervals, minimal pre/post roll."""
        pre_roll = pacing_config.get("pre_roll_seconds", 0.1)
        post_roll = pacing_config.get("post_roll_seconds", 0.1)
        animation = pacing_config.get("animation", "zoom-in")
        max_interval = pacing_config.get("max_cut_interval_seconds", 3.0)

        cuts: list[dict[str, Any]] = []
        cursor = 0.0
        i = 0

        while cursor < scene_duration:
            clip = clips[i % len(clips)]
            seg_end = min(cursor + max_interval, scene_duration)

            cuts.append(
                self._make_cut(
                    clip, cut_index + i, scene_id, "crisis",
                    in_seconds=cursor,
                    out_seconds=seg_end,
                    speed=1.0,
                    animation=animation,
                    volume=default_volume,
                    config=config,
                    pre_roll=pre_roll if i == 0 else 0,
                    post_roll=post_roll if seg_end >= scene_duration else 0,
                )
            )

            cursor = seg_end
            i += 1

        return cuts

    def _assemble_resolution(
        self,
        clips: list[dict[str, Any]],
        pacing_config: dict[str, Any],
        scene_duration: float,
        cut_index: int,
        scene_id: str,
        default_volume: int | float,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Resolution: single clip, long pre/post roll, static."""
        clip = clips[0]
        pre_roll = pacing_config.get("pre_roll_seconds", 0.8)
        post_roll = pacing_config.get("post_roll_seconds", 0.5)
        animation = pacing_config.get("animation", "static")

        return [
            self._make_cut(
                clip, cut_index, scene_id, "resolution",
                in_seconds=0,
                out_seconds=scene_duration,
                speed=1.0,
                animation=animation,
                volume=default_volume,
                config=config,
                pre_roll=pre_roll,
                post_roll=post_roll,
            )
        ]

    def _apply_fallback_chain(
        self,
        cuts: list[dict[str, Any]],
        scene_duration: float,
        scene_id: str,
        config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply 'too short' fallback chain when clips are shorter than needed.

        Chain: concat 2 clips -> speed ramp 0.85x -> freeze-drift 3s -> flag for AI gap-fill
        """
        warnings: list[str] = []
        speed_ramp_min = config.get("speed_ramp_minimum", 0.85)
        freeze_max = config.get("freeze_drift_max_seconds", 3.0)

        for cut in cuts:
            clip_duration = cut["out_seconds"] - cut["in_seconds"]
            effective_duration = clip_duration / cut.get("speed", 1.0)
            needed_duration = scene_duration / max(len(cuts), 1)

            if effective_duration >= needed_duration:
                continue

            gap = needed_duration - effective_duration

            # Step 1: Speed ramp to stretch
            if gap <= effective_duration * (1 / speed_ramp_min - 1):
                new_speed = max(
                    speed_ramp_min,
                    clip_duration / needed_duration,
                )
                cut["speed"] = round(new_speed, 3)
                warnings.append(
                    f"{scene_id}/{cut['id']}: speed-ramped to {new_speed:.2f}x "
                    f"to fill {gap:.1f}s gap"
                )
                continue

            # Step 2: Freeze-drift
            remaining_gap = gap - effective_duration * (1 / speed_ramp_min - 1)
            if remaining_gap <= freeze_max:
                cut["speed"] = speed_ramp_min
                cut["_freeze_drift_seconds"] = round(remaining_gap, 2)
                warnings.append(
                    f"{scene_id}/{cut['id']}: speed-ramp + freeze-drift "
                    f"{remaining_gap:.1f}s to fill gap"
                )
                continue

            # Step 3: Flag for AI gap-fill
            cut["_needs_gap_fill"] = True
            cut["_gap_seconds"] = round(gap, 2)
            warnings.append(
                f"{scene_id}/{cut['id']}: clip too short by {gap:.1f}s — "
                f"flagged for AI gap-fill"
            )

        return cuts, warnings

    def _generate_lj_cuts(
        self,
        cuts: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate L/J-cut decisions for the hardest transitions.

        Selects 3-4 transitions between scenes with different pacing types
        and adds ambient carry-over decisions.
        """
        carry_seconds = config.get("l_cut_default_carry_seconds", 1.2)
        l_cut_volume = config.get("l_cut_volume", 0.6)
        lj_cuts: list[dict[str, Any]] = []

        for i in range(len(cuts) - 1):
            from_cut = cuts[i]
            to_cut = cuts[i + 1]

            # L/J-cuts are most impactful at pacing transitions
            from_pacing = from_cut.get("_pacing", "")
            to_pacing = to_cut.get("_pacing", "")

            if from_pacing != to_pacing:
                lj_cuts.append({
                    "from_cut": from_cut["id"],
                    "to_cut": to_cut["id"],
                    "carry_seconds": carry_seconds,
                    "channel": "ambient",
                    "volume": l_cut_volume,
                    "reason": f"Pacing transition: {from_pacing} -> {to_pacing}",
                })

            if len(lj_cuts) >= 4:
                break

        return lj_cuts
