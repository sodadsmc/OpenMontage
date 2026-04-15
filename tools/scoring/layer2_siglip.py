"""Layer 2 scoring: SigLIP 2 visual matching.

Extracts frames from candidate clips via ffmpeg and scores them against
a visual description using the shared SigLIP 2 embedder (1152-d vectors).

This is the core visual-relevance gate. It looks at what the clip
actually SHOWS, not just what its metadata says. Runs locally but
requires torch + ffmpeg.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

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


# Frame extraction positions as fractions of clip duration
_FRAME_POSITIONS = [0.10, 0.25, 0.50, 0.75, 0.90]


def _extract_frames(video_path: str, output_dir: Path) -> list[Path]:
    """Extract 5 frames from a video at 10%, 25%, 50%, 75%, 90% positions.

    Uses ffprobe to get duration, then ffmpeg to seek and extract each frame.
    Returns a list of paths to the extracted JPEG frames.
    """
    video_path_str = str(video_path)

    # Get duration via ffprobe
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path_str,
    ]
    try:
        result = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=30
        )
        duration = float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise RuntimeError(f"Failed to probe video duration: {exc}") from exc

    if duration <= 0:
        raise RuntimeError(f"Video has invalid duration: {duration}")

    frame_paths: list[Path] = []
    for i, frac in enumerate(_FRAME_POSITIONS):
        timestamp = duration * frac
        out_path = output_dir / f"frame_{i:02d}.jpg"
        extract_cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{timestamp:.3f}",
            "-i", video_path_str,
            "-frames:v", "1",
            "-q:v", "2",
            str(out_path),
        ]
        try:
            subprocess.run(
                extract_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ffmpeg frame extraction failed at {timestamp:.1f}s: {exc.stderr}"
            ) from exc

        if out_path.is_file():
            frame_paths.append(out_path)

    return frame_paths


class LayerTwoSigLIP(BaseTool):
    """Score candidates by SigLIP 2 visual similarity between frames and description.

    Inputs
    ------
    candidates : list[dict]
        Each dict must have ``clip_id`` (str) and ``path`` (str, path to video file).
    visual_description : str
        The target scene description to match visually.

    Returns
    -------
    ToolResult with ``data`` containing:
        scored : list[dict]
            Each entry has ``clip_id``, ``composite_score``, ``max_score``,
            ``top3_mean``, ``overall_mean``, ``per_frame_scores``,
            ``best_frame_index``, and ``category``.
        strong / moderate / discard : list[str]
            Clip IDs grouped by category.
    """

    name = "layer2_siglip"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_scoring"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:torch", "cmd:ffmpeg"]
    install_instructions = (
        "Requires PyTorch and ffmpeg:\n"
        "  pip install torch transformers\n"
        "  ffmpeg must be on PATH (https://ffmpeg.org/download.html)"
    )

    capabilities = ["footage_scoring", "visual_matching"]
    best_for = [
        "visual relevance scoring",
        "frame-level clip analysis",
        "trim point estimation",
    ]
    not_good_for = [
        "metadata-only filtering (use layer1_metadata)",
        "subjective tonal judgment (use layer3_gemini)",
    ]

    input_schema = {
        "type": "object",
        "required": ["candidates", "visual_description"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["clip_id", "path"],
                    "properties": {
                        "clip_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
            },
            "visual_description": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=2048, vram_mb=0, disk_mb=500, network_required=False
    )

    # Category thresholds
    STRONG_THRESHOLD = 0.75
    MODERATE_THRESHOLD = 0.55

    # Composite score weights
    WEIGHT_MAX = 0.4
    WEIGHT_TOP3 = 0.4
    WEIGHT_MEAN = 0.2

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Local-only, no monetary cost."""
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Rough estimate: ~5s per candidate (frame extraction + SigLIP encoding)."""
        n = len(inputs.get("candidates", []))
        return max(2.0, n * 5.0)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Extract frames, embed via SigLIP 2, and score against visual description."""
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

        # Check ffmpeg availability
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            return ToolResult(
                success=False,
                error="ffmpeg/ffprobe not found on PATH. " + self.install_instructions,
            )

        # ---- Import embedder lazily -----------------------------------------
        try:
            from lib.clip_embedder import embed_images, embed_texts
        except ImportError as exc:
            return ToolResult(
                success=False,
                error=f"Failed to import clip_embedder: {exc}",
            )

        # ---- Encode the visual description once -----------------------------
        try:
            text_embedding = embed_texts([visual_description])  # (1, 1152)
            text_vec = text_embedding[0]  # (1152,)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to encode visual description: {exc}",
            )

        # ---- Score each candidate -------------------------------------------
        scored: list[dict[str, Any]] = []
        strong_ids: list[str] = []
        moderate_ids: list[str] = []
        discard_ids: list[str] = []
        errors: list[dict[str, str]] = []

        for candidate in candidates:
            clip_id: str = candidate["clip_id"]
            video_path: str = candidate["path"]

            if not Path(video_path).is_file():
                errors.append({
                    "clip_id": clip_id,
                    "error": f"Video file not found: {video_path}",
                })
                continue

            # Extract frames to a temporary directory
            tmp_dir = None
            try:
                tmp_dir = Path(tempfile.mkdtemp(prefix="siglip_frames_"))
                frame_paths = _extract_frames(video_path, tmp_dir)

                if not frame_paths:
                    errors.append({
                        "clip_id": clip_id,
                        "error": "No frames extracted from video.",
                    })
                    continue

                # Embed all frames in a single batch
                frame_embeddings = embed_images(frame_paths)  # (K, 1152)

                # Cosine similarity: frames are L2-normalised, text vec is L2-normalised
                frame_scores = (frame_embeddings @ text_vec).tolist()  # list of floats

                # Composite score
                sorted_scores = sorted(frame_scores, reverse=True)
                max_score = sorted_scores[0]
                top3_mean = float(np.mean(sorted_scores[:3])) if len(sorted_scores) >= 3 else float(np.mean(sorted_scores))
                overall_mean = float(np.mean(frame_scores))

                composite = (
                    self.WEIGHT_MAX * max_score
                    + self.WEIGHT_TOP3 * top3_mean
                    + self.WEIGHT_MEAN * overall_mean
                )

                best_frame_index = int(np.argmax(frame_scores))

                # Categorise
                if composite >= self.STRONG_THRESHOLD:
                    category = "strong"
                    strong_ids.append(clip_id)
                elif composite >= self.MODERATE_THRESHOLD:
                    category = "moderate"
                    moderate_ids.append(clip_id)
                else:
                    category = "discard"
                    discard_ids.append(clip_id)

                scored.append({
                    "clip_id": clip_id,
                    "composite_score": round(composite, 4),
                    "max_score": round(max_score, 4),
                    "top3_mean": round(top3_mean, 4),
                    "overall_mean": round(overall_mean, 4),
                    "per_frame_scores": [round(s, 4) for s in frame_scores],
                    "best_frame_index": best_frame_index,
                    "frames_extracted": len(frame_paths),
                    "category": category,
                })

            except Exception as exc:
                errors.append({
                    "clip_id": clip_id,
                    "error": str(exc),
                })
            finally:
                # Clean up temp frames
                if tmp_dir and tmp_dir.is_dir():
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        # Sort by composite score descending
        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "scored": scored,
                "strong": strong_ids,
                "moderate": moderate_ids,
                "discard": discard_ids,
                "errors": errors,
                "candidates_total": len(candidates),
                "candidates_scored": len(scored),
            },
            duration_seconds=elapsed,
        )
