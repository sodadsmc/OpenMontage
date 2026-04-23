"""Parse a showrunner script into voice segments and production cues.

A showrunner script contains narration text mixed with production
markers like ``[VISUAL: ...]``, ``[MUSIC: ...]``, ``[SILENCE Xs]``,
and ``[CUT TO BLACK]``.  This parser separates them into:

- **voice_segments** — pure narration text for TTS generation
- **timing_cues** — silence and cut-to-black markers with positions
- **visual_cues** — what should appear on screen
- **music_cues** — music direction changes
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VoiceSegment:
    """A chunk of narration text for TTS generation."""
    index: int
    act: str                # which act this belongs to
    text: str               # pure narration (no markers)
    word_count: int


@dataclass
class TimingCue:
    """A silence or cut-to-black marker."""
    after_segment: int      # insert after this voice segment index
    cue_type: str           # "silence" or "cut_to_black"
    duration: float         # seconds


@dataclass
class VisualCue:
    """A visual direction marker."""
    after_segment: int
    description: str


@dataclass
class MusicCue:
    """A music direction marker."""
    after_segment: int
    description: str


@dataclass
class ParsedScript:
    """Complete parsed showrunner script."""
    title: str
    voice_segments: list[VoiceSegment] = field(default_factory=list)
    timing_cues: list[TimingCue] = field(default_factory=list)
    visual_cues: list[VisualCue] = field(default_factory=list)
    music_cues: list[MusicCue] = field(default_factory=list)
    total_words: int = 0


# Regex patterns for markers
_VISUAL_RE = re.compile(r"\[VISUAL:\s*(.*?)\]", re.DOTALL)
_MUSIC_RE = re.compile(r"\[MUSIC:\s*(.*?)\]", re.DOTALL)
_SILENCE_RE = re.compile(r"\[SILENCE\s+(\d+)s?\]", re.IGNORECASE)
_CUT_BLACK_RE = re.compile(r"\[CUT TO BLACK\]", re.IGNORECASE)
_ACT_RE = re.compile(r"^##\s+ACT\s+\d+:\s*(.*)", re.MULTILINE)
_METADATA_RE = re.compile(r"^\*\[.*?\]\*$", re.MULTILINE)  # *[Target: ~90 seconds]*
_HEADER_RE = re.compile(r"^#+\s+.*$", re.MULTILINE)
_DIVIDER_RE = re.compile(r"^---+$", re.MULTILINE)
_ENDCARD_RE = re.compile(r"\*\*\[END CARD\]\*\*")
_WORD_COUNT_RE = re.compile(r"\*Total word count.*$", re.MULTILINE)


def parse_showrunner_script(script_path: str | Path) -> ParsedScript:
    """Parse a showrunner markdown script into production components."""
    text = Path(script_path).read_text(encoding="utf-8")
    return parse_showrunner_text(text)


def parse_showrunner_text(text: str) -> ParsedScript:
    """Parse showrunner script text into production components."""

    # Extract title
    title_match = re.search(r"^#\s+(.*)", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Untitled"

    result = ParsedScript(title=title)

    # Track current act
    current_act = ""
    segment_index = 0

    # Process line by line, accumulating narration text
    narration_buffer: list[str] = []

    def flush_buffer():
        nonlocal segment_index
        combined = " ".join(narration_buffer).strip()
        if combined:
            words = len(combined.split())
            result.voice_segments.append(VoiceSegment(
                index=segment_index,
                act=current_act,
                text=combined,
                word_count=words,
            ))
            result.total_words += words
            segment_index += 1
        narration_buffer.clear()

    for line in text.split("\n"):
        stripped = line.strip()

        # Skip empty lines, dividers, metadata, end card, word count
        if not stripped:
            continue
        if _DIVIDER_RE.match(stripped):
            continue
        if _METADATA_RE.match(stripped):
            continue
        if _ENDCARD_RE.search(stripped):
            continue
        if _WORD_COUNT_RE.match(stripped):
            continue

        # Act headers
        act_match = _ACT_RE.match(stripped)
        if act_match:
            flush_buffer()
            current_act = act_match.group(1).strip()
            continue

        # Other headers (title, subtitle)
        if _HEADER_RE.match(stripped):
            continue

        # Visual cues
        vis_match = _VISUAL_RE.search(stripped)
        if vis_match:
            flush_buffer()
            result.visual_cues.append(VisualCue(
                after_segment=segment_index - 1 if segment_index > 0 else 0,
                description=vis_match.group(1).strip(),
            ))
            # Check if there's narration text on the same line (unlikely but handle it)
            remaining = _VISUAL_RE.sub("", stripped).strip()
            if remaining:
                narration_buffer.append(remaining)
            continue

        # Music cues
        mus_match = _MUSIC_RE.search(stripped)
        if mus_match:
            flush_buffer()
            result.music_cues.append(MusicCue(
                after_segment=segment_index - 1 if segment_index > 0 else 0,
                description=mus_match.group(1).strip(),
            ))
            remaining = _MUSIC_RE.sub("", stripped).strip()
            if remaining:
                narration_buffer.append(remaining)
            continue

        # Silence cues
        sil_match = _SILENCE_RE.search(stripped)
        if sil_match:
            flush_buffer()
            result.timing_cues.append(TimingCue(
                after_segment=segment_index - 1 if segment_index > 0 else 0,
                cue_type="silence",
                duration=float(sil_match.group(1)),
            ))
            continue

        # Cut to black
        if _CUT_BLACK_RE.search(stripped):
            flush_buffer()
            result.timing_cues.append(TimingCue(
                after_segment=segment_index - 1 if segment_index > 0 else 0,
                cue_type="cut_to_black",
                duration=2.0,  # default 2s for cut to black
            ))
            continue

        # Everything else is narration
        narration_buffer.append(stripped)

    # Flush any remaining narration
    flush_buffer()

    return result
