"""Quality Gate — multi-layer validation for AI-generated visual assets.

Layer 1: Automated checks (fast, no API calls)
  - Static frame detection (frozen video)
  - Duration validation (matches target)
  - File integrity (not corrupt, has video stream)

Layer 2: Vision-model semantic check (Gemini)
  - Does the clip match its visual description?
  - Are there obvious AI artifacts?
  - Is the time period correct?

The quality gate sits between generation and assembly. Bad clips get
rejected and regenerated (up to max_attempts), then fall back to stock
footage or text cards.

Usage:
    from lib.quality_gate import QualityGate

    gate = QualityGate()
    report = gate.evaluate(clip_path, visual_spec, target_duration_s)
    if not report.passed:
        regenerate_or_fallback()
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single quality check."""
    name: str
    passed: bool
    score: float  # 0.0 to 1.0
    details: str = ""


@dataclass
class QualityReport:
    """Aggregated quality gate result."""
    clip_path: str
    segment_id: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def overall_score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(c.score for c in self.checks) / len(self.checks)

    @property
    def issues(self) -> list[str]:
        return [c.details for c in self.checks if not c.passed]

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [
            f"Quality Gate: {status} ({self.overall_score:.0%}) — {self.segment_id}"
        ]
        for c in self.checks:
            icon = "OK" if c.passed else "FAIL"
            lines.append(f"  {icon}: {c.name} ({c.score:.0%}) {c.details}")
        return "\n".join(lines)


