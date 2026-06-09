"""Word Timing — turn ElevenLabs character alignments into cut-safe timings.

WHY this module exists: the pipeline is audio-first. Segments longer than a
video provider's clip cap are split into chained legs, and today those legs
are EQUAL time slices — which means a seam can land mid-word or mid-sentence.
A cut that interrupts a word reads as a glitch; a cut that lands on a sentence
end reads as an intentional edit. This module collapses the character-level
alignment that the with-timestamps TTS endpoint returns into word and
sentence boundaries, and snaps leg seams to those boundaries so chained clips
feel like deliberate cuts. It also unlocks future word-synced cuts/captions.

Pure functions only — no API calls, no ffprobe. Input is the alignment JSON
saved by ``elevenlabs_tts`` when ``with_timestamps`` is set:

    {
      "text": "...",
      "alignment": {
        "characters": [...],
        "character_start_times_seconds": [...],
        "character_end_times_seconds": [...]
      },
      "normalized_alignment": { same shape, may be null }
    }

Usage:
    from lib.word_timing import load_alignment, words, sentence_ends, leg_split_points

    alignment = load_alignment("assets/audio_v6/seg_001.alignment.json")
    boundaries = sentence_ends(alignment)
    splits = leg_split_points(total_s=18.4, n_legs=3, boundaries=boundaries)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Sentence-terminating characters. A terminator followed by whitespace (or
# end of text) closes a sentence; this naturally skips decimals like "3.5"
# because the '.' is followed by a digit, not whitespace.
_SENTENCE_TERMINATORS = {".", "!", "?", "…"}

# Dash characters that narrators render as audible pauses. If the pause is
# long enough it is a usable soft cut point even though no sentence ended.
_DASH_CHARS = {"—", "–", "―"}

# A dash (or the silence right after it) must last at least this long to
# count as a soft boundary — shorter dashes are just intonation, not pauses.
DASH_PAUSE_THRESHOLD_S = 0.4

# How far a leg seam may move from its equal-slice position to reach a
# sentence boundary. Beyond this the pacing drift becomes noticeable.
SNAP_TOLERANCE_S = 2.5

# Punctuation stripped from word edges (interior chars like the hyphen in
# "Therac-25" are kept).
_EDGE_PUNCTUATION = set(".,!?;:…\"'()[]{}<>—–―-*‘’“”`")


# ---------------------------------------------------------------------------
# Loading and normalization
# ---------------------------------------------------------------------------

def load_alignment(path: str | Path) -> dict:
    """Load an alignment JSON saved by elevenlabs_tts (with_timestamps=True)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _char_alignment(alignment: dict) -> dict[str, Any]:
    """Extract the character-level alignment arrays from either shape.

    Accepts the full saved document ({"text", "alignment",
    "normalized_alignment"}) or a bare alignment object ({"characters", ...}).
    Prefers the raw ``alignment`` (its characters match the request text);
    falls back to ``normalized_alignment`` when the raw one is missing.
    """
    if "characters" in alignment:
        candidate = alignment
    else:
        candidate = alignment.get("alignment") or alignment.get("normalized_alignment")
    if not candidate or "characters" not in candidate:
        raise ValueError(
            "No character alignment found — expected 'characters', "
            "'character_start_times_seconds', 'character_end_times_seconds'"
        )
    return candidate


# ---------------------------------------------------------------------------
# Word and sentence boundaries
# ---------------------------------------------------------------------------

def words(alignment: dict) -> list[dict]:
    """Collapse character timings into word timings.

    Returns [{"word", "start_s", "end_s"}, ...] in narration order. Words are
    whitespace-delimited runs with edge punctuation stripped from both the
    text and the timing window, so "cuts," yields word "cuts" timed on its
    letters only. Tokens that are pure punctuation (a lone dash) are dropped.

    WHY: a video seam or caption keyed to a word must not include the silence
    of trailing punctuation, or the cut will feel late and the caption will
    linger — word windows have to hug the actual spoken letters.
    """
    char_align = _char_alignment(alignment)
    chars = char_align["characters"]
    starts = char_align["character_start_times_seconds"]
    ends = char_align["character_end_times_seconds"]

    result: list[dict] = []
    token: list[tuple[str, float, float]] = []  # (char, start, end)

    def flush() -> None:
        if not token:
            return
        # Strip edge punctuation from both text and timing window.
        lo, hi = 0, len(token)
        while lo < hi and token[lo][0] in _EDGE_PUNCTUATION:
            lo += 1
        while hi > lo and token[hi - 1][0] in _EDGE_PUNCTUATION:
            hi -= 1
        core = token[lo:hi]
        if core:  # pure-punctuation tokens are dropped
            result.append({
                "word": "".join(c for c, _, _ in core),
                "start_s": core[0][1],
                "end_s": core[-1][2],
            })
        token.clear()

    for ch, start_s, end_s in zip(chars, starts, ends):
        if ch.isspace():
            flush()
        else:
            token.append((ch, start_s, end_s))
    flush()
    return result


