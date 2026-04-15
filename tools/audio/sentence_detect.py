"""Sentence boundary detection via silence analysis.

Loads an audio file and detects silence gaps to identify potential visual
cut points between sentences. Falls back gracefully when pydub is not
available, recommending whisperx for word-level alignment.
"""

from __future__ import annotations

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
    ToolStatus,
    ToolTier,
)


class SentenceDetect(BaseTool):
    """Detects sentence boundaries in audio using silence gap analysis."""

    name = "sentence_detect"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "audio_analysis"
    provider = "pydub"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["python:pydub"]
    install_instructions = (
        "Install pydub for audio analysis:\n"
        "  pip install pydub\n"
        "FFmpeg must also be installed for non-WAV formats."
    )

    capabilities = ["audio_analysis", "sentence_detection"]
    supports = {
        "silence_detection": True,
        "cut_point_identification": True,
    }
    best_for = [
        "finding visual cut points aligned to narration pauses",
        "sentence boundary detection for documentary pacing",
    ]
    not_good_for = [
        "word-level alignment (use whisperx instead)",
        "music or non-speech audio",
    ]

    input_schema = {
        "type": "object",
        "required": ["audio_path", "scene_id"],
        "properties": {
            "audio_path": {
                "type": "string",
                "description": "Path to the narration audio file",
            },
            "scene_id": {
                "type": "string",
                "description": "Scene identifier for this audio segment",
            },
            "min_silence_len": {
                "type": "integer",
                "default": 300,
                "description": "Minimum silence length in milliseconds to consider a gap",
            },
            "silence_thresh": {
                "type": "integer",
                "default": -40,
                "description": "Silence threshold in dBFS",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=50
    )
    idempotency_key_fields = ["audio_path", "scene_id"]
    side_effects = []
    user_visible_verification = [
        "Verify detected sentence boundaries align with natural speech pauses",
    ]

    def get_status(self) -> ToolStatus:
        """Check if pydub is available."""
        try:
            __import__("pydub")
            return ToolStatus.AVAILABLE
        except ImportError:
            return ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Detect sentence boundaries in audio via silence analysis."""
        audio_path = Path(inputs["audio_path"])
        scene_id = inputs["scene_id"]
        min_silence_len = inputs.get("min_silence_len", 300)
        silence_thresh = inputs.get("silence_thresh", -40)

        if not audio_path.exists():
            return ToolResult(
                success=False,
                error=f"Audio file not found: {audio_path}",
            )

        start = time.time()

        try:
            segments = self._detect_sentences(
                audio_path, min_silence_len, silence_thresh
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Sentence detection failed: {exc}",
            )

        elapsed = round(time.time() - start, 2)

        # Calculate total duration
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(audio_path))
        total_duration = round(len(audio) / 1000.0, 3)

        # Build note if gaps were unclear
        note = None
        if len(segments) <= 1:
            note = (
                "Only one segment detected — silence gaps may be too short or "
                "threshold too aggressive. For better results, use whisperx for "
                "word-level alignment."
            )

        result_data: dict[str, Any] = {
            "scene_id": scene_id,
            "segments": segments,
            "segment_count": len(segments),
            "total_duration_seconds": total_duration,
            "detection_params": {
                "min_silence_len_ms": min_silence_len,
                "silence_thresh_dbfs": silence_thresh,
            },
        }
        if note:
            result_data["note"] = note

        return ToolResult(
            success=True,
            data=result_data,
            duration_seconds=elapsed,
        )

    def _detect_sentences(
        self,
        audio_path: Path,
        min_silence_len: int,
        silence_thresh: int,
    ) -> list[dict[str, Any]]:
        """Analyze audio for silence gaps and return sentence segments."""
        from pydub import AudioSegment
        from pydub.silence import detect_silence

        audio = AudioSegment.from_file(str(audio_path))
        total_ms = len(audio)

        # detect_silence returns list of [start_ms, end_ms] for silent ranges
        silent_ranges = detect_silence(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
        )

        # Convert silence ranges to sentence segments
        segments: list[dict[str, Any]] = []

        if not silent_ranges:
            # No silences detected — treat entire audio as one segment
            segments.append({
                "start_seconds": 0.0,
                "end_seconds": round(total_ms / 1000.0, 3),
                "gap_after_seconds": 0.0,
            })
            return segments

        # First segment: from start to first silence
        first_silence_start = silent_ranges[0][0]
        if first_silence_start > 0:
            first_gap = round(
                (silent_ranges[0][1] - silent_ranges[0][0]) / 1000.0, 3
            )
            segments.append({
                "start_seconds": 0.0,
                "end_seconds": round(first_silence_start / 1000.0, 3),
                "gap_after_seconds": first_gap,
            })

        # Middle segments: between silence gaps
        for i in range(len(silent_ranges) - 1):
            seg_start = silent_ranges[i][1]  # end of current silence
            seg_end = silent_ranges[i + 1][0]  # start of next silence
            gap_duration = round(
                (silent_ranges[i + 1][1] - silent_ranges[i + 1][0]) / 1000.0, 3
            )

            if seg_end > seg_start:
                segments.append({
                    "start_seconds": round(seg_start / 1000.0, 3),
                    "end_seconds": round(seg_end / 1000.0, 3),
                    "gap_after_seconds": gap_duration,
                })

        # Last segment: from last silence end to audio end
        last_silence_end = silent_ranges[-1][1]
        if last_silence_end < total_ms:
            segments.append({
                "start_seconds": round(last_silence_end / 1000.0, 3),
                "end_seconds": round(total_ms / 1000.0, 3),
                "gap_after_seconds": 0.0,
            })

        return segments
