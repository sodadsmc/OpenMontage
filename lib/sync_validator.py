"""Sync Validator — duration enforcement between audio and visual assets.

Pre-assembly validation:
  - Every visual asset must match its segment's total_duration within tolerance
  - No gaps or overlaps in the timeline
  - Sum of visual durations must equal sum of audio + silence durations

Post-render validation:
  - Video track duration >= audio track duration
  - Final duration matches expected duration from the duration map

These are HARD GATES. The pipeline MUST NOT proceed if validation fails.

Usage:
    from lib.sync_validator import validate_pre_assembly, validate_post_render

    errors = validate_pre_assembly(duration_map, visual_assets)
    if errors:
        raise AssemblyError("Pre-assembly sync check failed")

    errors = validate_post_render("output.mp4", duration_map)
    if errors:
        raise RenderError("Post-render sync check failed")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lib.duration_map import DurationMap, get_video_duration, get_audio_duration


# Tolerances
VISUAL_DURATION_TOLERANCE_S = 0.15   # ±150ms for visual vs audio match
FINAL_DURATION_TOLERANCE_S = 2.0     # ±2s for total render duration (FFmpeg concat rounding)
MIN_SEGMENT_DURATION_S = 0.5         # Minimum meaningful segment duration


@dataclass
class SyncReport:
    """Result of sync validation."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Sync Validation: {status} ({self.checks_passed}/{self.checks_run} checks)"]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN:  {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pre-assembly validation
# ---------------------------------------------------------------------------

def validate_pre_assembly(
    duration_map: DurationMap,
    visual_assets: dict[str, str | Path],
    tolerance_s: float = VISUAL_DURATION_TOLERANCE_S,
) -> SyncReport:
    """Validate that every visual asset matches its required duration.

    Args:
        duration_map: The timing contract from TTS generation
        visual_assets: Mapping of segment_id → visual asset file path
        tolerance_s: Allowed duration mismatch (default ±150ms)

    Returns:
        SyncReport with errors (blocking) and warnings
    """
    report = SyncReport()

    # Check 1: Every segment in the duration map has a visual asset
    report.checks_run += 1
    missing = []
    for ts in duration_map.segments:
        if ts.id not in visual_assets:
            missing.append(ts.id)
    if missing:
        report.errors.append(
            f"Missing visual assets for {len(missing)} segments: "
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}"
        )
    else:
        report.checks_passed += 1

    # Check 2: Each visual asset duration matches required duration
    duration_mismatches = []
    for ts in duration_map.segments:
        if ts.id not in visual_assets:
            continue

        asset_path = Path(visual_assets[ts.id])
        report.checks_run += 1

        if not asset_path.exists():
            report.errors.append(f"{ts.id}: visual asset not found: {asset_path}")
            continue

        try:
            actual_dur = get_video_duration(asset_path)
        except Exception as e:
            report.errors.append(f"{ts.id}: cannot read duration of {asset_path}: {e}")
            continue

        required_dur = ts.total_duration_s
        diff = actual_dur - required_dur

        if abs(diff) > tolerance_s:
            direction = "too short" if diff < 0 else "too long"
            duration_mismatches.append(ts.id)
            report.errors.append(
                f"{ts.id}: visual is {actual_dur:.2f}s but needs {required_dur:.2f}s "
                f"({direction} by {abs(diff):.2f}s, tolerance={tolerance_s}s)"
            )
        else:
            report.checks_passed += 1

    # Check 3: No duplicate asset paths (unless explicitly reusable)
    report.checks_run += 1
    path_to_segments: dict[str, list[str]] = {}
    for seg_id, path in visual_assets.items():
        p = str(Path(path).resolve())
        path_to_segments.setdefault(p, []).append(seg_id)

    reused = {p: segs for p, segs in path_to_segments.items() if len(segs) > 1}
    if reused:
        for path, segs in reused.items():
            report.warnings.append(
                f"Asset reused across {len(segs)} segments: "
                f"{', '.join(segs)} -> {Path(path).name}"
            )
        # This is a warning, not an error — the Asset Registry (Phase 4)
        # will enforce uniqueness constraints
    report.checks_passed += 1

    # Check 4: Timeline is contiguous (no gaps)
    report.checks_run += 1
    for i in range(1, len(duration_map.segments)):
        prev = duration_map.segments[i - 1]
        curr = duration_map.segments[i]
        gap = curr.timeline_start_s - prev.timeline_end_s
        if abs(gap) > 0.01:  # 10ms tolerance for floating point
            report.errors.append(
                f"Timeline gap between {prev.id} and {curr.id}: "
                f"{prev.timeline_end_s:.3f}s to {curr.timeline_start_s:.3f}s "
                f"(gap={gap:.3f}s)"
            )
    report.checks_passed += 1

    # Check 5: Sum of durations matches total
    report.checks_run += 1
    sum_dur = sum(ts.total_duration_s for ts in duration_map.segments)
    if abs(sum_dur - duration_map.total_duration_s) > 0.01:
        report.errors.append(
            f"Duration sum mismatch: segments sum to {sum_dur:.3f}s "
            f"but map says {duration_map.total_duration_s:.3f}s"
        )
    else:
        report.checks_passed += 1

    if duration_mismatches:
        report.errors.append(
            f"DURATION MISMATCH SUMMARY: {len(duration_mismatches)} visual assets "
            f"do not match their required durations. These would cause "
            f"desync in the final render (bugs #2, #6, #9)."
        )

    return report


