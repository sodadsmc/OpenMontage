"""Apply a Production Bible template to a new topic via Claude API.

Takes a video_template.json (extracted by TemplateExtractor) and a topic,
then uses Claude to write topic-specific narration, search queries, and
visual descriptions for each scene — preserving the template's pacing,
narrative arc, mood, and visual strategy.

The output is a complete segment_plan.json ready for the narrated-documentary
pipeline (voice_gen → footage_search → scoring → assembly → render).

This is NOT a find-replace tool.  Claude writes original narration text
guided by the template's reusable_pattern for each scene, following the
narration_style from the Production Bible (tone, WPM, vocabulary,
rhetorical devices).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


# JSON schema for Claude structured output — matches segment_plan schema
_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["scenes"],
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "scene_id",
                    "narration",
                    "duration_seconds",
                    "pacing",
                    "search_queries",
                    "visual_description",
                    "ai_prompt",
                    "mood",
                    "visual_strategy",
                ],
                "properties": {
                    "scene_id": {"type": "string"},
                    "narration": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "pacing": {
                        "type": "string",
                        "enum": ["establishing", "escalation", "crisis", "resolution"],
                    },
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "visual_description": {"type": "string"},
                    "ai_prompt": {"type": "string"},
                    "min_duration": {"type": "number"},
                    "preferred_duration": {"type": "number"},
                    "mood": {"type": "string"},
                    "visual_strategy": {
                        "type": "string",
                        "enum": [
                            "stock_footage",
                            "diagram",
                            "math_animation",
                            "animated_chart",
                            "text_card",
                            "archival",
                            "generated",
                            "mixed",
                        ],
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


class TemplateApplier(BaseTool):
    """Fill a Production Bible template with topic-specific content via Claude."""

    name = "template_applier"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "script_generation"
    provider = "anthropic"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["python:anthropic", "env:ANTHROPIC_API_KEY"]
    install_instructions = (
        "Install the Anthropic SDK and set your API key:\n"
        "  pip install anthropic\n"
        "  export ANTHROPIC_API_KEY=your_key_here\n"
        "Get a key at https://console.anthropic.com/"
    )

    input_schema = {
        "type": "object",
        "required": ["template_path", "topic"],
        "properties": {
            "template_path": {
                "type": "string",
                "description": "Path to video_template.json from TemplateExtractor",
            },
            "topic": {
                "type": "string",
                "description": (
                    "The topic to produce a video about. "
                    "E.g., 'The Therac-25 radiation therapy disaster'"
                ),
            },
            "topic_brief": {
                "type": "string",
                "description": (
                    "Optional plain-text research notes. Superseded by "
                    "research_brief_path when both are provided."
                ),
            },
            "research_brief_path": {
                "type": "string",
                "description": (
                    "Path to research_brief.json from the research stage. "
                    "When provided, Claude writes narration using ONLY "
                    "the verified facts in the brief. This is the recommended "
                    "input for documentary production."
                ),
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save the output segment_plan.json",
            },
            "target_duration_seconds": {
                "type": "integer",
                "description": (
                    "Target total video duration. If provided, scene durations "
                    "are scaled proportionally from the template."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True,
    )
    retry_policy = RetryPolicy(
        max_retries=2, retryable_errors=["rate_limit", "timeout", "overloaded"],
    )
    side_effects = ["calls Anthropic Claude API"]

    def get_status(self) -> ToolStatus:
        try:
            __import__("anthropic")
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Template + topic brief + prompt overhead ≈ 5-10K input tokens
        # Structured output ≈ 5-15K output tokens
        return 0.08

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()

        template_path = Path(inputs.get("template_path", ""))
        if not template_path.is_file():
            return ToolResult(
                success=False,
                error=f"Template file not found: {template_path}",
            )

        topic = inputs.get("topic", "").strip()
        if not topic:
            return ToolResult(success=False, error="topic is required.")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="ANTHROPIC_API_KEY not set. " + self.install_instructions,
            )

        # Load template
        with open(template_path, encoding="utf-8") as f:
            template = json.load(f)

        # Load research brief if provided (preferred over topic_brief)
        research_brief: dict[str, Any] | None = None
        research_brief_path = inputs.get("research_brief_path")
        if research_brief_path and Path(research_brief_path).is_file():
            with open(research_brief_path, encoding="utf-8") as f:
                research_brief = json.load(f)

        topic_brief = inputs.get("topic_brief", "")
        target_duration = inputs.get("target_duration_seconds")
        output_dir = Path(inputs.get("output_dir", "projects/template_output"))
        output_dir.mkdir(parents=True, exist_ok=True)

        # Scale durations if target specified
        scene_templates = template.get("scene_templates", [])
        if target_duration and scene_templates:
            template_total = sum(s.get("duration_seconds", 20) for s in scene_templates)
            if template_total > 0:
                scale = target_duration / template_total
                for s in scene_templates:
                    s["duration_seconds"] = round(s.get("duration_seconds", 20) * scale, 1)

        # Build the prompt
        prompt = self._build_prompt(template, topic, topic_brief, research_brief)

        # Call Claude
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16384,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": _OUTPUT_SCHEMA,
                    },
                },
            )

            response_text = response.content[0].text
            segment_plan_data = json.loads(response_text)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Claude API call failed: {exc}",
            )

        scenes = segment_plan_data.get("scenes", [])
        if not scenes:
            return ToolResult(
                success=False,
                error="Claude returned an empty scene list.",
            )

        # Add metadata
        segment_plan = {
            "version": "1.0",
            "scenes": scenes,
            "metadata": {
                "source": "template_applier",
                "topic": topic,
                "reference_title": template.get("source_reference", {}).get("title", ""),
                "reference_url": template.get("source_reference", {}).get("url", ""),
                "format": template.get("format_classification", {}).get("primary_format", ""),
                "narrative_arc": template.get("narrative_arc", {}).get("structure_type", ""),
            },
        }

        # Save
        out_path = output_dir / "segment_plan.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(segment_plan, f, indent=2, ensure_ascii=False)

        total_dur = sum(s.get("duration_seconds", 0) for s in scenes)
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "segment_plan_path": str(out_path),
                "scene_count": len(scenes),
                "total_duration_seconds": round(total_dur, 1),
                "total_words": total_words,
                "estimated_narration_minutes": round(total_words / 150, 1),
                "topic": topic,
            },
            artifacts=[str(out_path)],
            duration_seconds=elapsed,
            cost_usd=self.estimate_cost(inputs),
        )

    def _build_prompt(
        self,
        template: dict[str, Any],
        topic: str,
        topic_brief: str,
        research_brief: dict[str, Any] | None = None,
    ) -> str:
        """Build the Claude prompt from the Production Bible template."""

        # Extract narration style guide
        narr_style = template.get("narration_style", {})
        style_guide = (
            f"Tone: {narr_style.get('tone', 'serious and informative')}\n"
            f"Pacing: ~{narr_style.get('pace_wpm', 160)} words per minute\n"
            f"Vocabulary: {narr_style.get('vocabulary_level', 'accessible')}\n"
            f"Person: {narr_style.get('person', 'third')}\n"
            f"Rhetorical devices: {', '.join(narr_style.get('rhetorical_devices', ['contrast', 'rhetorical questions']))}\n"
            f"Emotional register: {narr_style.get('emotional_register', 'measured with moments of intensity')}"
        )

        # Extract narrative arc
        arc = template.get("narrative_arc", {})
        arc_desc = f"Structure: {arc.get('structure_type', 'linear_chronological')}\n"
        for act in arc.get("act_breakdown", []):
            arc_desc += f"  - {act.get('act_name', '?')} ({act.get('duration_pct', '?')}%): {act.get('purpose', '')}\n"
        arc_desc += f"Tension curve: {arc.get('tension_curve', 'builds and releases')}"

        # Extract hook template
        hook = template.get("hook_analysis", {})
        hook_desc = (
            f"Technique: {hook.get('technique', 'dramatic_statement')}\n"
            f"Duration: ~{hook.get('duration_seconds', 10)}s\n"
            f"Pattern: {hook.get('reusable_pattern', 'Open with a specific date and personal story')}"
        )

        # Build scene-by-scene instructions
        scene_instructions = []
        for s in template.get("scene_templates", []):
            inst = (
                f"Scene {s.get('scene_index', '?')} "
                f"({s.get('duration_seconds', 20)}s, "
                f"pacing={s.get('pacing', 'establishing')}, "
                f"mood={s.get('mood', 'neutral')}):\n"
                f"  Function: {s.get('narrative_function', 'context_setting')}\n"
                f"  Visual: {s.get('visual_strategy', 'stock_footage')} "
                f"({s.get('b_roll_relationship', 'literal')})\n"
                f"  Pattern: {s.get('reusable_pattern', 'No pattern specified')}\n"
                f"  Energy: {s.get('energy_level', 'medium')}"
            )
            scene_instructions.append(inst)

        scene_block = "\n\n".join(scene_instructions)

        prompt = f"""\
