"""Codified scene library — "The Director's Touch" as data.

This is the single source of truth for the documentary directing vocabulary the
pipeline encodes from the research doc *The Director's Touch: A Codified Scene
Library for an Animated Documentary AI Agent* (docs/research/). It turns the
acclaimed-documentary "moves" into deterministic, machine-checkable tags:

  - NARRATION_MODES   — how the visual relates to the words (Taxonomy 7)
  - DIRECTORS_MOVES   — named reveal/withholding/story moves (Taxonomy 5)
  - AUDIO_TRANSITIONS — how a scene hands off to the next (Taxonomy 6)
  - RHYTHM            — per-scene pacing nuance over the four pacing types
  - RETENTION_BEATS   — episode-structure roles (Taxonomy 8)
  - EMOTIONAL_DEFAULTS — editorial_intent → default shot/camera/music/narration

The shot-size and camera-move vocabularies are NOT re-declared here: they are
imported verbatim from ``lib.shot_prompt_builder`` so there is exactly one
definition and the JSON schemas, the validator, and this library can never
drift apart (``_scene_library_test.py`` asserts the schema enums equal these
sets).

Layer-2 prose guidance that explains WHEN to reach for each tag lives in
``skills/creative/scene-library.md``; this module is the machine half.

Design note — defaults are *suggestions, not overrides*. ``default_camera_*``
only ever fires when the author left the field blank (see visual_router), so an
intentional static-hold on a grief beat is never stomped by a mapping.
"""
from __future__ import annotations

from typing import Any

# Canonical cinematography vocabulary — one definition, imported not copied.
from lib.shot_prompt_builder import _MOVEMENT_PHRASES, _SHOT_SIZE_PHRASES

# Shot sizes (Taxonomy 2) and virtual camera moves (Taxonomy 3). Frozen views of
# the phrase maps so callers can validate enum membership without importing the
# private dicts.
SHOT_SIZES: frozenset[str] = frozenset(_SHOT_SIZE_PHRASES)
CAMERA_MOVES: frozenset[str] = frozenset(_MOVEMENT_PHRASES)


# ---------------------------------------------------------------------------
# Taxonomy 7 — narration ↔ visual relationship
# ---------------------------------------------------------------------------
# "literal" = the visual shows the words (clarity/teaching); "evocative" = the
# visual evokes the feeling, not the literal words (emotion); "none" = no
# narration over this beat (observed/direct-cinema texture). The best work
# ALTERNATES — a script that is all-literal reads like a slideshow.
NARRATION_MODES: frozenset[str] = frozenset({"literal", "evocative", "none"})


# ---------------------------------------------------------------------------
# Taxonomy 4 — pacing rhythm nuance (overlays the four pacing TYPES)
# ---------------------------------------------------------------------------
# pacing (establishing/escalation/crisis/resolution) sets the arc role; rhythm
# sets shot duration: "breath" is the deliberate near-silent held beat after a
# peak (usually paired with silence_after_s > 0).
RHYTHM: frozenset[str] = frozenset({"fast", "medium", "lingering", "breath"})


# ---------------------------------------------------------------------------
# Taxonomy 6 — scene-to-scene transitions (incl. audio-lead/trail)
# ---------------------------------------------------------------------------
# These are fully available in animation because audio is built in post. The
# audio-relative ones (j_cut/l_cut/sound_bridge) are what the freeform
# transition_in/out strings could never capture.
AUDIO_TRANSITIONS: frozenset[str] = frozenset({
    "hard_cut",        # plain cut, no bridge
    "match_cut",       # graphic match: shared shape/composition across the cut
    "match_on_action", # continuous movement across the cut
    "j_cut",           # next scene's AUDIO leads its image — anticipation
    "l_cut",           # prior audio TRAILS over the new image — emotional carry
    "sound_bridge",    # shared/continuous sound or VO glues the scenes
    "contrast_cut",    # juxtapose opposite tones for whiplash
})


# ---------------------------------------------------------------------------
# Taxonomy 8 — episode-structure / retention roles
# ---------------------------------------------------------------------------
# What job a segment does in the retention architecture. The first ~15s must
# carry cold_open + value_proposition; a long episode needs pattern_interrupt
# beats every 30–90s and a re_hook past the ~7–8 min mark; segments end on
# cliffhanger beats; payoff closes an open loop.
RETENTION_BEATS: frozenset[str] = frozenset({
    "cold_open",          # 0–5s attention grab
    "value_proposition",  # 5–15s payoff promise (what the viewer gets)
    "commitment_hook",    # 15–30s open the information gap the episode resolves
    "pattern_interrupt",  # reset attention (angle/graphic/sound/POV change)
    "re_hook",            # mid-video re-hook past the ~7–8 min valley
    "cliffhanger",        # segment ends on calibrated escalation
    "payoff",             # closes an open loop / fires a planted tease
    "none",
})


