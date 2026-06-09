"""Gemini-powered visual validation for animated diagrams.

Uploads the video AND extracts the final frame as a separate image,
then sends both to Gemini for layout analysis.  The final frame is
critical because Gemini's 1 FPS video sampling misses overlap issues
that only appear when all elements are on screen together.

Originally written for Manim animations; now also the critic for the
hand-sketched matplotlib diagrams (lib.sketch_diagrams / lib.diagram_codegen).
The rubric is a parameter because the two styles invert expectations: in a
Manim clip, wobbling lines would be an encoding artifact; in a sketch diagram
the wobble, halftone, and duotone are the WHOLE POINT and must not be flagged.
Pass SKETCH_VALIDATION_PROMPT (optionally with extra context prepended) for
sketch diagrams; the default remains the original Manim rubric, so existing
callers are unchanged.

Returns pass/fail with specific issues and suggestions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_VALIDATION_PROMPT = """\
You are reviewing an animated diagram for a documentary video.
I'm sending you the video AND a screenshot of the FINAL FRAME
(when all elements are visible). Check BOTH carefully.

Check for these issues:

1. **TEXT OVERLAP** — Are any text labels overlapping each other?
   Check the FINAL FRAME especially — this is where all elements
   are on screen and overlap is most likely.

2. **TEXT CUTOFF** — Is any text cut off at the edges of the frame?
   NOTE: Text appearing character-by-character during a Write()
   animation is NORMAL — that is the animation playing, not cutoff.

3. **READABILITY** — Is all text large enough to read at 1080p?
   Source citations at the bottom can be small (11-13px is OK).

4. **ELEMENT OVERLAP** — Do any boxes, lines, arrows, or shapes
   overlap incorrectly in the FINAL FRAME?

5. **ENCODING ARTIFACTS** — Are there garbled characters, /n literals,
   mojibake, or question marks replacing real text?

6. **LAYOUT BALANCE** — Is the overall layout balanced? Are elements
   well-spaced? Is there wasted empty space or cramped areas?

Return ONLY valid JSON:
{
  "passed": true,
  "issues": [],
  "suggestions": []
}

If there ARE issues:
{
  "passed": false,
  "issues": [
    {"type": "text_overlap", "description": "X overlaps Y in the final frame", "severity": "high"}
  ],
  "suggestions": ["Move X up by 0.5 units to clear Y"]
}

Severity: "high" = must fix, "medium" = should fix, "low" = cosmetic.
"""

_JSON_SHAPE = """\
Return ONLY valid JSON:
{
  "passed": true,
  "issues": [],
  "suggestions": []
}

If there ARE issues:
{
  "passed": false,
  "issues": [
    {"type": "text_overlap", "description": "X overlaps Y in the final frame", "severity": "high"}
  ],
  "suggestions": ["Move X up by 0.5 units to clear Y"]
}

Severity: "high" = must fix, "medium" = should fix, "low" = cosmetic.
Fail (passed=false) ONLY on high/medium issues; low-severity cosmetic notes
alone should still pass.
"""

# Rubric for the hand-sketched diagrams. The style cues that the Manim rubric
# would read as defects (jittering lines, dotted texture, two-color grading)
# are declared EXPECTED up front, and a dead-time check is added because
# generated scenes — unlike hand-tuned Manim — often misjudge phase pacing.
SKETCH_VALIDATION_PROMPT = """\
You are reviewing a HAND-SKETCHED animated diagram for a graphic-novel-styled
documentary. I'm sending you the video AND a screenshot of the FINAL FRAME
(when all elements are visible). Check BOTH carefully.

EXPECTED STYLE — do NOT flag any of these as issues:
- wobbly, hand-inked lines that gently jitter/"boil" between frames
  (deliberate hand-drawn-animation look, NOT an encoding artifact)
- a casual handwriting font for ALL text
- a navy + amber two-color (duotone) palette on a dark indigo gradient
  background with a faint halftone dot field, plus film grain

Check for these ACTUAL issues:

1. **TEXT OVERLAP** — Are any text labels overlapping each other or sitting
   on top of diagram elements so they are hard to read? Check the FINAL
   FRAME especially.

2. **EDGE CLIPPING** — Is any text or diagram element cut off at the edges
   of the frame? (Elements deliberately animating in/out across an edge
   are fine; settled elements must be fully inside.)