You are a documentary scriptwriter applying a Production Bible template to a new topic.

## TOPIC
{topic}

{self._format_research_section(research_brief, topic_brief)}

## PRODUCTION BIBLE — NARRATION STYLE
{style_guide}

## NARRATIVE ARC
{arc_desc}

## HOOK
{hook_desc}

## SCENE-BY-SCENE TEMPLATE
Each scene below has a narrative function, pacing, mood, visual strategy, and a
"reusable pattern" describing WHAT content should go in this slot. Write narration
that fills each slot with topic-specific content.

{scene_block}

## YOUR TASK

For EACH scene in the template, generate:

1. **scene_id**: "scene_01", "scene_02", etc.
2. **narration**: Write the actual narration script for this scene about {topic}.
   Follow the narration style guide above. Match the specified pacing and mood.
   The narration should fill the allocated duration at ~{narr_style.get('pace_wpm', 160)} WPM.
3. **duration_seconds**: Calculate from your narration word count at 150 WPM.
4. **pacing**: Use the pacing from the template.
5. **search_queries**: 5 stock footage queries, specific to {topic}:
   - Tier 1 (queries 1-2): Very specific to the exact subject
   - Tier 2 (queries 3-4): Related but broader
   - Tier 3 (query 5): Generic atmospheric fallback
