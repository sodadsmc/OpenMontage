"""Duration Map — the single source of truth for timeline timing.

The Duration Map is built AFTER TTS generation. It measures exact audio
durations via ffprobe and computes absolute timeline positions for every
segment. All downstream stages (visual generation, assembly) use this
map as their timing contract.

Pipeline order:
    Scored Script → TTS Generation → Duration Map → Visual Generation → Assembly

Usage:
    from lib.scored_script import load_scored_script
    from lib.duration_map import build_duration_map, save_duration_map

    script = load_scored_script("scored_script.yaml")
    timed = build_duration_map(script, audio_dir="assets/audio")
    save_duration_map(timed, "artifacts/duration_map.json")
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from lib.scored_script import ScoredScript, Segment, VisualSpec


@dataclass
class TimedSegment:
    """A segment with measured audio duration and absolute timeline position."""
    id: str
    act: str
    narration: str
    audio_path: str
    audio_duration_s: float      # Measured from actual TTS output
    silence_after_s: float       # From scored script
    total_duration_s: float      # audio + silence
    timeline_start_s: float      # Absolute position in final video
    timeline_end_s: float        # timeline_start + total_duration
    visual_type: str             # From scored script visual spec
    visual_description: str      # From scored script visual spec
    word_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DurationMap:
    """Complete timeline map — the timing contract for all downstream stages."""
    segments: list[TimedSegment]
    total_duration_s: float
    total_audio_s: float
    total_silence_s: float
    segment_count: int

    def get_segment(self, seg_id: str) -> TimedSegment | None:
        """Look up a segment by ID."""
        for s in self.segments:
            if s.id == seg_id:
                return s
        return None

    def get_target_duration(self, seg_id: str) -> float:
        """Get the exact duration a visual must be for this segment."""
        seg = self.get_segment(seg_id)
        if seg is None:
            raise KeyError(f"Segment {seg_id} not found in duration map")
        return seg.total_duration_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1.0",
            "total_duration_s": self.total_duration_s,
            "total_audio_s": self.total_audio_s,
            "total_silence_s": self.total_silence_s,
            "segment_count": self.segment_count,
            "segments": [s.to_dict() for s in self.segments],
        }

    def summary(self) -> str:
        lines = [
            f"Duration Map: {self.segment_count} segments, "
            f"{self.total_duration_s:.1f}s ({self.total_duration_s/60:.1f} min)",
            f"  Audio: {self.total_audio_s:.1f}s | Silence: {self.total_silence_s:.1f}s",
            "",
        ]
        for s in self.segments:
            marker = f"[+{s.silence_after_s:.0f}s silence]" if s.silence_after_s > 0 else ""
            lines.append(
                f"  {s.id} [{s.act}] "
                f"{s.timeline_start_s:7.2f}s - {s.timeline_end_s:7.2f}s "
                f"({s.audio_duration_s:.1f}s audio) "
                f"{s.visual_type:20s} {marker}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Audio duration measurement
# ---------------------------------------------------------------------------

def get_audio_duration(path: str | Path) -> float:
    """Get exact audio duration in seconds via ffprobe."""
    p = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {p.stderr.strip()}")
    return float(p.stdout.strip())


def get_video_duration(path: str | Path) -> float:
    """Get exact video duration in seconds via ffprobe."""
    return get_audio_duration(path)  # same ffprobe call works for video


# ---------------------------------------------------------------------------
# Duration Map builder
# ---------------------------------------------------------------------------

def build_duration_map(
    script: ScoredScript,
    audio_dir: str | Path,
    audio_pattern: str = "seg_{index:03d}.mp3",
) -> DurationMap:
    """Build a duration map from a scored script and existing TTS audio files.

    Args:
        script: Parsed scored script
        audio_dir: Directory containing TTS audio files
        audio_pattern: Filename pattern with {index} placeholder

    Returns:
        DurationMap with measured durations and absolute timeline positions
    """
    audio_dir = Path(audio_dir)
    timed_segments: list[TimedSegment] = []
    cursor = 0.0
    total_audio = 0.0
    total_silence = 0.0

    for seg in script.segments:
        # Find the audio file
        audio_file = audio_dir / audio_pattern.format(index=seg.index)

        if not audio_file.exists():
            # Try alternative naming: use the segment ID directly
            alt_file = audio_dir / f"{seg.id}.mp3"
            if alt_file.exists():
                audio_file = alt_file
            else:
                raise FileNotFoundError(
                    f"Audio file not found for {seg.id}: "
                    f"tried {audio_file} and {alt_file}"
                )

        # Measure exact duration
        audio_dur = get_audio_duration(audio_file)
        silence = seg.silence_after_s
        total_dur = audio_dur + silence

        timed_segments.append(TimedSegment(
            id=seg.id,
            act=seg.act,
            narration=seg.narration,
            audio_path=str(audio_file),
            audio_duration_s=round(audio_dur, 3),
            silence_after_s=silence,
            total_duration_s=round(total_dur, 3),
            timeline_start_s=round(cursor, 3),
            timeline_end_s=round(cursor + total_dur, 3),
            visual_type=seg.visual.type,
            visual_description=seg.visual.description,
            word_count=seg.word_count,
        ))

        total_audio += audio_dur
        total_silence += silence
        cursor += total_dur

    return DurationMap(
        segments=timed_segments,
        total_duration_s=round(cursor, 3),
        total_audio_s=round(total_audio, 3),
        total_silence_s=round(total_silence, 3),
        segment_count=len(timed_segments),
    )


def build_duration_map_from_paths(
    script: ScoredScript,
    audio_paths: dict[str, str | Path],
) -> DurationMap:
    """Build a duration map from explicit segment_id → audio_path mapping.

    Use this when audio files don't follow a predictable naming pattern.
    """
    timed_segments: list[TimedSegment] = []
    cursor = 0.0
    total_audio = 0.0
    total_silence = 0.0

    for seg in script.segments:
        if seg.id not in audio_paths:
            raise KeyError(f"No audio path provided for segment {seg.id}")

        audio_file = Path(audio_paths[seg.id])
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        audio_dur = get_audio_duration(audio_file)
        silence = seg.silence_after_s
        total_dur = audio_dur + silence

        timed_segments.append(TimedSegment(
            id=seg.id,
            act=seg.act,
            narration=seg.narration,
            audio_path=str(audio_file),
            audio_duration_s=round(audio_dur, 3),
            silence_after_s=silence,
            total_duration_s=round(total_dur, 3),
            timeline_start_s=round(cursor, 3),
            timeline_end_s=round(cursor + total_dur, 3),
            visual_type=seg.visual.type,
            visual_description=seg.visual.description,
            word_count=seg.word_count,
        ))

        total_audio += audio_dur
        total_silence += silence
        cursor += total_dur

    return DurationMap(
        segments=timed_segments,
        total_duration_s=round(cursor, 3),
        total_audio_s=round(total_audio, 3),
        total_silence_s=round(total_silence, 3),
        segment_count=len(timed_segments),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_duration_map(dm: DurationMap, path: str | Path) -> Path:
    """Save a duration map to JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dm.to_dict(), f, indent=2)
    return path


def load_duration_map(path: str | Path) -> DurationMap:
    """Load a duration map from JSON."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)

    segments = [
        TimedSegment(**s) for s in doc["segments"]
    ]
    return DurationMap(
        segments=segments,
        total_duration_s=doc["total_duration_s"],
        total_audio_s=doc["total_audio_s"],
        total_silence_s=doc["total_silence_s"],
        segment_count=doc["segment_count"],
    )
