"""Motion Director — author ai_motion at hand-crafted quality.

The deterministic ``scene_library.default_camera_phrase`` can only emit generic
camera grammar ("slow dolly in"). Hand-authored documentary motion is far
richer: it follows a teachable SIX-LAYER formula —

  1. SUBJECT ACTION      — one concrete thing that moves in the frame
  2. MOTIVATED CAMERA    — the emotional camera grammar, stated specifically
  3. TEXTURAL QUALIFIER  — adverbs that set feeling + pace
  4. SECONDARY ATMOSPHERE— small living light/dust/shadow detail
  5. OBJECT-PERMANENCE   — the per-scene rigidity/no-morph law
  6. COMPOSITIONAL ANCHOR— ties the move to the frame + caption space

Layers 1/4/6 depend on what is actually IN the shot, so a lookup table can
never reach them. This module closes the gap the way you match any expert
output: codify the craft (MOTION_CRAFT_RUBRIC), few-shot on the channel's own
gold-standard motions (EXEMPLARS), ground in the scene's content, and let the
LLM synthesize. ``scene_library`` stays the offline / no-key floor.

Placement: this is a SCRIPT-stage authoring tool — it fills a segment's
``ai_motion`` so a human reviews prose (exactly how the channel already works),
not a hidden plan-time default. ``visual_router`` keeps using the deterministic
floor when a script still ships with blank motion.

Usage:
    from lib.motion_director import author_motion
    motion = author_motion({
        "narration": "...", "shot_prompt": "...",
        "editorial_intent": "emotional_impact", "pacing": "crisis",
    })
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from lib import scene_library

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"  # repo standard (matches script_review/quality_gate)


# ---------------------------------------------------------------------------
# The craft, codified — the six layers + the channel's hard constraints.
# ---------------------------------------------------------------------------
MOTION_CRAFT_RUBRIC = """\
You are the MOTION DIRECTOR for an animated graphic-novel documentary. You write
the ai_motion prompt for ONE shot — the instruction that tells an image-to-video
model how the camera and the frame should MOVE over a single still keyframe.

Your job is NOT to redescribe the scene (the keyframe already fixes what is in
frame). Your job is to author MOTION at the level of a master documentary
director. Great motion follows SIX layers — compose them into one or two flowing
present-tense sentences, separated by semicolons:

1. SUBJECT ACTION — the one concrete thing that moves in the frame, taken from
   THIS shot's content. If the subject is inert (a machine, a screen, paper),
   it holds dead-still or barely breathes and the camera + atmosphere carry the
   life. If a person or mechanism is present, give it ONE specific, physical
   action beat (eyes snap open and the body tenses; the needle is driven across
   the dial and slams the stop; the cam rotates and the lever snaps).

2. MOTIVATED CAMERA — the camera move, motivated by the beat's EMOTION, never
   decoration. Start from the emotional grammar you are given (the suggested
   move) but state it specifically and tie it to the frame: "a firm push-in to
   her face", "a slow smooth orbital arc with the machine locked dead-center",
   "a slow macro push to the contact point". A static-hold is a real, strong
   choice for grief/gravity — use it and let layers 1+4 carry the shot. But do
   NOT hold static on a beat that SHOWS a mechanism working (a cam rotating, a
   lever snapping, gears turning): let the part actuate and push slowly — a
   macro move to the moving contact point — so the motion reads. Static is for
   inert subjects (a diagram, a still label, a fixed object), not for actuation.

3. TEXTURAL QUALIFIER — the adverbs that set FEELING and PACE: "slow,
   unsettling", "unhurried, routine", "firm", "almost imperceptible", "steady".
   This is where dread / calm / urgency live.

4. SECONDARY ATMOSPHERE — one or two small living details so a near-static frame
   breathes, drawn from the scene's light and materials: light sliding / flaring
   / pulsing, dust drifting and settling, a phosphor shimmer, a status light
   blinking, shadows breathing imperceptibly.

5. OBJECT-PERMANENCE LAW — reinforce the discipline for THIS scene: geometry
   stays rigid and dimensionally constant; nothing appears, vanishes, or
   transforms; every object exists exactly once; space revealed by camera
   movement is a bare continuation of the established scene; the subject never
   changes form. Phrase it for what is actually in this shot.

