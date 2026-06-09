"""Narration-beat leg prompts for chained video clips.

Segments longer than the provider clip cap are split into N "legs" that
CHAIN: leg N+1 is generated image-to-video from leg N's final frame. The
naive approach — render the same base prompt N times — produces the exact
failure this module exists to fix: a 30s narration arc that moves through
several beats (error appears → operators ignore it → habituation) sits over
N near-identical static clips.

derive_leg_prompts() maps the narration's beat progression onto the legs:
leg 1 establishes the scene (close to the base prompt), each later leg
describes what happens NEXT in the same continuous scene. Legs 2+ must be
phrased as continuations of visible action/camera state — the video model
sees ONLY the leg's prompt plus the previous leg's final frame, so a prompt
that re-establishes the scene fights the anchor frame and causes resets,
jump-cuts, or content drift.

Style/medium language is deliberately excluded from leg prompts: the caller
appends the segment's ai_style separately, and duplicated (or conflicting)
style words degrade i2v output.

Returns None on any LLM failure so the caller can fall back to
fallback_leg_prompts() — deterministic, no API, never fails.

Usage:
    from lib.beat_splitter import derive_leg_prompts, fallback_leg_prompts

    legs = derive_leg_prompts(seg.narration, base_prompt, n_legs=3,
                              motion=seg.visual.ai_motion,
                              total_duration_s=31.0)
    if legs is None:
        legs = fallback_leg_prompts(base_prompt, n_legs=3)
"""
from __future__ import annotations

import json
import logging
import os
import re

_log = logging.getLogger(__name__)

# Prefix for fallback continuation legs. Phrasing matters: it tells the i2v
# model the anchor frame IS the scene (no re-establishment), which is the
# best we can do without knowing the narration's beats.
_CONTINUATION_PREFIX = (
    "Continue the same scene seamlessly from the current frame; "
)

_LEG_PROMPT_TEMPLATE = """\
You are writing prompts for a chained image-to-video pipeline.

One continuous scene is rendered as {n_legs} consecutive short clips
("legs"). Leg 1 is generated from its prompt alone. Every later leg is
generated image-to-video FROM THE FINAL FRAME of the previous leg — the
video model sees ONLY that frame plus the leg's own prompt. Nothing else.

The viewer hears this narration across all {n_legs} legs:
"{narration}"

Approved base visual prompt (the scene's content):
"{base_prompt}"
{motion_line}{duration_line}
Write exactly {n_legs} prompts:

1. Leg 1 establishes the scene. Stay close to the base prompt's content.
2. Legs 2..{n_legs} describe what happens NEXT in the same continuous,
   uncut scene. Phrase them as continuations of visible action and camera
   state (e.g. "the camera continues its slow push as ..."). NEVER
   re-establish the scene or restate framing/placement details that may
   have evolved — the anchor frame already shows the scene as it now is.
   No cuts, no new angles, no scene resets, nothing appearing from nowhere.
3. Derive the beat progression from the narration arc: if the narration
   moves through distinct beats, map them in order onto the legs. If the
   narration is a single sustained beat, do NOT invent events — vary
   secondary motion and atmosphere instead (light shifting, slow drift,
   ambient movement already plausible in the scene).
4. Each prompt must be standalone-renderable: the model sees only that
   prompt and the anchor frame, so every leg needs concrete, present-tense
   visual description of subjects and motion.
5. Stay consistent with the base prompt: same subjects, same setting, same
   era. Nothing on screen may contradict it.
6. NO style or medium words (no "illustration", "photorealistic",
   "cinematic", "film grain", color-grade language). If the base prompt
   contains style words, drop them — describe content and motion only.
   The caller appends style separately.

Return ONLY a JSON array of exactly {n_legs} strings, leg 1 first.
"""


def fallback_leg_prompts(base_prompt: str, n_legs: int) -> list[str]:
    """Deterministic leg prompts when LLM derivation is unavailable.

    Leg 1 is the base prompt verbatim; legs 2+ prefix a continuation clause
    so the i2v model at least treats the anchor frame as an evolving scene
    instead of re-establishing it. No beats, but also no API and no failure
    modes — the caller's safety net when derive_leg_prompts() returns None.
    """
    if n_legs < 1:
        raise ValueError(f"n_legs must be >= 1, got {n_legs}")
    return [base_prompt] + [
        _CONTINUATION_PREFIX + base_prompt for _ in range(n_legs - 1)
    ]


def derive_leg_prompts(
    narration: str,
    base_prompt: str,
    n_legs: int,
    motion: str | None = None,
    total_duration_s: float = 0.0,
    model: str = "gemini-2.5-flash",
) -> list[str] | None:
    """Derive per-leg i2v prompts from the narration's beat progression.

    Args:
        narration: The narration spoken over the whole segment.
        base_prompt: The segment's approved visual prompt (content anchor).
        n_legs: Number of chained clips the segment is split into.
        motion: Optional camera/motion direction (segment ai_motion).
        total_duration_s: Total on-screen duration across all legs; gives
            the model pacing context (0 = unknown, omitted from prompt).
        model: Gemini model name (repo standard: gemini-2.5-flash).

    Returns:
        Exactly n_legs prompts (leg 1 establishes, legs 2+ continue), or
        None on ANY failure — the caller is contractually expected to fall
        back to fallback_leg_prompts(), so this function never raises for
        LLM/parse problems and never returns a wrong-length list.
    """
    if n_legs < 1:
        raise ValueError(f"n_legs must be >= 1, got {n_legs}")
    if n_legs == 1:
        # A single leg IS the base prompt — burning an LLM call to
        # paraphrase the approved prompt only adds drift risk.
        return [base_prompt]

    motion_line = f'\nCamera/motion direction: "{motion}"\n' if motion else "\n"
    duration_line = ""
    if total_duration_s > 0:
        per_leg = total_duration_s / n_legs
        duration_line = (
            f"Total on-screen duration: ~{total_duration_s:.0f}s across "
            f"{n_legs} legs (~{per_leg:.0f}s per leg).\n"
        )

    prompt = _LEG_PROMPT_TEMPLATE.format(
        n_legs=n_legs,
        narration=narration.strip(),
        base_prompt=base_prompt.strip(),
        motion_line=motion_line,
        duration_line=duration_line,
    )

    try:
        import google.generativeai as genai

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            _log.warning("GOOGLE_API_KEY not set — cannot derive leg prompts")
            return None

        genai.configure(api_key=api_key)
        gem = genai.GenerativeModel(
            model,
            generation_config=genai.types.GenerationConfig(
                # Slightly creative: beat phrasing benefits from variation,
                # but content must stay anchored to narration + base prompt.
                temperature=0.4,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        response = gem.generate_content(prompt)
        text = response.text.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        legs = json.loads(text)
    except Exception as exc:
        _log.warning("derive_leg_prompts failed (%d legs): %s",
                     n_legs, str(exc)[:200])
        return None

    # Contract check: exactly n_legs non-empty strings, or the caller falls
    # back. A wrong-length list would silently desync the leg chain.
    if (
        not isinstance(legs, list)
        or len(legs) != n_legs
        or not all(isinstance(p, str) and p.strip() for p in legs)
    ):
        _log.warning(
            "derive_leg_prompts: model returned %s items (need %d) — discarding",
            len(legs) if isinstance(legs, list) else type(legs).__name__, n_legs,
        )
        return None

    return [p.strip() for p in legs]