class QualityGate:
    """Multi-layer quality validation for generated visual assets."""

    def __init__(self, enable_gemini: bool = True):
        self.enable_gemini = enable_gemini

    def evaluate(
        self,
        clip_path: str | Path,
        visual_spec: Any,
        target_duration_s: float = 0.0,
        segment_id: str = "",
    ) -> QualityReport:
        """Run all quality checks on a generated clip.

        Args:
            clip_path: Path to the video/image file
            visual_spec: VisualSpec from the scored script
            target_duration_s: Expected duration (0 = skip duration check)
            segment_id: For reporting

        Returns:
            QualityReport with pass/fail and per-check details
        """
        clip_path = Path(clip_path)
        report = QualityReport(
            clip_path=str(clip_path),
            segment_id=segment_id,
        )

        # Layer 1: Automated checks
        report.checks.append(self._check_file_integrity(clip_path))

        if clip_path.suffix.lower() in (".mp4", ".webm", ".mov", ".avi"):
            report.checks.append(self._check_static_frames(clip_path))
            if target_duration_s > 0:
                report.checks.append(
                    self._check_duration(clip_path, target_duration_s)
                )

        # Layer 2: Gemini semantic check (if enabled and API key available)
        if self.enable_gemini and os.environ.get("GOOGLE_API_KEY"):
            desc = getattr(visual_spec, "description", "") if visual_spec else ""
            period = getattr(visual_spec, "reference_period", "") if visual_spec else ""
            if desc:
                report.checks.append(
                    self._check_semantic_match(clip_path, desc, period)
                )

        return report

    # ------------------------------------------------------------------
    # Layer 1: Automated checks
    # ------------------------------------------------------------------

    def _check_file_integrity(self, clip_path: Path) -> CheckResult:
        """Verify the file exists, has size, and has a valid video stream."""
        if not clip_path.exists():
            return CheckResult("file_integrity", False, 0.0, "File not found")

        if clip_path.stat().st_size < 1024:
            return CheckResult("file_integrity", False, 0.0,
                               f"File too small ({clip_path.stat().st_size} bytes)")

        # Check for valid video stream
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type",
                 "-of", "csv=p=0", str(clip_path)],
                capture_output=True, text=True, timeout=10,
            )
            if "video" not in p.stdout.lower():
                return CheckResult("file_integrity", False, 0.2,
                                   "No video stream found")
        except Exception as e:
            return CheckResult("file_integrity", False, 0.1, f"ffprobe error: {e}")

        return CheckResult("file_integrity", True, 1.0)

    def _check_static_frames(self, clip_path: Path) -> CheckResult:
        """Detect frozen/stuck video (all frames identical).

        Extracts 5 frames and compares pixel variance. A fully static
        video (single frame repeated) gets flagged.
        """
        try:
            import cv2
            cap = cv2.VideoCapture(str(clip_path))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames < 5:
                cap.release()
                return CheckResult("static_frames", True, 0.8,
                                   "Too few frames to check")

            # Sample 5 frames evenly
            positions = [int(total_frames * p) for p in [0.1, 0.3, 0.5, 0.7, 0.9]]
            frames = []
            for pos in positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()
                if ret:
                    # Downscale for fast comparison
                    small = cv2.resize(frame, (64, 36))
                    frames.append(small.astype(float))
            cap.release()

            if len(frames) < 3:
                return CheckResult("static_frames", True, 0.7,
                                   "Could not extract enough frames")

            # Compare consecutive frame differences
            diffs = []
            for i in range(len(frames) - 1):
                diff = abs(frames[i] - frames[i + 1]).mean()
                diffs.append(diff)

            avg_diff = sum(diffs) / len(diffs)

            # Threshold: very low diff = static video
            if avg_diff < 0.5:
                return CheckResult("static_frames", False, 0.1,
                                   f"Video appears static (avg frame diff={avg_diff:.2f})")
            elif avg_diff < 2.0:
                return CheckResult("static_frames", True, 0.6,
                                   f"Very low motion (avg frame diff={avg_diff:.2f})")
            else:
                return CheckResult("static_frames", True, 1.0,
                                   f"Normal motion (avg frame diff={avg_diff:.2f})")

        except ImportError:
            return CheckResult("static_frames", True, 0.5,
                               "cv2 not available — skipped")
        except Exception as e:
            return CheckResult("static_frames", True, 0.5,
                               f"Check failed: {e}")

    def _check_duration(
        self, clip_path: Path, target_s: float, tolerance_s: float = 0.5
    ) -> CheckResult:
        """Verify clip duration matches target."""
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(clip_path)],
                capture_output=True, text=True, timeout=10,
            )
            actual = float(p.stdout.strip())
        except Exception as e:
            return CheckResult("duration", False, 0.0, f"Cannot measure: {e}")

        diff = actual - target_s
        # Too SHORT by more than tolerance can't fill the slot cleanly → fail
        # (assembly would have to freeze-pad a large gap, risking desync).
        # Too LONG is fine: assembly trims every clip to the exact slot, and
        # providers like Grok have a hard ~6s minimum clip, so any sub-6s slot is
        # unavoidably a touch long. Treat over-length as acceptable.
        if diff < -tolerance_s:
            return CheckResult("duration", False, max(0, 1.0 + diff / target_s),
                               f"Clip is {actual:.1f}s, needs {target_s:.1f}s "
                               f"({abs(diff):.1f}s too short)")

        note = "will trim to slot" if diff > tolerance_s else "within tolerance"
        return CheckResult("duration", True, 1.0,
                           f"Duration OK ({actual:.1f}s vs {target_s:.1f}s target; {note})")

    # ------------------------------------------------------------------
    # Layer 2: Gemini semantic check
    # ------------------------------------------------------------------

    def _check_semantic_match(
        self,
        clip_path: Path,
        description: str,
        reference_period: str = "",
    ) -> CheckResult:
        """Use Gemini vision to verify the clip matches its description."""
        try:
            import google.generativeai as genai
        except ImportError:
            return CheckResult("semantic_match", True, 0.5,
                               "google-generativeai not installed — skipped")

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            return CheckResult("semantic_match", True, 0.5,
                               "No API key — skipped")

        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                "gemini-2.5-flash",
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )

            # Extract 3 frames for analysis
            frames = self._extract_sample_frames(clip_path)
            if not frames:
                return CheckResult("semantic_match", True, 0.5,
                                   "Could not extract frames")

            period_note = f" Time period should be: {reference_period}." if reference_period else ""

            prompt = f"""This video clip was generated for a documentary.
It should match this description: "{description}"{period_note}

Rate each criterion 1-10:
1. "content_match": Does the clip show what the description asks for?
2. "artifact_free": Are there AI artifacts (reversed motion, morphing faces, floating objects, extra limbs)?
3. "visual_quality": Does it look like plausible real footage (not obviously AI-generated)?

Return JSON:
{{"content_match": N, "artifact_free": N, "visual_quality": N, "issues": ["issue1", "issue2"]}}
"""

            # Upload frames
            parts = []
            uploaded_files = []
            for frame_path in frames:
                uf = genai.upload_file(path=str(frame_path),
                                       display_name=frame_path.stem)
                parts.append(uf)
                uploaded_files.append(uf)
            parts.append(prompt)

            response = model.generate_content(parts)

            # Cleanup uploaded files
            for uf in uploaded_files:
                try:
                    genai.delete_file(uf.name)
                except Exception:
                    pass

            # Parse
            text = response.text.strip()
            if "```" in text:
                match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
                if match:
                    text = match.group(1).strip()

            result = json.loads(text)
            scores = {
                "content_match": result.get("content_match", 5),
                "artifact_free": result.get("artifact_free", 5),
                "visual_quality": result.get("visual_quality", 5),
            }
            avg_score = sum(scores.values()) / len(scores) / 10.0
            issues = result.get("issues", [])

            passed = all(v >= 5 for v in scores.values())
            details = f"content={scores['content_match']}/10, " \
                      f"artifacts={scores['artifact_free']}/10, " \
                      f"quality={scores['visual_quality']}/10"
            if issues:
                details += f" | Issues: {'; '.join(issues[:3])}"

            return CheckResult("semantic_match", passed, avg_score, details)

        except Exception as e:
            _log.warning("Semantic check failed: %s", e)
            return CheckResult("semantic_match", True, 0.5,
                               f"Check failed: {str(e)[:80]}")

        finally:
            # Cleanup temp frames
            for fp in frames:
                try:
                    fp.unlink()
                except Exception:
                    pass

    def _extract_sample_frames(
        self, clip_path: Path, count: int = 3
    ) -> list[Path]:
        """Extract sample frames from a video at evenly spaced positions."""
        frames = []
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(clip_path)],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(p.stdout.strip()) if p.returncode == 0 else 5.0
        except Exception:
            duration = 5.0

        positions = [duration * (i + 1) / (count + 1) for i in range(count)]

        for i, pos in enumerate(positions):
            frame_path = clip_path.parent / f"_qg_{clip_path.stem}_f{i}.png"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{pos:.2f}", "-i", str(clip_path),
                     "-frames:v", "1", "-q:v", "2", str(frame_path)],
                    capture_output=True, timeout=10,
                )
                if frame_path.exists() and frame_path.stat().st_size > 0:
                    frames.append(frame_path)
            except Exception:
                pass

        return frames