def sentence_ends(alignment: dict) -> list[float]:
    """End times (seconds) of sentence boundaries in the narration.

    Hard boundaries: a run of . ! ? … followed by whitespace or end-of-text
    (the end time of the LAST terminator in the run is used, so "?!" and
    "..." yield one boundary). Decimals like "3.5" are skipped because the
    '.' is followed by a digit. Soft boundaries: dash characters whose
    audible pause (the char's own duration, or the silence before the next
    char starts) lasts >= DASH_PAUSE_THRESHOLD_S.

    WHY: these are the moments a listener perceives as natural rests. A leg
    seam placed here reads as an intentional cut; a seam placed mid-sentence
    reads as a glitch. Known limitation: abbreviations ("U.S. ") register as
    sentence ends — acceptable, since a pause follows them anyway.
    """
    char_align = _char_alignment(alignment)
    chars = char_align["characters"]
    starts = char_align["character_start_times_seconds"]
    ends = char_align["character_end_times_seconds"]
    n = len(chars)

    boundaries: set[float] = set()
    for i, ch in enumerate(chars):
        nxt = chars[i + 1] if i + 1 < n else None

        if ch in _SENTENCE_TERMINATORS:
            if nxt is not None and nxt in _SENTENCE_TERMINATORS:
                continue  # mid-run ("..." / "?!") — wait for the last one
            if nxt is None or nxt.isspace():
                boundaries.add(ends[i])
        elif ch in _DASH_CHARS:
            pause = ends[i] - starts[i]
            if nxt is not None:
                pause = max(pause, starts[i + 1] - ends[i])
            if pause >= DASH_PAUSE_THRESHOLD_S:
                boundaries.add(ends[i])

    return sorted(boundaries)


# ---------------------------------------------------------------------------
# Leg split points
# ---------------------------------------------------------------------------

def leg_split_points(
    total_s: float,
    n_legs: int,
    boundaries: list[float],
    min_leg_s: float = 4.0,
) -> list[float]:
    """Choose the interior split points (seconds) for n_legs chained video legs.

    Starts from equal slices, then snaps each split to the nearest boundary
    within SNAP_TOLERANCE_S, enforcing monotonicity and a minimum leg length.
    When no boundary is near (or the near one would violate min_leg_s), the
    split falls back to the equal-slice position clamped into the feasible
    window. If total_s is too short to give every leg min_leg_s, pure equal
    slices are returned (the constraint is unsatisfiable). Returns n_legs - 1
    ascending floats; [] when n_legs <= 1.

    WHY: legs that cut mid-word or mid-sentence read as glitches — the
    narration stumbles across the seam. Snapping seams to sentence ends makes
    chained clips feel like intentional cuts, while the tolerance cap and
    min_leg_s keep legs close enough to equal that no single clip exceeds the
    provider's cap or shrinks into a flash frame.

    CONTRACT: this signature is consumed by the leg-splitting stage in the
    visual pipeline — do not change it.
    """
    if n_legs <= 1 or total_s <= 0:
        return []

    equal_points = [total_s * k / n_legs for k in range(1, n_legs)]

    # Unsatisfiable min_leg_s — degrade gracefully to equal slices.
    if total_s < n_legs * min_leg_s:
        return [round(p, 3) for p in equal_points]

    usable = sorted(b for b in boundaries if 0.0 < b < total_s)
    points: list[float] = []
    prev = 0.0

    for k, target in enumerate(equal_points, start=1):
        legs_after = n_legs - k
        lo = prev + min_leg_s
        hi = total_s - min_leg_s * legs_after

        candidates = [
            b for b in usable
            if abs(b - target) <= SNAP_TOLERANCE_S and lo <= b <= hi
        ]
        if candidates:
            chosen = min(candidates, key=lambda b: abs(b - target))
        else:
            chosen = min(max(target, lo), hi)  # equal-slice fallback, clamped

        points.append(round(chosen, 3))
        prev = chosen

    return points
