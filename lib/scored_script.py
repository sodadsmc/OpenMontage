"""Parse and validate Scored Script Format (YAML).

The Scored Script replaces freeform markdown showrunner scripts with
structured YAML where every segment declares its own visual inline —
creating an unbreakable 1:1 narration↔visual binding.

No heuristics. No regex guessing. Direct mapping from YAML to dataclasses.

Usage:
    script = load_scored_script("path/to/scored_script.yaml")
    for seg in script.segments:
        print(seg.id, seg.narration, seg.visual.type)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AnimationPhase:
    """A timed phase within a Manim animation."""
    phase: str
    description: str = ""
    target_duration_pct: float = 100.0


@dataclass
class ShotSpec:
    """A single AI-generated shot within a multi-shot segment.

    Long segments (20-30s) are rendered as several short i2v clips cut
    together rather than one looped clip. Each shot inherits the segment's
    asset_ref so the same canonical reference threads through all of them.
    """
    shot_id: str
    ai_prompt: str
    ai_motion: str | None = None
    asset_ref: str | None = None
    duration_weight: float = 1.0
    hero: bool = False


@dataclass
class FLFSpec:
    """A FIRST-LAST-FRAME transition for an EXPLANATORY beat (counts, state-changes, things
    going dark/cold/gone). The start keyframe is authored with the subject present/lit; the END
    frame is derived DETERMINISTICALLY from it (blend toward navy, optionally over a vertical
    band) so Kling 3.0 interpolates a pinned, drift-free transition — exact counts, a locked
    camera, and object permanence hold by construction. When a beat sets ``visual.flf`` the
    assembly routes it through lib.flf instead of open Grok i2v (and it is skipped from the paid
    i2v manifest). See lib/flf.py + skills/pipelines/narrated-documentary/ai-visual-director.md."""
    start_prompt: str               # START keyframe content (subject present/lit); channel style appended at gen
    transition: str                 # the Kling FLF prompt (start -> end), <= 500 chars
    drain: float = 0.85             # deterministic end: blend weight toward navy (subject goes cold/dead)
    band: tuple[float, float] | None = None   # optional (lo, hi) vertical ramp — drains only that region (dim N of M)
    anchor: str = "fresh"           # "fresh", or a bible asset_id to ground/edit the start keyframe on a real reference

    @classmethod
    def from_dict(cls, d: dict) -> "FLFSpec":
        band = d.get("band")
        return cls(
            start_prompt=d["start_prompt"],
            transition=d["transition"],
            drain=float(d.get("drain", 0.85)),
            band=tuple(band) if band else None,
            anchor=d.get("anchor", "fresh"),
        )


@dataclass
class VisualSpec:
    """What should appear on screen for a segment."""
    description: str
    type: str  # atmospheric_footage, manim_animation, text_card, etc.

    # Text card specifics
    card_type: str | None = None  # stat_reveal, chapter_title, error_message, ...
    values: dict[str, Any] | None = None
    emphasis: str | None = None
    terminal_text: str | None = None
    subtext: str | None = None

    # Manim specifics
    template: str | None = None
    must_show: list[str] = field(default_factory=list)
    animation_phases: list[AnimationPhase] = field(default_factory=list)
    reference_images: list[str] = field(default_factory=list)

    # Asset identity
    uniqueness: str = "unique"  # unique, unique_per_location, reusable
    location_id: str | None = None
    subject_continuity: str | None = None

    # Search / generation hints
    reference_period: str | None = None
    search_queries: list[str] = field(default_factory=list)
    ai_fallback_prompt: str | None = None

    # AI-generated video (primary source)
    ai_prompt: str | None = None
    ai_motion: str | None = None
    ai_style: str | None = None
    ai_reference_image: str | None = None
    asset_ref: str | None = None
    shots: list[ShotSpec] = field(default_factory=list)
    # Cross-asset staging: bible asset_ids whose reference sheets/canonicals
    # ride into this segment's keyframe edits as EXTRA references. Lets one
    # frame stage two anchored identities (e.g. the terminal in the foreground
    # while the treatment-room machine looms in the background) without either
    # drifting off-model. The segment's own asset stays the i2v anchor.
    support_asset_refs: list[str] = field(default_factory=list)

    # First-last-frame transition (explanatory beats). When set, the assembly routes this
    # segment through the FLF lane (lib.flf) and it is excluded from the paid i2v manifest.
    flf: "FLFSpec | None" = None

    # Styled caption burned over this segment's footage ("text over footage").
    text_overlay: list[str] = field(default_factory=list)
    text_emphasis: int = -1  # index of the amber/large line (-1 = all equal)

    # Denormalized segment context for the Scene Library default-motion
    # derivation (visual_router.plan_ai_video). These are SEGMENT-level tags
    # (they also live on Segment) copied onto the visual at parse time so a
    # caller holding only ``seg.visual`` — the two-phase batch path — can still
    # derive the emotionally-motivated camera move when ai_motion is blank.
    editorial_intent: str | None = None
    directors_move: str | None = None
    pacing: str | None = None

    @property
    def effective_prompt(self) -> str:
        """Prompt used for AI generation — explicit ai_prompt, else the description."""
        return self.ai_prompt or self.description

    @classmethod
    def from_dict(cls, d: dict) -> VisualSpec:
        phases = [
            AnimationPhase(**p) for p in d.get("animation_phases", [])
        ]
        shots = [
            ShotSpec(
                shot_id=s["shot_id"],
                ai_prompt=s["ai_prompt"],
                ai_motion=s.get("ai_motion"),
                asset_ref=s.get("asset_ref"),
                duration_weight=s.get("duration_weight", 1.0),
                hero=s.get("hero", False),
            )
            for s in d.get("shots", [])
        ]
        return cls(
            description=d["description"],
            type=d["type"],
            card_type=d.get("card_type"),
            values=d.get("values"),
            emphasis=d.get("emphasis"),
            terminal_text=d.get("terminal_text"),
            subtext=d.get("subtext"),
            template=d.get("template"),
            must_show=d.get("must_show", []),
            animation_phases=phases,
            reference_images=d.get("reference_images", []),
            uniqueness=d.get("uniqueness", "unique"),
            location_id=d.get("location_id"),
            subject_continuity=d.get("subject_continuity"),
            reference_period=d.get("reference_period"),
            search_queries=d.get("search_queries", []),
            ai_fallback_prompt=d.get("ai_fallback_prompt"),
            ai_prompt=d.get("ai_prompt"),
            ai_motion=d.get("ai_motion"),
            ai_style=d.get("ai_style"),
            ai_reference_image=d.get("ai_reference_image"),
            asset_ref=d.get("asset_ref"),
            shots=shots,
            support_asset_refs=d.get("support_asset_refs", []),
            flf=FLFSpec.from_dict(d["flf"]) if d.get("flf") else None,
            text_overlay=d.get("text_overlay", []),
            text_emphasis=d.get("text_emphasis", -1),
        )


@dataclass
class MusicSpec:
    """Music state at a segment."""
    state: str  # playing, continue, change, stop, fade_out
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> MusicSpec:
        return cls(state=d["state"], description=d.get("description", ""))


@dataclass
class ActInfo:
    """Act metadata from the script header."""
    id: str
    title: str
    target_pct: float = 0.0


@dataclass
class Segment:
    """A single scored segment — narration + visual binding."""
    id: str  # seg_001
    act: str  # act_1
    narration: str  # pure speakable text
    visual: VisualSpec
    music: MusicSpec | None = None
    silence_after_s: float = 0.0
    editorial_intent: str | None = None
    pacing: str | None = None

    # Scene Library tags (The Director's Touch) — all optional/back-compatible.
    rhythm: str | None = None              # fast / medium / lingering / breath
    narration_mode: str | None = None      # literal / evocative / none
    audio_transition: str | None = None    # hard_cut / j_cut / l_cut / sound_bridge / ...
    directors_move: str | None = None      # named reveal/withholding move (Taxonomy 5)
    retention_beat: str | None = None      # cold_open / pattern_interrupt / cliffhanger / ...
    open_loop: dict[str, Any] | None = None  # {action: plant|payoff, id: <loop key>, note}

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    @property
    def index(self) -> int:
        """Numeric index extracted from id (seg_001 → 1)."""
        return int(self.id.split("_")[1])


@dataclass
class ScoredScript:
    """Complete scored script — the single source of truth."""
    title: str
    segments: list[Segment]
    acts: list[ActInfo] = field(default_factory=list)
    total_target_duration_s: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_words(self) -> int:
        return sum(s.word_count for s in self.segments)

    def get_act_segments(self, act_id: str) -> list[Segment]:
        """Get all segments belonging to an act."""
        return [s for s in self.segments if s.act == act_id]


# ---------------------------------------------------------------------------
# Parser — trivial YAML loader, zero heuristics
# ---------------------------------------------------------------------------

def load_scored_script(path: str | Path) -> ScoredScript:
    """Load a scored script from a YAML file."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_scored_script_text(text)