6. **visual_description**: 10-20 words describing what should be shown on screen.
   Focus on SUBJECTS and SETTING, not mood words or camera angles.
7. **ai_prompt** (required for ai_video): a concrete scene + action — shot type,
   subject, the action the narration describes, camera move. Exclude on-screen
   text, faces, and logos. E.g. 'technician reaches for the control dial as the
   screen flickers red'.
8. **min_duration**: narration word count / 150 * 60 + 0.5
9. **preferred_duration**: narration word count / 150 * 60 + 2.0
10. **mood**: Use the mood from the template.
11. **visual_strategy**: How this scene should be visualized. Choose ONE:
   - "stock_footage" — real-world footage (hospital rooms, equipment, people)
   - "diagram" — animated flowchart or state diagram (for explaining processes, race conditions, data flows)
   - "math_animation" — animated chart or counter (for dose comparisons, byte overflow, numeric data)
   - "animated_chart" — bar chart, line chart, or stat reveal (for statistics and comparisons)
   - "text_card" — title card, date card, or quote card (for section transitions, key dates, direct quotes)
   - "archival" — historical images or documents (for showing the actual paper, FDA documents, diagrams from primary sources)
   - "generated" — AI-generated atmospheric footage (for period-accurate settings no stock footage covers)
   - "mixed" — combination within the scene (footage + diagram overlay)

{"CRITICAL: Write narration using ONLY the verified facts provided in the RESEARCH BRIEF above. Every patient name, date, number, and quote MUST come from the data_points or timeline_events. Do NOT invent facts from your training data. If the research brief does not contain a specific detail, leave it out rather than fabricate it. Each scene's narration should flow naturally into the next." if research_brief else f"IMPORTANT: Write narration that is factually accurate about {topic}. The narration should tell a compelling story following the narrative arc structure above. Each scene's narration should flow naturally into the next."}
"""
        return prompt

    @staticmethod
    def _format_research_section(
        research_brief: dict[str, Any] | None,
        topic_brief: str,
    ) -> str:
        """Format the research section of the prompt."""
        if not research_brief:
            if topic_brief:
                return f"## RESEARCH NOTES\n{topic_brief}"
            return ""

        sections = ["## VERIFIED RESEARCH (from research_brief — use ONLY these facts)\n"]

        # Primary source
        primary = research_brief.get("primary_source")
        if primary:
            sections.append(f"### Primary Source\n{json.dumps(primary, indent=2)}\n")

        # Timeline
        timeline = research_brief.get("timeline_events", [])
        if timeline:
            sections.append("### Verified Timeline")
            for evt in timeline:
                src = evt.get("source_url", "")
                sections.append(f"- **{evt.get('date', '?')}**: {evt.get('event', '')} [{src}]")
            sections.append("")

        # Stakeholders
        stakeholders = research_brief.get("stakeholders", [])
        if stakeholders:
            sections.append("### Verified Stakeholders")
            for sh in stakeholders:
                verified = sh.get("verified_in", "unverified")
                sections.append(f"- **{sh.get('name', '?')}** — {sh.get('role', '')} (verified: {verified})")
            sections.append("")

        # Data points
        data_points = research_brief.get("data_points", [])
        if data_points:
            sections.append("### Verified Data Points")
            for dp in data_points:
                cred = dp.get("credibility", "unknown")
                sections.append(
                    f"- [{cred}] {dp.get('claim', '')} "
                    f"(Source: {dp.get('source_name', dp.get('source_url', 'unknown'))})"
                )
            sections.append("")

        # Unverifiable claims
        unverifiable = research_brief.get("unverifiable_claims", [])
        if unverifiable:
            sections.append("### UNVERIFIABLE CLAIMS (do NOT use in narration)")
            for claim in unverifiable:
                sections.append(f"- {claim}")
            sections.append("")

        return "\n".join(sections)