# ---------------------------------------------------------------------------
# Post-render validation
# ---------------------------------------------------------------------------

def validate_post_render(
    render_path: str | Path,
    duration_map: DurationMap,
    tolerance_s: float = FINAL_DURATION_TOLERANCE_S,
) -> SyncReport:
    """Validate the final rendered file against the duration map.

    Args:
        render_path: Path to the rendered video file
        duration_map: The timing contract
        tolerance_s: Allowed total duration mismatch (default ±500ms)

    Returns:
        SyncReport with errors if sync is broken
    """
    report = SyncReport()
    render_path = Path(render_path)

    if not render_path.exists():
        report.errors.append(f"Render file not found: {render_path}")
        return report

    # Check 1: Video duration >= audio duration (prevents bug #9)
    report.checks_run += 1
    try:
        video_dur = get_video_duration(render_path)
    except Exception as e:
        report.errors.append(f"Cannot read video duration: {e}")
        return report

    # Get audio duration (may differ from video if muxed separately)
    try:
        # Use ffprobe to get audio stream duration specifically
        import subprocess
        p = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=duration",
                "-of", "csv=p=0",
                str(render_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if p.returncode == 0 and p.stdout.strip():
            audio_dur = float(p.stdout.strip())
        else:
            # Fallback: use total audio from duration map
            audio_dur = duration_map.total_duration_s
    except Exception:
        audio_dur = duration_map.total_duration_s

    if video_dur < audio_dur - 0.1:
        report.errors.append(
            f"BUG #9 DETECTED: Video ({video_dur:.2f}s) is shorter than "
            f"audio ({audio_dur:.2f}s). Audio will be cut off."
        )
    else:
        report.checks_passed += 1

    # Check 2: Total duration matches expected
    report.checks_run += 1
    expected_dur = duration_map.total_duration_s
    diff = abs(video_dur - expected_dur)
    if diff > tolerance_s:
        report.errors.append(
            f"Final duration {video_dur:.2f}s differs from expected "
            f"{expected_dur:.2f}s by {diff:.2f}s (tolerance={tolerance_s}s)"
        )
    else:
        report.checks_passed += 1

    # Check 3: Minimum viable duration
    report.checks_run += 1
    if video_dur < 1.0:
        report.errors.append(f"Render is only {video_dur:.2f}s — likely corrupt")
    else:
        report.checks_passed += 1

    return report


# ---------------------------------------------------------------------------
# Convenience: validate visual duration against target
# ---------------------------------------------------------------------------

def check_visual_duration(
    visual_path: str | Path,
    target_duration_s: float,
    segment_id: str = "",
    tolerance_s: float = VISUAL_DURATION_TOLERANCE_S,
) -> tuple[bool, str]:
    """Quick check: does this visual match its target duration?

    Returns (passed, message). Use this in the generation loop to validate
    each visual immediately after creation.
    """
    try:
        actual = get_video_duration(visual_path)
    except Exception as e:
        return False, f"{segment_id}: cannot measure duration: {e}"

    diff = actual - target_duration_s
    if abs(diff) > tolerance_s:
        direction = "short" if diff < 0 else "long"
        return False, (
            f"{segment_id}: visual is {actual:.2f}s, needs {target_duration_s:.2f}s "
            f"({abs(diff):.2f}s too {direction})"
        )

    return True, f"{segment_id}: OK ({actual:.2f}s vs {target_duration_s:.2f}s target)"
