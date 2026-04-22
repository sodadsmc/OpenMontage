"""Animation opportunity classifier for documentary scenes.

Reads each scene's narration and identifies specific animation
opportunities — scientific diagrams, data visualizations, process
flows, timelines, counter animations, and state diagrams.

Uses Claude Haiku for fast, cheap classification (~$0.01 per segment
plan).  Runs as a post-processing step on the segment plan to upgrade
coarse ``visual_strategy`` assignments (like ``mixed``) to specific
animation types with descriptions.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class AnimationOpportunity:
    """A detected animation opportunity for a scene."""
    scene_id: str
    animation_type: str        # scientific_diagram, data_visualization, process_flow, timeline, counter, state_diagram, none
    description: str           # what to animate
    manim_description: str     # specific Manim scene description for code-gen
    confidence: str            # high, medium, low
    original_strategy: str     # what visual_strategy was before


_CLASSIFICATION_PROMPT = """\
You are analyzing narration text from a documentary video to identify scenes
that would benefit from an animated diagram or visualization instead of stock
footage.

For each scene, classify whether it needs an animation and what type.

ANIMATION TYPES:
- scientific_diagram: Physical/scientific processes (radiation hitting cells, beam paths, DNA damage, how machines work)
- data_visualization: Numeric comparisons, doses, measurements, statistics (bar charts, stat cards)
- process_flow: Step-by-step sequences, cause-and-effect chains (flowcharts, numbered steps)
- timeline: Chronological events, dates, temporal sequences
- counter: Values changing over time, thresholds, overflow, accumulation
- state_diagram: Modes, configurations, state changes, before/after comparisons
- none: No animation needed — stock footage or atmospheric visuals are better

RULES:
- Scenes describing HOW something works physically → scientific_diagram
- Scenes with specific numbers being compared → data_visualization
- Scenes describing a sequence of steps/events → process_flow
- Scenes listing dates/incidents chronologically → timeline
- Scenes about counters, byte values, thresholds → counter
- Scenes about modes or configurations switching → state_diagram
- Scenes that are purely narrative/emotional → none (stock footage is better)
- Be selective — not every scene needs an animation. Aim for 30-40% of scenes.

SCENES:
{scenes_json}

Return ONLY valid JSON — an array of objects, one per scene:
[
  {{
    "scene_id": "scene_01",
    "animation_type": "none",
    "description": "",
    "manim_description": "",
    "confidence": "high"
  }}
]

For scenes with animation_type != "none", provide:
- description: 1-2 sentences describing WHAT to animate
- manim_description: Specific Manim objects/animations to use (e.g., "BarChart with values [86, 10000], colors [GREEN, RED], animated with Create()")
"""


def classify_scenes(
    segment_plan: dict[str, Any],
) -> list[AnimationOpportunity]:
    """Classify all scenes in a segment plan for animation opportunities.

    Uses Claude Haiku for fast, cheap classification.
    Returns a list of AnimationOpportunity for every scene.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _log.warning("ANTHROPIC_API_KEY not set — skipping animation classification")
        return []

    scenes = segment_plan.get("scenes", [])
    if not scenes:
        return []

    # Build scene summaries for the prompt
    scene_summaries = []
    for s in scenes:
        scene_summaries.append({
            "scene_id": s["scene_id"],
            "narration": s.get("narration", "")[:300],  # cap to save tokens
            "current_visual_strategy": s.get("visual_strategy", "mixed"),
            "pacing": s.get("pacing", ""),
            "mood": s.get("mood", ""),
        })

    prompt = _CLASSIFICATION_PROMPT.format(
        scenes_json=json.dumps(scene_summaries, indent=2),
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            import re
            match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        classifications = json.loads(text)
    except Exception as exc:
        _log.warning("Animation classification failed: %s", exc)
        return []

    # Build results
    results = []
    for cls in classifications:
        sid = cls.get("scene_id", "")
        atype = cls.get("animation_type", "none")
        orig = next((s.get("visual_strategy", "mixed") for s in scenes if s["scene_id"] == sid), "mixed")

        results.append(AnimationOpportunity(
            scene_id=sid,
            animation_type=atype,
            description=cls.get("description", ""),
            manim_description=cls.get("manim_description", ""),
            confidence=cls.get("confidence", "medium"),
            original_strategy=orig,
        ))

    return results


def enrich_segment_plan(
    segment_plan: dict[str, Any],
    opportunities: list[AnimationOpportunity],
) -> dict[str, Any]:
    """Update a segment plan's visual_strategy fields based on classification.

    Upgrades scenes from coarse strategies (mixed, stock_footage) to
    specific animation types.  Adds ``animation_description`` field
    for the visual router to use.
    """
    opp_map = {o.scene_id: o for o in opportunities}

    # Map animation_type to visual_strategy
    type_to_strategy = {
        "scientific_diagram": "math_animation",
        "data_visualization": "math_animation",
        "process_flow": "diagram",
        "timeline": "math_animation",
        "counter": "math_animation",
        "state_diagram": "diagram",
    }

    upgraded = 0
    for scene in segment_plan.get("scenes", []):
        sid = scene["scene_id"]
        opp = opp_map.get(sid)
        if not opp or opp.animation_type == "none":
            continue

        current = scene.get("visual_strategy", "mixed")
        new_strategy = type_to_strategy.get(opp.animation_type, current)

        # Only upgrade if current strategy is generic
        if current in ("mixed", "stock_footage"):
            scene["visual_strategy"] = new_strategy
            scene["animation_description"] = opp.description
            scene["manim_description"] = opp.manim_description
            scene["animation_type"] = opp.animation_type
            upgraded += 1
            _log.info("Upgraded %s: %s → %s (%s)", sid, current, new_strategy, opp.animation_type)

    _log.info("Enriched segment plan: %d scenes upgraded", upgraded)
    return segment_plan