# ---------------------------------------------------------------------------
# Taxonomy 5 — named "director's moves" (reveal & withholding architecture)
# ---------------------------------------------------------------------------
# Each move carries the trigger (when the script supports it) and, where the
# move implies a virtual-camera grammar, a ``camera_move`` hint (an enum key in
# CAMERA_MOVES) the planner can apply when the author left motion blank.
DIRECTORS_MOVES: dict[str, dict[str, str]] = {
    "genre_bait_and_switch": {
        "description": "Establish one tone, then deliberately modulate to "
                       "another (Three Identical Strangers: 80s-comedy → "
                       "identity thriller).",
        "trigger": "The script has a tonal turn or dark second half.",
    },
    "acclimatize_dont_ambush": {
        "description": "Plant foreboding teases so a dark turn is earned, not a "
                       "cheap shock — never 'you won't believe what happened "
                       "next'.",
        "trigger": "A dark/tragic turn is coming; seed it beats earlier.",
    },
    "recontextualized_replay": {
        "description": "Reuse the same image/footage after a reveal so its "
                       "meaning inverts.",
        "trigger": "An earlier visual gains new meaning post-reveal.",
        "camera_move": "dolly_out",  # pull back to re-frame what it now means
    },
    "chronological_reveal_ladder": {
        "description": "Tell A→Z, each escalation depending on the prior one "
                       "(Wild Wild Country).",
        "trigger": "Causality matters more than a time-jump structure.",
    },
    "withhold_judgment": {
        "description": "Give opposing accounts equal weight; let the audience "
                       "judge (point/counterpoint, different cue per side). Use "
                       "to SEQUENCE true information, never to distort it.",
        "trigger": "Genuinely contested accounts exist.",
    },
    "acts_within_acts": {
        "description": "Fractal structure — every act AND every segment gets "
                       "its own strong opening and cliffhanger ending.",
        "trigger": "Always available; structures each act.",
    },
    "calibrated_cliffhanger": {
        "description": "End a segment on escalating threat/stakes without "
                       "prematurely resolving or making a character unlikable.",
        "trigger": "A segment/act boundary needs forward pull.",
        "camera_move": "dolly_in",  # lean into the unresolved threat
    },
    "escalating_weirdness": {
        "description": "Each segment out-escalates the last, with a concrete "
                       "end point as the anchor.",
        "trigger": "Story has a mounting series of revelations.",
    },
    "delayed_antagonist_reveal": {
        "description": "Build a figure folklorically through others' accounts "
                       "before they appear, so their entrance lands.",
        "trigger": "A central figure can be introduced via others first.",
        "camera_move": "dolly_in",
    },
    "cold_open_inversion": {
        "description": "Open on a near-end moment, then rewind (Tiger King "
                       "jailhouse open).",
        "trigger": "The ending image is a stronger hook than the beginning.",
    },
    "visual_anchor_before_context": {
        "description": "Johnny Harris rule — show the striking image first "
                       "('look at this thing!'), THEN explain it.",
        "trigger": "A beat has a strong image that should precede its context.",
        "camera_move": "dolly_in",
    },
}
DIRECTORS_MOVE_NAMES: frozenset[str] = frozenset(DIRECTORS_MOVES)


# ---------------------------------------------------------------------------
# Stage 2 — emotional-trigger → default shot grammar
# ---------------------------------------------------------------------------
# Keyed by the existing ``editorial_intent`` enum (lib.scored_script). Each maps
# to default shot_size + camera_move (enum keys) + rhythm + narration_mode +
# music_cue (a MusicSpec state). These encode the doc's mappings — tension →
# slow push-in + lingering + build; revelation → pull-back + cue change; grief
# → static hold + drop-to-silence; teaching → medium + literal + steady.
EMOTIONAL_DEFAULTS: dict[str, dict[str, str]] = {
    "establishing_atmosphere": {
        "shot_size": "establishing", "camera_move": "dolly_in",
        "rhythm": "lingering", "narration_mode": "evocative",
        "music_cue": "playing",
    },
    "building_trust": {
        "shot_size": "medium", "camera_move": "dolly_in",
        "rhythm": "medium", "narration_mode": "literal",
        "music_cue": "continue",
    },
    "technical_explanation": {
        "shot_size": "medium", "camera_move": "static",
        "rhythm": "medium", "narration_mode": "literal",
        "music_cue": "continue",
    },
    "escalation": {
        "shot_size": "medium_close", "camera_move": "dolly_in",
        "rhythm": "fast", "narration_mode": "evocative",
        "music_cue": "change",
    },
    "crisis_moment": {
        "shot_size": "close_up", "camera_move": "handheld",
        "rhythm": "fast", "narration_mode": "evocative",
        "music_cue": "change",
    },
    "pattern_recognition": {
        "shot_size": "insert", "camera_move": "dolly_in",
        "rhythm": "medium", "narration_mode": "literal",
        "music_cue": "continue",
    },
    "emotional_impact": {
        # grief/gravity — static hold + prepared silence (drop to silence)
        "shot_size": "close_up", "camera_move": "static",
        "rhythm": "breath", "narration_mode": "evocative",
        "music_cue": "fade_out",
    },
    "institutional_critique": {
        "shot_size": "medium", "camera_move": "dolly_in",
        "rhythm": "medium", "narration_mode": "literal",
        "music_cue": "continue",
    },
    "resolution": {
        # the reveal/pull-back — show the bigger picture
        "shot_size": "wide", "camera_move": "dolly_out",
        "rhythm": "lingering", "narration_mode": "evocative",
        "music_cue": "change",
    },
    "call_to_action": {
        "shot_size": "medium", "camera_move": "dolly_in",
        "rhythm": "medium", "narration_mode": "literal",
        "music_cue": "change",
    },
    "chapter_transition": {
        "shot_size": "establishing", "camera_move": "dolly_out",
        "rhythm": "breath", "narration_mode": "none",
        "music_cue": "change",
    },
}