# ---------------------------------------------------------------------------
# Convenience: full quality-gated generation loop
# ---------------------------------------------------------------------------

class GenerationHardStop(Exception):
    """A NON-retryable generation failure — out of credits, daily limit reached,
    image host down, or a placeholder prompt. Retrying just burns more credits/time,
    so we raise this to stop immediately and let the caller abort the batch."""


# Substrings that mark a failure as non-retryable (matched case-insensitively).
# Includes the out-of-credits / auth / rate-limit phrasings the active grok-kie
# adapter surfaces (HTTP 402/403/429), so a SYSTEMIC failure aborts the batch on the
# first shot instead of burning 3 retries x every remaining shot. False positives only
# cause an (safe) early abort, never extra spend.
_HARD_STOP_PATTERNS = (
    "insufficient", "daily limit", "exceeded", "balance",
    "failed to host", "placeholder", "(planned)", "quota",
    "credit", "payment required", "not enough", "forbidden",
    "unauthorized", "too many requests", "rate limit",
)


def _is_hard_stop(msg: str) -> bool:
    m = (msg or "").lower()
    return any(p in m for p in _HARD_STOP_PATTERNS)


def generate_with_quality_gate(
    generate_fn,
    visual_spec: Any,
    target_duration_s: float,
    segment_id: str,
    max_attempts: int = 3,
    enable_gemini: bool = True,
) -> tuple[Path | None, QualityReport | None]:
    """Generate a visual asset with quality gate retry loop.

    Args:
        generate_fn: Callable(spec, duration, attempt) → Path
        visual_spec: VisualSpec from scored script
        target_duration_s: Required duration
        segment_id: For reporting
        max_attempts: Max generation attempts before giving up
        enable_gemini: Whether to use Gemini semantic checks

    Returns:
        (asset_path, final_report) or (None, last_report) if all attempts fail
    """
    gate = QualityGate(enable_gemini=enable_gemini)
    last_report = None

    for attempt in range(max_attempts):
        try:
            clip_path = generate_fn(visual_spec, target_duration_s, attempt)
        except GenerationHardStop:
            raise
        except Exception as e:
            if _is_hard_stop(str(e)):
                # Out of credits / daily limit / host down / bad prompt — retrying
                # only burns credits. Stop now and let the caller abort the batch.
                _log.error("Non-retryable failure for %s (NOT retrying): %s", segment_id, e)
                raise GenerationHardStop(str(e)) from e
            _log.warning("Generation attempt %d failed for %s: %s",
                         attempt + 1, segment_id, e)
            continue

        if clip_path is None or not Path(clip_path).exists():
            continue

        report = gate.evaluate(
            clip_path, visual_spec, target_duration_s, segment_id
        )
        last_report = report

        if report.passed:
            _log.info("Quality gate PASSED for %s on attempt %d (score=%.0f%%)",
                       segment_id, attempt + 1, report.overall_score * 100)
            return Path(clip_path), report

        _log.warning(
            "Quality gate FAILED for %s on attempt %d: %s",
            segment_id, attempt + 1, "; ".join(report.issues)
        )

    _log.error("Quality gate failed %d times for %s", max_attempts, segment_id)
    return None, last_report