6. COMPOSITIONAL ANCHOR — tie the move to the frame and continuity: what the
   move arrives at ("the distant door slowly growing nearer"), a locked framing
   ("dead-center"), and — WHEN the shot reserves caption space — keep the lower
   third still and dark for the caption.

HARD CHANNEL CONSTRAINTS (every motion must obey):
- Graphic-novel illustration channel. Do NOT include style or medium words
  (inked, painterly, palette, grain) — style is applied separately. Write MOTION
  only, not look.
- NO legible text, numbers, or readouts that resolve or move — any screen/label
  stays indistinct. When words matter they live in a separate caption layer.
- Anonymous illustrated figures only; faces may show emotion, never a real
  person's likeness.
- Strict period accuracy when a period is given (e.g. 1985: CRT, beige metal,
  no modern hardware, no flatscreens/LEDs).
- ONE continuous shot generated from a SINGLE keyframe: no cuts, no montage, no
  second camera setup — a composition that reads in one moving take.

Return ONLY JSON: {"motion": "<the ai_motion prompt>", "rationale": "<one line: the emotional read and why this camera>"}
"""


# ---------------------------------------------------------------------------
# Gold-standard exemplars — the channel's own hand-authored motions. These
# teach the target quality AND the house voice. Keep them representative of the
# range: traveling-follow, static-subject/moving-camera, intimate push,
# violent subject action, dramatic-irony depth.
# ---------------------------------------------------------------------------
EXEMPLARS: list[dict[str, str]] = [
    {
        "context": "intent=establishing_atmosphere; pacing=establishing. "
                   "Narration: a woman arrives for a routine 1985 radiation "
                   "treatment. Keyframe: a lone elderly woman seen from behind "
                   "walks away down a long empty hospital corridor toward a "
                   "distant treatment-room door.",
        "motion": "she walks steadily away from the camera down the corridor, "
                  "unhurried, routine; the camera follows her at walking pace in "
                  "one continuous take, the distant treatment-room door slowly "
                  "growing nearer; fluorescent light pools sliding over her as "
                  "she passes beneath each fixture; the corridor's geometry stays "
                  "rigid and constant; OBJECT PERMANENCE: nothing appears, "
                  "vanishes, or changes anywhere in the corridor — every object "
                  "stays exactly where it started, and stretches of corridor "
                  "revealed by the camera are bare walls and floor only",
    },
    {
        "context": "intent=building_trust; pacing=establishing. Narration: the "
                   "machine is trusted completely, even when it lies. Keyframe: "
                   "the Therac-25 looming over an empty treatment table, cold "
                   "clinical light.",
        "motion": "the machine is COMPLETELY STILL — a fixed, rigid object with "
                  "no moving parts; the camera alone moves, a slow smooth orbital "
                  "arc around the room with the machine locked dead-center in "
                  "frame, perspective shifting gently as the camera circles; a "
                  "single small status light blinks; deep shadows hold their "
                  "shapes; no zoom, no tilt, the machine never changes form",
    },
    {
        "context": "intent=emotional_impact; pacing=crisis. Narration: the dose "
                   "delivered was off the scale. Keyframe: a close insert of an "
                   "analog dose meter, its needle near the end stop.",
        "motion": "the black needle jerks off rest and is driven violently across "
                  "the dial, slamming hard against the end stop and quivering "
                  "there, pinned, unable to read any higher; the amber pilot lamp "
                  "flares and pulses as it pegs; a slow, unsettling push in toward "
                  "the pinned needle, faint dust drifting through the cold "
                  "highlight, the deep shadows breathing almost imperceptibly",
    },
    {
        "context": "intent=institutional_critique; pacing=crisis. Narration: the "
                   "terminal reported the treatment delivered normally. Keyframe: "
                   "foreground a calm amber control terminal; far through a "
                   "doorway, the patient on the table. A caption will be burned "
                   "over the lower third.",
        "motion": "the amber cursor blinks on, steady and serene, a soft pulse of "
                  "light crossing the placid screen; far beyond the doorway the "
                  "small figure on the table stiffens and flinches once; a very "
                  "slow unsettling push past the calm screen toward the doorway, "
                  "the lower third held still and dark for the caption",
    },
]


def _exemplar_block() -> str:
    return "\n\n".join(
        f"EXAMPLE {i + 1}\nSCENE: {ex['context']}\nMOTION: {ex['motion']}"
        for i, ex in enumerate(EXEMPLARS)
    )


def _seed_grammar(editorial_intent: str, directors_move: str, content: str = "") -> str:
    """The emotional camera grammar to seed layer 2 (from scene_library).

    ``content`` (narration + keyframe text) refines the bimodal
    technical_explanation seed: a mechanism actuating gets a push + explicit
    actuation guidance instead of a static-hold nudge.
    """
    move = scene_library.default_camera_move(
        editorial_intent=editorial_intent or None,
        directors_move=directors_move or None,
        content=content or None,
    )
    phrase = scene_library.default_camera_phrase(
        editorial_intent=editorial_intent or None,
        directors_move=directors_move or None,
        content=content or None,
    )
    bits = []
    if phrase:
        bits.append(f"suggested camera move: {phrase}")
    elif move == "static" or editorial_intent in ("emotional_impact",):
        bits.append("suggested camera move: a static hold (let the subject "
                    "action and atmosphere carry the shot)")
    if (editorial_intent == "technical_explanation"
            and scene_library.is_mechanism_action(content)):
        bits.append("this beat SHOWS a mechanism working — the actuation IS the "
                    "subject action: let the part move (rotate / snap / drive / "
                    "turn) and push slowly, a macro move to the moving contact "
                    "point; do NOT hold static")
    if directors_move:
        desc = scene_library.DIRECTORS_MOVES.get(directors_move, {}).get("description")
        if desc:
            bits.append(f"director's move ({directors_move}): {desc}")
    return "; ".join(bits) or "suggested camera move: choose the one the emotion motivates"


def _build_prompt(ctx: dict[str, Any]) -> str:
    intent = ctx.get("editorial_intent") or ""
    move = ctx.get("directors_move") or ""
    fields = [
        ("editorial_intent", intent or "(none)"),
        ("pacing", ctx.get("pacing") or "(none)"),
        ("narration (heard over the shot)", (ctx.get("narration") or "").strip() or "(none)"),
        ("keyframe / shot content", (ctx.get("shot_prompt") or ctx.get("description") or "").strip() or "(none)"),
        ("emotional tone", (ctx.get("ai_style") or "").strip() or "(none)"),
        ("subject / location", ctx.get("subject") or ctx.get("location_id") or "(none)"),
        ("reserves a burned caption (keep lower third still)", "yes" if ctx.get("has_caption") else "no"),
    ]
    scene_block = "\n".join(f"- {k}: {v}" for k, v in fields)
    content = " ".join(filter(None, [
        ctx.get("narration", ""), ctx.get("shot_prompt", ""), ctx.get("description", ""),
    ]))
    return (
        f"{MOTION_CRAFT_RUBRIC}\n\n"
        f"GOLD-STANDARD EXEMPLARS (match this quality and voice):\n\n{_exemplar_block()}\n\n"
        f"NOW AUTHOR THE MOTION FOR THIS SHOT.\n"
        f"Emotional camera grammar to start from — {_seed_grammar(intent, move, content)}\n\n"
        f"SCENE:\n{scene_block}\n"
    )


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    return json.loads(text)


def author_motion(
    scene_context: dict[str, Any],
    model: str = _DEFAULT_MODEL,
) -> str:
    """Author a hand-crafted-quality ai_motion for one shot.

    ``scene_context`` keys (all optional but more = better): narration,
    shot_prompt (or description), editorial_intent, directors_move, pacing,
    ai_style, subject/location_id, has_caption.

    Falls back to ``scene_library.default_camera_phrase`` (the deterministic
    floor) when there is no API key, the SDK is missing, or the call errors —
    so this is always safe to call. Returns '' only when even the floor is empty
    (a static-hold beat), which the planner treats as no motion clause.
    """
    floor = scene_library.default_camera_phrase(
        editorial_intent=scene_context.get("editorial_intent") or None,
        directors_move=scene_context.get("directors_move") or None,
    )

    if os.environ.get("SCENE_LIBRARY_MOTION_DIRECTOR", "1") == "0":
        return floor

    try:
        import google.generativeai as genai
    except ImportError:
        _log.warning("motion_director: google-generativeai not installed — using deterministic floor")
        return floor

    api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        _log.warning("motion_director: no GOOGLE_API_KEY/GEMINI_API_KEY — using deterministic floor")
        return floor

    try:
        genai.configure(api_key=api_key)
        gem = genai.GenerativeModel(
            model,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,  # motion authoring wants some range, not determinism
                # gemini-2.5-flash spends "thinking" tokens against this budget
                # before emitting the JSON; 1024 truncated it mid-string. Give
                # thinking + the short motion JSON ample room (repo uses 8192).
                max_output_tokens=4096,
                response_mime_type="application/json",
            ),
        )
        resp = gem.generate_content(_build_prompt(scene_context))
        out = _parse_json(resp.text)
        motion = (out.get("motion") or "").strip()
        if motion:
            return motion
        _log.warning("motion_director: empty motion returned — using floor")
        return floor
    except Exception as exc:  # noqa: BLE001
        _log.warning("motion_director: authoring failed (%s) — using floor", str(exc)[:160])
        return floor


def author_for_segment(seg: Any, model: str = _DEFAULT_MODEL) -> str:
    """Author motion for a scored-script Segment (convenience wrapper)."""
    vis = seg.visual
    shot_prompt = ""
    if getattr(vis, "shots", None):
        shot_prompt = getattr(vis.shots[0], "ai_prompt", "") or ""
    shot_prompt = shot_prompt or getattr(vis, "ai_prompt", "") or ""
    return author_motion({
        "narration": getattr(seg, "narration", ""),
        "shot_prompt": shot_prompt,
        "description": getattr(vis, "description", ""),
        "editorial_intent": getattr(seg, "editorial_intent", None) or getattr(vis, "editorial_intent", None),
        "directors_move": getattr(seg, "directors_move", None),
        "pacing": getattr(seg, "pacing", None),
        "ai_style": getattr(vis, "ai_style", None),
        "location_id": getattr(vis, "location_id", None),
        "has_caption": bool(getattr(vis, "text_overlay", None)),
    }, model=model)


def propose_for_script(
    script_path: str,
    only_blank: bool = True,
    seg_ids: list[str] | None = None,
    model: str = _DEFAULT_MODEL,
) -> list[dict[str, str]]:
    """Propose ai_motion for a scored script's AI-video segments.

    Review-oriented (does NOT write back — you paste what you like, matching the
    hand-authoring workflow). ``only_blank`` skips segments that already have
    motion; ``seg_ids`` restricts to specific segments. Returns a list of
    {id, intent, proposed, existing} dicts.
    """
    from lib.scored_script import load_scored_script

    script = load_scored_script(script_path)
    out: list[dict[str, str]] = []
    for seg in script.segments:
        if seg_ids and seg.id not in seg_ids:
            continue
        if getattr(seg.visual, "type", "") != "ai_video":
            continue
        existing = seg.visual.ai_motion or ""
        if only_blank and existing.strip():
            continue
        out.append({
            "id": seg.id,
            "intent": seg.visual.editorial_intent or seg.editorial_intent or "",
            "existing": existing,
            "proposed": author_for_segment(seg, model=model),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m lib.motion_director",
        description="Author ai_motion at hand-crafted quality for review (paste what you like).",
    )
    parser.add_argument("script", help="Path to scored_script.yaml")
    parser.add_argument("--all", action="store_true",
                        help="Propose for every AI-video segment (default: only blank ai_motion)")
    parser.add_argument("--seg", help="Comma-separated segment ids to restrict to")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    args = parser.parse_args(argv)

    # Load .env (GOOGLE_API_KEY) the same way every repo entry point does.
    try:
        import tools.base_tool  # noqa: F401
    except ImportError:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    seg_ids = [s.strip() for s in args.seg.split(",")] if args.seg else None
    proposals = propose_for_script(args.script, only_blank=not args.all,
                                   seg_ids=seg_ids, model=args.model)
    if not proposals:
        print("No segments to propose (use --all to include segments that already have motion).")
        return 0
    for p in proposals:
        print(f"\n=== {p['id']}  (intent: {p['intent']}) ===")
        if p["existing"]:
            print(f"  existing: {p['existing']}")
        print(f"  ai_motion: \"{p['proposed']}\"")
    print(f"\n{len(proposals)} proposal(s). Review and paste the ones you want into the script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