# ---------------------------------------------------------------------------
# Mechanism / actuation detection (refines the technical_explanation default)
# ---------------------------------------------------------------------------
# technical_explanation is bimodal: a static hold is right for an INERT subject
# (a diagram, a label, a still object), but a beat that SHOWS a mechanism working
# — a cam rotating, a lever snapping, gears turning — must push so the actuation
# reads; the motion is the whole point. The intent alone can't tell which, so the
# shot CONTENT (narration + keyframe prompt) is inspected for these cues.
MECHANISM_CUES: frozenset[str] = frozenset({
    "rotat", "camshaft", "cam ", "lever", "gear", "microswitch", "switch",
    "plunger", "solenoid", "piston", "spring", "actuat", "interlock", "relay",
    "valve", "motor", "hinge", "latch", "toggle", "linkage", "mechanis",
    "ratchet", "escapement", "gearbox", "moving part", "snaps", "snapping",
    "contact point",
})


def is_mechanism_action(text: str | None) -> bool:
    """True if the shot content describes a mechanism actuating (a moving part)."""
    if not text:
        return False
    t = text.lower()
    return any(cue in t for cue in MECHANISM_CUES)


# ---------------------------------------------------------------------------
# Membership helpers (used by the structural validator)
# ---------------------------------------------------------------------------

def is_known_narration_mode(value: str | None) -> bool:
    return value is None or value in NARRATION_MODES


def is_known_directors_move(value: str | None) -> bool:
    return value is None or value in DIRECTORS_MOVE_NAMES


def is_known_audio_transition(value: str | None) -> bool:
    return value is None or value in AUDIO_TRANSITIONS


def is_known_rhythm(value: str | None) -> bool:
    return value is None or value in RHYTHM


def is_known_retention_beat(value: str | None) -> bool:
    return value is None or value in RETENTION_BEATS


# ---------------------------------------------------------------------------
# Default-derivation helpers (used by the planner)
# ---------------------------------------------------------------------------

def emotional_default(editorial_intent: str | None) -> dict[str, str]:
    """Default shot grammar for an editorial_intent ({} if unknown/None)."""
    return dict(EMOTIONAL_DEFAULTS.get(editorial_intent or "", {}))


def default_camera_move(
    editorial_intent: str | None = None,
    directors_move: str | None = None,
    pacing: str | None = None,
    content: str | None = None,
) -> str | None:
    """Resolve a default camera_move ENUM KEY (in CAMERA_MOVES), or None.

    A director's-move camera hint wins (recontextualized_replay → pull-back),
    otherwise the editorial_intent's emotional default applies. ``content`` (the
    shot's narration/keyframe text) refines the bimodal ``technical_explanation``
    default: a mechanism actuating gets a slow push instead of a static hold.
    """
    if directors_move:
        hint = DIRECTORS_MOVES.get(directors_move, {}).get("camera_move")
        if hint:
            return hint
    move = EMOTIONAL_DEFAULTS.get(editorial_intent or "", {}).get("camera_move")
    # technical_explanation is static for inert subjects, but a mechanism that
    # ACTUATES must push so the motion reads — the actuation is the point.
    if (editorial_intent == "technical_explanation" and move == "static"
            and is_mechanism_action(content)):
        return "dolly_in"
    return move or None


def default_camera_phrase(
    editorial_intent: str | None = None,
    directors_move: str | None = None,
    pacing: str | None = None,
    content: str | None = None,
) -> str:
    """Natural-language camera phrase for the default move ('' if none).

    Returns a phrase like 'slow dolly in toward subject' suitable for an
    ``ai_motion`` field. 'static' resolves to '' — a locked-off camera needs no
    motion clause and the planner treats blank as static anyway. ``content``
    refines the technical_explanation default (mechanism → push).
    """
    move = default_camera_move(editorial_intent, directors_move, pacing, content)
    if not move or move == "static":
        return ""
    return _MOVEMENT_PHRASES.get(move, "")


def default_shot_language(
    editorial_intent: str | None = None,
    directors_move: str | None = None,
    pacing: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """Full default tag set for a scene given its emotional/structural tags.

    Returns a dict with shot_size, camera_move, rhythm, narration_mode,
    music_cue (any subset that is known). A director's-move camera hint and the
    technical_explanation mechanism refinement both apply to camera_move.
    """
    out = dict(EMOTIONAL_DEFAULTS.get(editorial_intent or "", {}))
    move = default_camera_move(editorial_intent, directors_move, pacing, content)
    if move:
        out["camera_move"] = move
    return out
