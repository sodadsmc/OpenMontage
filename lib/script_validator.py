"""Structural validator for Scored Scripts.

Runs pre-generation checks that catch binding errors, card type problems,
visual diversity issues, and uniqueness constraint violations. These are
the bugs that the old freeform parser let through because it relied on
proximity heuristics.

Usage:
    from lib.scored_script import load_scored_script
    from lib.script_validator import validate_structure

    script = load_scored_script("path/to/scored_script.yaml")
    report = validate_structure(script)
    if report.blocking:
        for err in report.errors:
            print(f"ERROR: {err}")
        raise SystemExit("Structural validation failed — fix script before generating")
    for warn in report.warnings:
        print(f"WARNING: {warn}")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lib.scored_script import ScoredScript, Segment, VisualSpec


# Known visual types and card types (must match schema)
KNOWN_VISUAL_TYPES = {
    "atmospheric_footage",
    "archival_footage",
    "stock_footage",
    "generated_footage",
    "ai_video",
    "manim_animation",
    "text_card",
    "diagram",
    "mixed",
}

KNOWN_CARD_TYPES = {
    "stat_reveal",
    "chapter_title",
    "institutional_text",
    "technical_label",
    "error_message",
    "verdict",
    "quote",
}

KNOWN_EDITORIAL_INTENTS = {
    "establishing_atmosphere",
    "building_trust",
    "technical_explanation",
    "escalation",
    "crisis_moment",
    "pattern_recognition",
    "emotional_impact",
    "institutional_critique",
    "resolution",
    "call_to_action",
    "chapter_transition",
}


@dataclass
class ValidationReport:
    """Result of structural validation."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """True if any errors exist (pipeline should not proceed)."""
        return len(self.errors) > 0

    @property
    def clean(self) -> bool:
        """True if no errors and no warnings."""
        return not self.errors and not self.warnings

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"{len(self.errors)} error(s):")
            for e in self.errors:
                lines.append(f"  ERROR: {e}")
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            for w in self.warnings:
                lines.append(f"  WARN:  {w}")
        if self.clean:
            lines.append("All checks passed.")
        return "\n".join(lines)


def validate_structure(script: ScoredScript, asset_bible: Any = None) -> ValidationReport:
    """Run all structural validation checks on a scored script.

    Args:
        script: the parsed scored script.
        asset_bible: optional AssetBible (lib.asset_bible). When provided, the
            referential integrity of every asset_ref is checked. When None
            (e.g. before the bible is built), the asset-ref check is skipped.

    Returns a ValidationReport with errors (blocking) and warnings (advisory).
    """
    report = ValidationReport()

    _check_segment_ids(script, report)
    _check_visual_bindings(script, report)
    _check_card_types(script, report)
    _check_visual_types(script, report)
    _check_animation_phases(script, report)
    _check_uniqueness_constraints(script, report)
    _check_visual_diversity(script, report)
    _check_narration_not_empty(script, report)
    _check_act_references(script, report)
    _check_manim_templates(script, report)
    _check_ai_shots(script, report)
    if asset_bible is not None:
        _check_asset_refs(script, asset_bible, report)

    return report


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_segment_ids(script: ScoredScript, report: ValidationReport):
    """Every segment must have a unique ID in seg_NNN format."""
    seen: set[str] = set()
    for seg in script.segments:
        if seg.id in seen:
            report.errors.append(f"Duplicate segment ID: {seg.id}")
        seen.add(seg.id)

    # Check ordering — segment indices should be monotonically increasing
    indices = [seg.index for seg in script.segments]
    for i in range(1, len(indices)):
        if indices[i] <= indices[i - 1]:
            report.warnings.append(
                f"Segment ordering: {script.segments[i].id} "
                f"(index {indices[i]}) follows {script.segments[i-1].id} "
                f"(index {indices[i-1]}) — not monotonically increasing"
            )


def _check_visual_bindings(script: ScoredScript, report: ValidationReport):
    """Every segment MUST have a visual with a non-empty description."""
    for seg in script.segments:
        if not seg.visual:
            report.errors.append(f"{seg.id}: missing visual binding")
        elif not seg.visual.description or len(seg.visual.description.strip()) < 10:
            report.errors.append(
                f"{seg.id}: visual description too short "
                f"({len(seg.visual.description.strip()) if seg.visual.description else 0} chars, need >=10)"
            )


def _check_card_types(script: ScoredScript, report: ValidationReport):
    """Text cards must declare a known card_type."""
    for seg in script.segments:
        if seg.visual.type == "text_card":
            if not seg.visual.card_type:
                report.errors.append(
                    f"{seg.id}: text_card visual missing required card_type"
                )
            elif seg.visual.card_type not in KNOWN_CARD_TYPES:
                report.errors.append(
                    f"{seg.id}: unknown card_type '{seg.visual.card_type}'. "
                    f"Must be one of: {', '.join(sorted(KNOWN_CARD_TYPES))}"
                )

            # stat_reveal should have values
            if seg.visual.card_type == "stat_reveal" and not seg.visual.values:
                report.warnings.append(
                    f"{seg.id}: stat_reveal card has no 'values' dict — "
                    f"numbers won't animate"
                )

            # error_message should have terminal_text
            if seg.visual.card_type == "error_message" and not seg.visual.terminal_text:
                report.warnings.append(
                    f"{seg.id}: error_message card has no 'terminal_text'"
                )


def _check_visual_types(script: ScoredScript, report: ValidationReport):
    """Every visual must use a known type."""
    for seg in script.segments:
        if seg.visual.type not in KNOWN_VISUAL_TYPES:
            report.errors.append(
                f"{seg.id}: unknown visual type '{seg.visual.type}'. "
                f"Must be one of: {', '.join(sorted(KNOWN_VISUAL_TYPES))}"
            )


def _check_animation_phases(script: ScoredScript, report: ValidationReport):
    """Animation phases must sum to 100%."""
    for seg in script.segments:
        if seg.visual.animation_phases:
            total = sum(p.target_duration_pct for p in seg.visual.animation_phases)
            if abs(total - 100.0) > 0.5:
                report.errors.append(
                    f"{seg.id}: animation_phases sum to {total}% (must be 100%)"
                )


def _check_uniqueness_constraints(script: ScoredScript, report: ValidationReport):
    """unique_per_location segments must have a location_id."""
    for seg in script.segments:
        if seg.visual.uniqueness == "unique_per_location" and not seg.visual.location_id:
            report.errors.append(
                f"{seg.id}: uniqueness is 'unique_per_location' but no location_id set"
            )


def _check_visual_diversity(script: ScoredScript, report: ValidationReport):
    """Warn on consecutive identical card types or 3+ text cards in a row."""
    consecutive_text = 0
    last_card_type: str | None = None

    for seg in script.segments:
        if seg.visual.type == "text_card":
            consecutive_text += 1
            if consecutive_text >= 3:
                report.warnings.append(
                    f"{seg.id}: 3+ consecutive text cards ending here. "
                    f"Consider breaking up with footage or animation."
                )
            if seg.visual.card_type and seg.visual.card_type == last_card_type:
                report.warnings.append(
                    f"{seg.id}: same card_type '{last_card_type}' as previous "
                    f"text card — visual monotony risk"
                )
            last_card_type = seg.visual.card_type
        else:
            consecutive_text = 0
            last_card_type = None


def _check_narration_not_empty(script: ScoredScript, report: ValidationReport):
    """Every segment must have narration text."""
    for seg in script.segments:
        if not seg.narration or not seg.narration.strip():
            report.errors.append(f"{seg.id}: empty narration text")


def _check_act_references(script: ScoredScript, report: ValidationReport):
    """Segment act references should match declared acts (if any)."""
    if not script.acts:
        return

    act_ids = {a.id for a in script.acts}
    for seg in script.segments:
        if seg.act not in act_ids:
            report.warnings.append(
                f"{seg.id}: references act '{seg.act}' which is not "
                f"declared in metadata.acts"
            )


def _check_manim_templates(script: ScoredScript, report: ValidationReport):
    """Manim animations should specify a template name."""
    for seg in script.segments:
        if seg.visual.type == "manim_animation" and not seg.visual.template:
            report.warnings.append(
                f"{seg.id}: manim_animation visual has no template specified — "
                f"visual router won't know which animation to generate"
            )


def _check_ai_shots(script: ScoredScript, report: ValidationReport):
    """AI-video segments with an explicit shot breakdown must be well-formed."""
    for seg in script.segments:
        shots = seg.visual.shots
        if not shots:
            continue
        if seg.visual.type != "ai_video":
            report.warnings.append(
                f"{seg.id}: declares shots[] but type is '{seg.visual.type}', "
                f"not 'ai_video' — shots will be ignored"
            )
        seen: set[str] = set()
        for shot in shots:
            if shot.shot_id in seen:
                report.errors.append(f"{seg.id}: duplicate shot_id '{shot.shot_id}'")
            seen.add(shot.shot_id)
            if shot.duration_weight <= 0:
                report.errors.append(
                    f"{seg.id}/{shot.shot_id}: duration_weight must be > 0 "
                    f"(got {shot.duration_weight})"
                )


def _check_asset_refs(script: ScoredScript, asset_bible: Any, report: ValidationReport):
    """Every asset_ref (segment- or shot-level) must resolve in the Asset Bible.

    Only runs when an asset_bible is passed to validate_structure. The bible is
    duck-typed: it just needs a ``.get(asset_id)`` returning None when missing.
    """
    for seg in script.segments:
        refs: list[str] = []
        if seg.visual.asset_ref:
            refs.append(seg.visual.asset_ref)
        refs.extend(shot.asset_ref for shot in seg.visual.shots if shot.asset_ref)
        for ref in refs:
            if asset_bible.get(ref) is None:
                report.errors.append(
                    f"{seg.id}: asset_ref '{ref}' does not resolve in the Asset Bible"
                )
