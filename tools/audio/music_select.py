"""Mood-based music selection from a local library.

Scans a music library directory for audio files and recommends tracks
based on scene moods. Does NOT generate music — only selects from
available files or reports unavailability with setup instructions.
"""

from __future__ import annotations

import subprocess
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

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac"}

# Keyword hints for mood-matching against filenames
_MOOD_KEYWORDS: dict[str, list[str]] = {
    "tension": ["tension", "suspense", "dark", "ominous", "eerie", "sinister", "dread"],
    "neutral": ["neutral", "ambient", "calm", "background", "corporate", "soft"],
    "dramatic": ["dramatic", "epic", "intense", "cinematic", "powerful", "crisis"],
    "resolution": ["resolution", "hopeful", "uplifting", "gentle", "reflective", "peaceful"],
}


class MusicSelect(BaseTool):
    """Selects music tracks from a local library based on scene moods."""

    name = "music_select"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "music_selection"
    provider = "local"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = []
    install_instructions = (
        "No dependencies required. Place royalty-free music files in\n"
        "  music_library/ (mp3, wav, ogg, or flac)\n"
        "Optionally install pydub for accurate duration detection:\n"
        "  pip install pydub"
    )

    capabilities = ["music_selection"]
    supports = {
        "mood_matching": True,
        "library_scan": True,
    }
    best_for = [
        "selecting background music from a curated local library",
        "mood-based track recommendation for documentary videos",
    ]
    not_good_for = [
        "generating music (use music_gen or suno_music instead)",
        "music with specific BPM or key requirements",
    ]

    input_schema = {
        "type": "object",
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "description": "List of scene dicts, each must have a 'mood' field",
                "items": {
                    "type": "object",
                    "required": ["mood"],
                    "properties": {
                        "mood": {
                            "type": "string",
                            "enum": ["tension", "neutral", "dramatic", "resolution"],
                        },
                        "scene_id": {"type": "string"},
                    },
                },
            },
            "music_library_path": {
                "type": "string",
                "default": "music_library/",
                "description": "Path to directory containing audio files",
            },
            "strategy": {
                "type": "string",
                "enum": ["single_track", "per_mood"],
                "default": "single_track",
                "description": (
                    "single_track: recommend one track for the whole video. "
                    "per_mood: recommend a track per distinct mood."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10
    )
    idempotency_key_fields = ["scenes", "music_library_path", "strategy"]
    side_effects = []
    user_visible_verification = [
        "Listen to recommended tracks and verify mood alignment",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Scan music library and recommend tracks for the given scenes."""
        scenes = inputs.get("scenes", [])
        if not scenes:
            return ToolResult(success=False, error="No scenes provided")

        library_path = Path(inputs.get("music_library_path", "music_library/"))
        strategy = inputs.get("strategy", "single_track")

        start = time.time()

        # Scan library for audio files
        tracks = self._scan_library(library_path)

        if not tracks:
            return ToolResult(
                success=True,
                data={
                    "available": False,
                    "tracks": [],
                    "recommendation": "no_music_available",
                    "instructions": (
                        "No music files found. To add background music:\n"
                        "  1. Drop royalty-free audio files (.mp3, .wav, .ogg, .flac) "
                        f"into {library_path}/\n"
                        "  2. Or configure a music generation API (music_gen, suno_music)\n"
                        "  3. Free sources: YouTube Audio Library, Pixabay Music, Jamendo"
                    ),
                },
                duration_seconds=round(time.time() - start, 2),
            )

        # Get durations for all tracks
        tracks_with_info = []
        for track_path in tracks:
            duration = self._get_duration(track_path)
            tracks_with_info.append({
                "path": str(track_path),
                "filename": track_path.name,
                "duration_seconds": duration,
            })

        # Extract unique moods from scenes
        moods_needed = list(dict.fromkeys(
            s.get("mood", "neutral") for s in scenes
        ))

        # Build recommendations
        if strategy == "per_mood":
            recommendations = self._recommend_per_mood(
                tracks_with_info, moods_needed
            )
        else:
            recommendations = self._recommend_single(
                tracks_with_info, moods_needed
            )

        return ToolResult(
            success=True,
            data={
                "available": True,
                "library_path": str(library_path),
                "track_count": len(tracks_with_info),
                "tracks": tracks_with_info,
                "moods_needed": moods_needed,
                "strategy": strategy,
                "recommendations": recommendations,
            },
            duration_seconds=round(time.time() - start, 2),
        )

    def _scan_library(self, library_path: Path) -> list[Path]:
        """Find all audio files in the music library directory."""
        if not library_path.exists() or not library_path.is_dir():
            return []

        audio_files = []
        for ext in _AUDIO_EXTENSIONS:
            audio_files.extend(library_path.glob(f"*{ext}"))
            audio_files.extend(library_path.glob(f"**/*{ext}"))

        # Deduplicate and sort by name
        seen: set[str] = set()
        unique: list[Path] = []
        for f in sorted(audio_files, key=lambda p: p.name.lower()):
            resolved = str(f.resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique.append(f)

        return unique

    def _get_duration(self, audio_path: Path) -> float | None:
        """Get audio duration in seconds. Tries pydub first, then ffprobe."""
        # Try pydub
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(str(audio_path))
            return round(len(audio) / 1000.0, 2)
        except Exception:
            pass

        # Try ffprobe
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return round(float(result.stdout.strip().split("\n")[0]), 2)
        except Exception:
            pass

        return None

    def _match_mood(self, filename: str, mood: str) -> float:
        """Score how well a filename matches a mood (0.0 to 1.0)."""
        name_lower = filename.lower()
        keywords = _MOOD_KEYWORDS.get(mood, [])
        if not keywords:
            return 0.0

        matches = sum(1 for kw in keywords if kw in name_lower)
        return min(matches / max(len(keywords), 1), 1.0)

    def _recommend_single(
        self,
        tracks: list[dict[str, Any]],
        moods: list[str],
    ) -> dict[str, Any]:
        """Recommend a single track that best fits the overall mood mix."""
        if not tracks:
            return {"track": None, "reason": "No tracks available"}

        # Score each track against all moods
        best_track = tracks[0]
        best_score = -1.0

        for track in tracks:
            score = sum(self._match_mood(track["filename"], m) for m in moods)
            if score > best_score:
                best_score = score
                best_track = track

        reason = (
            "Best overall mood match"
            if best_score > 0
            else "Default selection (no mood keywords in filenames)"
        )

        return {
            "strategy": "single_track",
            "track": best_track,
            "reason": reason,
        }

    def _recommend_per_mood(
        self,
        tracks: list[dict[str, Any]],
        moods: list[str],
    ) -> dict[str, Any]:
        """Recommend one track per distinct mood."""
        if not tracks:
            return {"tracks_by_mood": {}, "reason": "No tracks available"}

        tracks_by_mood: dict[str, dict[str, Any]] = {}

        for mood in moods:
            best_track = tracks[0]
            best_score = -1.0

            for track in tracks:
                score = self._match_mood(track["filename"], mood)
                if score > best_score:
                    best_score = score
                    best_track = track

            tracks_by_mood[mood] = {
                "track": best_track,
                "match_score": round(best_score, 2),
            }

        return {
            "strategy": "per_mood",
            "tracks_by_mood": tracks_by_mood,
        }