3. **READABILITY** — Is all text large enough to read at 1080p? The
   handwriting font is expected; flag only text that is genuinely too
   small or cramped. Source citations at the bottom can be small.

4. **ELEMENT OVERLAP** — Do boxes, arrows, bars, or shapes collide in ways
   that make the diagram confusing (not deliberate emphasis)?

5. **DEAD TIME** — Are there stretches of roughly 3+ seconds where nothing
   appears, moves, or changes? The scene should always be building or
   emphasizing something.

6. **LAYOUT BALANCE** — Is the layout balanced? Large wasted empty regions
   or one cramped corner?

""" + _JSON_SHAPE


def validate_animation(
    video_path: str | Path,
    max_retries: int = 1,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Validate an animated diagram with Gemini vision.

    Uploads both the video and a final-frame screenshot for thorough
    layout analysis. `prompt` selects the rubric — default is the original
    Manim rubric (existing callers unchanged); pass SKETCH_VALIDATION_PROMPT
    (with any scene-specific context prepended) for sketch diagrams.

    Returns dict with: passed (bool), issues (list), suggestions (list)
    """
    video_path = Path(video_path)
    rubric = prompt or _VALIDATION_PROMPT
    if not video_path.is_file():
        return {"passed": False, "issues": [{"type": "error", "description": f"File not found: {video_path}"}], "suggestions": []}

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        _log.warning("GOOGLE_API_KEY not set — skipping validation")
        return {"passed": True, "issues": [], "suggestions": ["Validation skipped — no API key"]}

    # Extract final frame
    final_frame = video_path.parent / f"_validate_{video_path.stem}_final.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.5", "-i", str(video_path),
             "-frames:v", "1", str(final_frame)],
            capture_output=True, timeout=10,
        )
    except Exception:
        _log.warning("Failed to extract final frame from %s", video_path.name)

    # Upload to Gemini
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)

        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )

        # Upload video
        uploaded_video = genai.upload_file(path=str(video_path), display_name=video_path.stem)
        while uploaded_video.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_video = genai.get_file(uploaded_video.name)

        # Build content parts: video + final frame image + prompt
        parts = [uploaded_video]

        if final_frame.is_file():
            uploaded_frame = genai.upload_file(path=str(final_frame), display_name=f"{video_path.stem}_final_frame")
            parts.append(uploaded_frame)
            parts.append("Above: the video animation followed by a screenshot of the FINAL FRAME. " + rubric)
        else:
            parts.append(rubric)

        response = model.generate_content(parts)

        # Cleanup
        try:
            genai.delete_file(uploaded_video.name)
            if final_frame.is_file():
                genai.delete_file(uploaded_frame.name)
        except Exception:
            pass

        # Parse response
        text = response.text.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        result = json.loads(text)

    except Exception as exc:
        _log.warning("Validation failed for %s: %s", video_path.name, exc)
        result = {"passed": False, "issues": [{"type": "error", "description": str(exc)[:100]}], "suggestions": []}

    finally:
        # Cleanup temp frame
        if final_frame.is_file():
            try:
                final_frame.unlink()
            except Exception:
                pass

    return result


def validate_all(
    video_dir: str | Path,
    skip_patterns: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate all MP4 files in a directory.

    Returns dict of filename → validation result.
    """
    video_dir = Path(video_dir)
    skip = set(skip_patterns or ["partial"])
    results: dict[str, dict[str, Any]] = {}

    for mp4 in sorted(video_dir.glob("*.mp4")):
        if any(s in mp4.name for s in skip):
            continue

        name = mp4.stem
        _log.info("Validating %s...", name)
        result = validate_animation(mp4)
        results[name] = result

        status = "PASS" if result.get("passed") else "FAIL"
        _log.info("  %s: %s", name, status)
        if not result.get("passed"):
            for issue in result.get("issues", []):
                _log.info("    [%s] %s: %s",
                          issue.get("severity", "?"),
                          issue.get("type", "?"),
                          issue.get("description", ""))

        time.sleep(1)  # rate limit buffer

    passed = sum(1 for r in results.values() if r.get("passed"))
    _log.info("Validation complete: %d / %d passed", passed, len(results))

    return results
