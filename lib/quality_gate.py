"""Quality Gate — multi-layer validation for AI-generated visual assets.

Layer 1: Automated checks (fast, no API calls)
  - Static frame detection (frozen video)
  - Duration validation (matches target)
  - File integrity (not corrupt, has video stream)

Layer 2: Vision-model semantic + artifact check (Gemini, VIDEO upload)
  - Does the clip depict its description (and narration, when provided)?
  - Are there generation artifacts — bodies merging into surfaces, geometry
    that stretches during camera moves, morphing faces/limbs?
  - Does the clip stay one coherent scene from first frame to last?

  The clip is uploaded as a VIDEO (google-generativeai files API — same
  pattern as lib/manim_validator.py). Still frames are NOT enough here:
  the two artifact classes that actually shipped in production (an
  ever-elongating corridor during a dolly move; a patient melting INTO the
  bed) read as perfectly plausible in any single frame — only the motion
  betrays them.

Failure semantics (Layer 2):
  - GOOGLE_API_KEY set + check errors (upload, inference, JSON parse) →
    the check FAILS (retryable). An unverified clip must not silently ship;
    a retry is cheap relative to a broken clip reaching assembly.
  - GOOGLE_API_KEY missing → loud one-time warning, check skipped (we
    cannot fail-closed with no way to ever pass).
  - Env QC_FAIL_OPEN=1 restores the legacy fail-open behavior (errors pass
    at 0.5, with a stills fallback if the video upload fails) as an escape
    hatch for Gemini outages mid-batch.

Keyframe gate:
  - validate_keyframe() screens a still keyframe BEFORE paid image-to-video.
    A keyframe whose subject is already merged into the furniture poisons
    every i2v attempt seeded from it, so it is far cheaper to reject the
    image than to burn video generations.

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
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 2 tuning knobs (module constants so calibration runs can reference
# them). Calibrated against the therac-25-test production clips — see
# _qc_gate_test.py: on-prompt clips score 7-10 on every criterion, and
# deliberately-wrong-content negative controls score content_match=1, so 5
# splits them with wide margin. The originally user-reported artifact clips
# could not be re-tested (those generations were deleted and regenerated on
# June 8), so the artifact criterion is validated by rubric specificity plus
# the negative controls, not by a confirmed-bad clip.
# ---------------------------------------------------------------------------

GEMINI_QC_MODEL = "gemini-2.5-flash"

# Video clip rubric thresholds (each criterion scored 1-10 by Gemini;
# a clip must meet ALL of them to pass the semantic check).
CONTENT_MATCH_MIN = 5
ARTIFACT_FREE_MIN = 5
TEMPORAL_COHERENCE_MIN = 5

# Keyframe (still image) gate thresholds.
KEYFRAME_ANATOMY_MIN = 6
KEYFRAME_SUBJECT_MATCH_MIN = 5
KEYFRAME_TEXT_FREE_MIN = 6

# Max seconds to wait for the Gemini files API to finish PROCESSING an upload.
UPLOAD_TIMEOUT_S = 180

# The deliberate channel look. Stated verbatim in every rubric so the model
# never mistakes the art direction for a generation defect.
_STYLE_NOTE = (
    "IMPORTANT — the footage is INTENTIONALLY stylized as a graphic-novel "
    "illustration: bold black ink linework, cross-hatch and stippled shading, "
    "halftone dot texture, a duotone palette of deep navy blue and warm amber, "
    "high contrast, heavy film grain, and aged-paper texture. This deliberate "
    "art style is EXPECTED. Do NOT treat the illustration style, the limited "
    "duotone palette, grain, halftone dots, or the absence of photorealism as "
    "an artifact or a quality problem."
)

_warned_no_key = False


def _fail_open() -> bool:
    """QC_FAIL_OPEN=1 restores the legacy pass-at-0.5-on-error behavior.

    Escape hatch only: with it set, a Gemini outage mid-batch degrades QC
    instead of failing every clip. Default is fail-closed because the whole
    point of the gate is that an unverified clip never silently ships.
    """
    return os.environ.get("QC_FAIL_OPEN", "") == "1"


def _warn_no_key_once(context: str) -> None:
    """Loud one-time warning when semantic QC is impossible (no API key).

    We can't fail-closed without a key — every clip would fail forever — so
    the check is skipped, but the operator must know the gate is weakened.
    """
    global _warned_no_key
    if _warned_no_key:
        return
    _warned_no_key = True
    _log.warning(
        "=" * 70 + "\n"
        "GOOGLE_API_KEY is not set — Gemini semantic/artifact QC is DISABLED "
        "(%s). Clips will only get Layer-1 checks (integrity/static/duration); "
        "morphing and content-mismatch artifacts will NOT be caught. "
        "Set GOOGLE_API_KEY to enable the full quality gate.\n" + "=" * 70,
        context,
    )


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
            visual_spec: Any object with .description (and optionally
                .narration / .reference_period) — VisualSpec from the scored
                script, or a SimpleNamespace from a caller
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

        # Layer 2: Gemini semantic + artifact check. Uses BOTH the visual
        # description and the narration — a clip can match its prompt and
        # still contradict what the narrator is saying over it.
        if self.enable_gemini:
            desc = getattr(visual_spec, "description", "") if visual_spec else ""
            period = getattr(visual_spec, "reference_period", "") if visual_spec else ""
            narration = getattr(visual_spec, "narration", "") if visual_spec else ""
            if not os.environ.get("GOOGLE_API_KEY"):
                _warn_no_key_once(f"clip QC for {segment_id or clip_path.name}")
            elif desc or narration:
                report.checks.append(
                    self._check_semantic_match(
                        clip_path, desc, period, narration=narration
                    )
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
    # Layer 2: Gemini semantic + artifact check (video upload)
    # ------------------------------------------------------------------

    def _check_semantic_match(
        self,
        clip_path: Path,
        description: str,
        reference_period: str = "",
        narration: str = "",
    ) -> CheckResult:
        """Upload the actual VIDEO to Gemini and score it against the rubric.

        Stills can't catch the production failure modes (geometry that keeps
        stretching during a dolly, a body gradually melting into the bed) —
        each sampled frame looks fine in isolation. The full clip is cheap to
        upload at documentary shot lengths (5-15s).

        Fail-closed: with an API key present, any error here returns a FAILED
        (retryable) check unless QC_FAIL_OPEN=1, in which case we degrade to
        the legacy stills check and ultimately pass-at-0.5.
        """
        try:
            import google.generativeai as genai
        except ImportError:
            # Permanent environment gap, same class as a missing key: failing
            # every clip would burn paid regenerations with zero signal.
            _warn_no_key_once("google-generativeai not installed")
            return CheckResult("semantic_match", True, 0.5,
                               "google-generativeai not installed — skipped")

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            _warn_no_key_once(f"clip QC for {clip_path.name}")
            return CheckResult("semantic_match", True, 0.5,
                               "No API key — skipped")

        prompt = self._video_rubric(description, narration, reference_period)

        uploaded = None
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                GEMINI_QC_MODEL,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )

            # Upload the video and poll until the files API has processed it
            # (same pattern as lib/manim_validator.py).
            uploaded = genai.upload_file(path=str(clip_path),
                                         display_name=clip_path.stem)
            deadline = time.time() + UPLOAD_TIMEOUT_S
            while uploaded.state.name == "PROCESSING":
                if time.time() > deadline:
                    raise TimeoutError(
                        f"Gemini upload still PROCESSING after {UPLOAD_TIMEOUT_S}s")
                time.sleep(2)
                uploaded = genai.get_file(uploaded.name)
            if uploaded.state.name != "ACTIVE":
                raise RuntimeError(
                    f"Gemini upload ended in state {uploaded.state.name}")

            response = model.generate_content([uploaded, prompt])
            result = _parse_json_response(response.text)

            scores = {
                "content_match": int(result.get("content_match", 0)),
                "artifact_free": int(result.get("artifact_free", 0)),
                "temporal_coherence": int(result.get("temporal_coherence", 0)),
            }
            issues = [str(i) for i in (result.get("issues") or [])]

        except Exception as e:
            if _fail_open():
                # Legacy escape hatch: degrade to the stills check (which
                # itself passes at 0.5 on any error).
                _log.warning("Video QC errored (%s) — QC_FAIL_OPEN=1, falling "
                             "back to stills for %s", e, clip_path.name)
                return self._check_semantic_match_stills(
                    clip_path, description, reference_period, narration)
            _log.warning("Video QC errored for %s — failing closed: %s",
                         clip_path.name, e)
            return CheckResult(
                "semantic_match", False, 0.0,
                f"QC check errored (fail-closed, retryable): {str(e)[:120]}")

        finally:
            if uploaded is not None:
                try:
                    genai.delete_file(uploaded.name)
                except Exception:
                    pass

        passed = (
            scores["content_match"] >= CONTENT_MATCH_MIN
            and scores["artifact_free"] >= ARTIFACT_FREE_MIN
            and scores["temporal_coherence"] >= TEMPORAL_COHERENCE_MIN
        )
        avg_score = sum(scores.values()) / (10.0 * len(scores))
        details = (
            f"content={scores['content_match']}/10 (min {CONTENT_MATCH_MIN}), "
            f"artifacts={scores['artifact_free']}/10 (min {ARTIFACT_FREE_MIN}), "
            f"coherence={scores['temporal_coherence']}/10 "
            f"(min {TEMPORAL_COHERENCE_MIN})"
        )
        if issues:
            details += f" | Issues: {'; '.join(issues[:3])}"

        return CheckResult("semantic_match", passed, avg_score, details)

    @staticmethod
    def _video_rubric(description: str, narration: str = "",
                      reference_period: str = "") -> str:
        """Build the Layer-2 video rubric.

        The artifact criterion names the concrete failure modes that shipped
        in production (merge/melt into surfaces, elongating geometry) because
        a generic "any AI artifacts?" question scored those clips 7-8/10.
        """
        narration_note = (
            f'\nThe narration spoken over this clip is: "{narration}"\n'
            "The clip should be consistent with that narration."
            if narration else ""
        )
        period_note = (
            f"\nThe depicted time period should be: {reference_period}."
            if reference_period else ""
        )
        return f"""You are a strict quality-control reviewer for an AI-generated documentary clip. Watch the ENTIRE video before scoring.