def parse_scored_script_text(text: str) -> ScoredScript:
    """Parse scored script YAML text into a ScoredScript."""
    doc = yaml.safe_load(text)

    if not doc or "segments" not in doc:
        raise ValueError("Scored script must have a 'segments' key")

    meta = doc.get("metadata", {})

    acts = [
        ActInfo(id=a["id"], title=a["title"], target_pct=a.get("target_pct", 0))
        for a in meta.get("acts", [])
    ]

    segments = []
    for raw in doc["segments"]:
        vis = VisualSpec.from_dict(raw["visual"])
        # Denormalize the segment's Scene Library tags onto the visual so the
        # planner can derive a default camera move from seg.visual alone.
        vis.editorial_intent = raw.get("editorial_intent")
        vis.directors_move = raw.get("directors_move")
        vis.pacing = raw.get("pacing")
        mus = MusicSpec.from_dict(raw["music"]) if raw.get("music") else None

        segments.append(Segment(
            id=raw["id"],
            act=raw["act"],
            narration=raw["narration"].strip(),
            visual=vis,
            music=mus,
            silence_after_s=raw.get("silence_after_s", 0.0),
            editorial_intent=raw.get("editorial_intent"),
            pacing=raw.get("pacing"),
            rhythm=raw.get("rhythm"),
            narration_mode=raw.get("narration_mode"),
            audio_transition=raw.get("audio_transition"),
            directors_move=raw.get("directors_move"),
            retention_beat=raw.get("retention_beat"),
            open_loop=raw.get("open_loop"),
        ))

    return ScoredScript(
        title=meta.get("title", "Untitled"),
        segments=segments,
        acts=acts,
        total_target_duration_s=meta.get("total_target_duration_s", 0.0),
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Schema validation (against JSON Schema)
# ---------------------------------------------------------------------------

def validate_against_schema(doc: dict) -> list[str]:
    """Validate a scored script dict against the JSON Schema.

    Returns a list of error messages (empty = valid).
    """
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema package not installed — skipping schema validation"]

    schema_path = Path(__file__).parent.parent / "schemas" / "artifacts" / "scored_script.schema.json"
    if not schema_path.exists():
        return [f"Schema file not found: {schema_path}"]

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    errors = []
    for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{path}: {error.message}")
    return errors


def validate_scored_script_file(path: str | Path) -> list[str]:
    """Validate a YAML file against the scored script schema."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return validate_against_schema(doc)
