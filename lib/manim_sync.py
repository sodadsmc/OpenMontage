"""Narration-synced Manim — drive animation reveals from the narration timeline.

THE PROBLEM this solves: Manim beats are authored on a hardcoded internal clock
(`self.play(run_time=...)`, `self.wait(...)`), then the rendered clip is blunt-
trimmed/padded to the segment's audio length. So reveals never land on the words
— a beat about "the metal target" might appear 10 seconds before or after the
narrator says it. Meanwhile ElevenLabs gives per-word timestamps (lib.word_timing)
that were only ever wired to AI-video legs, never to Manim.

THE FIX: a Cue maps a narration PHRASE to an animation BEAT. ``schedule`` resolves
each phrase to its spoken timestamp (word_timing.find_phrase_start) and emits a
Timeline — the exact ``wait``/``play`` sequence so each reveal STARTS as its word
is spoken, and the whole scene runs exactly the narration's length (no post-hoc
trim). Scene-agnostic: any Manim scene that exposes its reveals as a ``BEATS``
dict of zero-arg callables can be narration-synced.

Usage (in a generated scene's construct()):
    cues = [
        Cue("machine",  "two ways to fire", run_time=0.9),
        Cue("electron", "electron mode",    run_time=0.8),
        Cue("xray",     "X-ray mode",       run_time=0.8),
        Cue("target",   "metal target",     run_time=0.9),
        Cue("rotate",   "Two modes",        run_time=1.4),
    ]
    tl = schedule(cues, alignment, total_s=34.74)
    # tl.code() -> the wait/play statements that drive BEATS[...] on the words
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lib.word_timing import find_phrase_start, words

_log = logging.getLogger(__name__)


@dataclass
class Cue:
    """Bind a narration phrase to an animation beat.

    ``beat``      — key into the scene's BEATS dict (the reveal to play).
    ``anchor``    — narration phrase whose spoken start time fires the beat.
    ``run_time``  — how long the reveal animation plays (seconds).
    ``min_start`` — floor for the start time (e.g. 0.0 to let the opening beat
                    appear at the top even if its phrase is a beat or two in).
    """
    beat: str
    anchor: str
    run_time: float = 0.8
    min_start: float = 0.0


@dataclass
class ScheduledBeat:
    beat: str
    start_s: float       # when the reveal play begins (lands on the word)
    wait_before: float   # self.wait inserted before this beat's play
    run_time: float


@dataclass
class Timeline:
    beats: list[ScheduledBeat] = field(default_factory=list)
    total_s: float = 0.0
    unresolved: list[str] = field(default_factory=list)  # anchors not found

    @property
    def end_s(self) -> float:
        """Cumulative end time of the last play — exactly what construct() reaches."""
        clock = 0.0
        for b in self.beats:
            clock += b.wait_before + b.run_time
        return round(clock, 3)

    @property
    def final_wait(self) -> float:
        """Trailing hold so the scene runs exactly total_s (0 if beats overrun)."""
        return max(0.0, round(self.total_s - self.end_s, 3))

    def code(self, beats_var: str = "BEATS", scene: str = "self") -> str:
        """Emit the construct()-body wait/play sequence that drives the beats.

        Each beat: an optional ``scene.wait(gap)`` to reach its word, then
        ``scene.play(*BEATS["beat"](), run_time=...)``. A trailing wait pads to
        total_s. BEATS must be a dict of zero-arg callables returning the
        animation(s) for that beat.
        """
        lines: list[str] = []
        for b in self.beats:
            if b.wait_before > 0.001:
                lines.append(f"{scene}.wait({b.wait_before:.3f})")
            lines.append(
                f'{scene}.play(*{beats_var}["{b.beat}"](), run_time={b.run_time:.3f})'
            )
        if self.final_wait > 0.001:
            lines.append(f"{scene}.wait({self.final_wait:.3f})")
        return "\n".join(lines)


def schedule(cues, alignment, total_s: float) -> Timeline:
    """Resolve cues against the narration alignment into a timed Timeline.

    ``alignment`` may be a word_timing alignment dict OR a pre-computed
    ``words()`` list. Cues whose anchor phrase isn't found are skipped (recorded
    in ``timeline.unresolved``) — a missing anchor must never crash a render.

    Beats are ordered by spoken time. ``wait_before`` is the gap from the
    previous beat's end to this beat's word; if a previous beat's run_time would
    overrun the next word the wait clamps to 0 (the next reveal plays as soon as
    it can) so the sequence never goes backwards.
    """
    word_list = alignment if isinstance(alignment, list) else words(alignment)

    resolved: list[tuple[Cue, float]] = []
    unresolved: list[str] = []
    for c in cues:
        t = find_phrase_start(word_list, c.anchor)
        if t is None:
            unresolved.append(c.anchor)
            _log.warning("manim_sync: anchor not found — skipping beat '%s' (anchor=%r)",
                         c.beat, c.anchor)
            continue
        resolved.append((c, max(t, c.min_start)))

    resolved.sort(key=lambda ct: ct[1])

    beats: list[ScheduledBeat] = []
    clock = 0.0
    for c, start in resolved:
        actual_start = max(clock, start)
        wait_before = round(actual_start - clock, 3)
        beats.append(ScheduledBeat(c.beat, round(actual_start, 3), wait_before, c.run_time))
        clock = actual_start + c.run_time

    return Timeline(beats=beats, total_s=round(total_s, 3), unresolved=unresolved)