{_STYLE_NOTE}

The clip was generated for this shot description:
"{description}"
{narration_note}{period_note}

Score each criterion from 1 (terrible) to 10 (perfect):

1. "content_match": Does the clip depict the subject, setting, and action of the description (and stay consistent with the narration, if given)?

2. "artifact_free": Watch for these specific AI generation failures across the WHOLE clip:
   - bodies or objects that MERGE or MELT into surfaces — e.g. a person whose torso or head sinks/blends INTO a bed, table, or wall instead of resting ON it;
   - room or corridor GEOMETRY that grows, stretches, or changes proportions during camera movement — e.g. a hallway that keeps elongating so the camera never gets closer to the door it is moving toward, walls or ceilings that warp or rescale;
   - morphing faces or limbs, extra or missing limbs, anatomy that deforms over time;
   - objects or people that appear out of nowhere, dissolve, or transform into something else mid-clip;
   - garbled, mutating, or nonsensical text/lettering.
   10 = none of these anywhere in the clip. 1-3 = at least one of these failures is clearly visible. The deliberate graphic-novel illustration style (ink linework, halftone, navy/amber duotone, film grain) is NOT an artifact.
   IMPORTANT: this criterion is ONLY about generation defects. A clip that simply
   omits or substitutes something the description asked for (wrong tree species, a
   missing prop, an action that doesn't happen) is a content_match problem, NOT an
   artifact — do not lower artifact_free for prompt-fidelity gaps.

3. "temporal_coherence": Does the clip remain one continuous, stable scene — same place, same subjects, consistent spatial layout — from first frame to last, allowing for the described camera and subject motion? Score low if the scene's identity or layout drifts into a different-looking space, or if perspective/scale relationships do not stay physically consistent during the camera move.

Return ONLY JSON:
{{"content_match": N, "artifact_free": N, "temporal_coherence": N, "issues": ["short concrete description of each problem you saw"]}}
"""

    def _check_semantic_match_stills(
        self,
        clip_path: Path,
        description: str,
        reference_period: str = "",
        narration: str = "",
    ) -> CheckResult:
        """Legacy stills-based check — fallback ONLY when the video upload
        failed AND QC_FAIL_OPEN=1.

        Kept because in fail-open mode a degraded check is still better than
        no check, but it cannot see motion so it misses the geometry-stretch
        and merge-over-time artifact classes. All its error paths pass at 0.5
        (that is the meaning of fail-open).
        """
        frames: list[Path] = []
        try:
            import google.generativeai as genai

            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                return CheckResult("semantic_match", True, 0.5,
                                   "No API key — skipped")

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                GEMINI_QC_MODEL,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )

            frames = self._extract_sample_frames(clip_path)
            if not frames:
                return CheckResult("semantic_match", True, 0.5,
                                   "Could not extract frames")

            prompt = (
                "These are 3 still frames sampled from an AI-generated "
                "documentary clip.\n\n"
                + self._video_rubric(description, narration, reference_period)
                + "\nNote: you are only seeing stills, so judge "
                "temporal_coherence by consistency BETWEEN the frames."
            )

            parts: list[Any] = []
            uploaded_files = []
            for frame_path in frames:
                uf = genai.upload_file(path=str(frame_path),
                                       display_name=frame_path.stem)
                parts.append(uf)
                uploaded_files.append(uf)
            parts.append(prompt)

            response = model.generate_content(parts)

            for uf in uploaded_files:
                try:
                    genai.delete_file(uf.name)
                except Exception:
                    pass

            result = _parse_json_response(response.text)
            scores = {
                "content_match": int(result.get("content_match", 5)),
                "artifact_free": int(result.get("artifact_free", 5)),
                "temporal_coherence": int(result.get("temporal_coherence", 5)),
            }
            avg_score = sum(scores.values()) / (10.0 * len(scores))
            issues = [str(i) for i in (result.get("issues") or [])]

            passed = (
                scores["content_match"] >= CONTENT_MATCH_MIN
                and scores["artifact_free"] >= ARTIFACT_FREE_MIN
                and scores["temporal_coherence"] >= TEMPORAL_COHERENCE_MIN
            )
            details = (
                f"[stills fallback] content={scores['content_match']}/10, "
                f"artifacts={scores['artifact_free']}/10, "
                f"coherence={scores['temporal_coherence']}/10"
            )
            if issues:
                details += f" | Issues: {'; '.join(issues[:3])}"

            return CheckResult("semantic_match", passed, avg_score, details)

        except Exception as e:
            _log.warning("Stills fallback check failed: %s", e)
            return CheckResult("semantic_match", True, 0.5,
                               f"Check failed (fail-open): {str(e)[:80]}")

        finally:
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
# Keyframe gate — screens stills BEFORE paid image-to-video
# ---------------------------------------------------------------------------

def validate_keyframe(
    image: str,
    description: str,
    narration: str = "",
) -> tuple[bool, dict]:
    """Validate a still keyframe with Gemini before spending on i2v.

    A keyframe with broken anatomy (subject merged into the bed, extra limbs)
    or baked-in legible text seeds every image-to-video attempt with the same
    defect — rejecting the image is far cheaper than burning video credits
    and quality-gate retries downstream.

    Args:
        image: Local path or http(s) URL of the keyframe image. URLs are
            downloaded to a temp file first (keyframes are often hosted for
            i2v providers, e.g. the nano-banana anchors).
        description: What the keyframe should depict (shot/keyframe prompt).
        narration: Optional narration for the shot — extra context for
            subject matching.

    Returns:
        (passed, detail) where detail contains "scores" (1-10 per criterion),
        "issues" (list of strings), and "passed". On a skipped check (no API
        key / SDK) detail contains "skipped"; on a fail-closed error it
        contains "error".

    Failure semantics match the clip check: errors fail closed unless
    QC_FAIL_OPEN=1; a missing GOOGLE_API_KEY warns loudly once and skips.
    """
    try:
        import google.generativeai as genai
    except ImportError:
        _warn_no_key_once("google-generativeai not installed")
        return True, {"passed": True,
                      "skipped": "google-generativeai not installed"}

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        _warn_no_key_once(f"keyframe QC for {image}")
        return True, {"passed": True, "skipped": "no GOOGLE_API_KEY"}

    tmp_path: Path | None = None
    uploaded = None
    try:
        # URL keyframes (hosted anchors for i2v providers) → temp file.
        if str(image).startswith(("http://", "https://")):
            import requests

            resp = requests.get(image, timeout=60)
            resp.raise_for_status()
            suffix = Path(str(image).split("?")[0]).suffix or ".png"
            fd, tmp_name = tempfile.mkstemp(prefix="_qg_keyframe_",
                                            suffix=suffix)
            os.close(fd)
            tmp_path = Path(tmp_name)
            tmp_path.write_bytes(resp.content)
            local_path = tmp_path
        else:
            local_path = Path(image)
            if not local_path.is_file():
                raise FileNotFoundError(f"Keyframe not found: {image}")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            GEMINI_QC_MODEL,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )

        uploaded = genai.upload_file(path=str(local_path),
                                     display_name=local_path.stem)
        deadline = time.time() + UPLOAD_TIMEOUT_S
        while uploaded.state.name == "PROCESSING":
            if time.time() > deadline:
                raise TimeoutError(
                    f"Gemini upload still PROCESSING after {UPLOAD_TIMEOUT_S}s")
            time.sleep(1)
            uploaded = genai.get_file(uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(
                f"Gemini upload ended in state {uploaded.state.name}")

        narration_note = (
            f'\nNarration for the shot: "{narration}"' if narration else ""
        )
        prompt = f"""You are a strict quality-control reviewer for a single illustrated keyframe that will be animated into a video clip (image-to-video). A flawed keyframe poisons every clip generated from it, so be critical.

{_STYLE_NOTE}

The keyframe should depict: "{description}"{narration_note}

Score each criterion from 1 (terrible) to 10 (perfect):

1. "anatomy_plausible": Is every human and object physically plausible? Subjects must rest ON surfaces — a patient lies ON a bed or table, not sunk into or merged with it; bodies have the correct number of limbs and fingers; faces are not deformed; furniture and architecture have coherent, possible geometry.

2. "subject_match": Does the image actually contain the subjects, setting, and framing the description asks for?

3. "text_free": Is the image free of baked-in LEGIBLE text, captions, signage, watermarks, or lettering? Indistinct impressionistic marks that merely suggest text are fine. 10 = nothing readable anywhere; 1-3 = clearly readable words.

Return ONLY JSON:
{{"anatomy_plausible": N, "subject_match": N, "text_free": N, "issues": ["short concrete description of each problem"]}}
"""

        response = model.generate_content([uploaded, prompt])
        result = _parse_json_response(response.text)

        scores = {
            "anatomy_plausible": int(result.get("anatomy_plausible", 0)),
            "subject_match": int(result.get("subject_match", 0)),
            "text_free": int(result.get("text_free", 0)),
        }
        issues = [str(i) for i in (result.get("issues") or [])]

        passed = (
            scores["anatomy_plausible"] >= KEYFRAME_ANATOMY_MIN
            and scores["subject_match"] >= KEYFRAME_SUBJECT_MATCH_MIN
            and scores["text_free"] >= KEYFRAME_TEXT_FREE_MIN
        )
        detail = {"passed": passed, "scores": scores, "issues": issues}
        if not passed:
            _log.warning("Keyframe QC FAILED for %s: %s", image, detail)
        return passed, detail

    except Exception as e:
        if _fail_open():
            _log.warning("Keyframe QC errored (%s) — QC_FAIL_OPEN=1, passing: %s",
                         e, image)
            return True, {"passed": True, "error": str(e)[:200],
                          "skipped": "fail-open"}
        _log.warning("Keyframe QC errored for %s — failing closed: %s", image, e)
        return False, {"passed": False, "error": str(e)[:200]}

    finally:
        if uploaded is not None:
            try:
                genai.delete_file(uploaded.name)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _parse_json_response(text: str) -> dict:
    """Parse a Gemini JSON response, tolerating markdown code fences."""
    text = (text or "").strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


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
        visual_spec: VisualSpec from scored script (any object with
            .description; .narration is also read when present)
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
