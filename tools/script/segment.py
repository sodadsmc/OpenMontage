"""Script segmentation via Claude API.

Analyzes a full script and segments it into scenes with pacing, search
queries, visual descriptions, and AI video prompts. Designed for
tech-disaster documentary production but adaptable to other styles.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

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


# Structured output schema sent to Claude for guaranteed valid JSON
_SCENE_SCHEMA = {
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
                    "min_duration",
                    "preferred_duration",
                    "mood",
                ],
                "properties": {
                    "scene_id": {"type": "string"},
                    "narration": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "pacing": {
                        "type": "string",
                        "enum": [
                            "establishing",
                            "escalation",
                            "crisis",
                            "resolution",
                        ],
                    },
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "visual_description": {"type": "string"},
                    "ai_prompt": {"type": "string"},
                    "min_duration": {"type": "number"},
                    "preferred_duration": {"type": "number"},
                    "mood": {
                        "type": "string",
                        "enum": ["tension", "neutral", "dramatic", "resolution"],
                    },
                    "closing_line": {"type": "string"},
                    "youtube_title": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

_PROMPT_TEMPLATE = """\
You are a documentary video editor specializing in {channel_style} content.

Segment the following script into scenes for a video production pipeline.
{duration_guidance}

For EACH scene, provide:

1. **scene_id**: "scene_01", "scene_02", etc.
2. **narration**: The exact narration text for this scene (verbatim from the script).
3. **duration_seconds**: Estimated duration based on word count at 150 words per minute.
4. **pacing**: One of "establishing", "escalation", "crisis", or "resolution".
   - establishing: Opening/context-setting. Slow, atmospheric.
   - escalation: Building tension. Moderate pace, layered information.
   - crisis: Peak tension. Fast cuts, urgent tone.
   - resolution: Denouement/aftermath. Slow, reflective.
5. **search_queries**: Exactly 5 stock footage search queries, ordered from most specific (tier 1) to most generic (tier 3):
   - Tier 1 (queries 1-2): Very specific to the exact subject (e.g., "Therac-25 radiation therapy machine 1980s")
   - Tier 2 (queries 3-4): Related but broader (e.g., "hospital radiation treatment room vintage")
   - Tier 3 (query 5): Generic fallback (e.g., "medical equipment dark moody")
6. **visual_description**: A MODERATE description for visual matching. One sentence, 10-20 words, describing the main subject and setting. Do NOT over-specify — avoid era-specific details (like "1980s"), specific colors, exact camera angles, or lighting descriptions. SigLIP scores HIGHER with moderate descriptions like "hospital treatment room with medical radiation equipment" than ultra-specific ones like "dimly lit 1980s hospital with boxy radiation therapy machine and green phosphor monitors." Focus on WHAT is in the frame, not the mood or era.
7. **ai_prompt** (required for ai_video): a concrete scene + action — shot type, subject, the action the narration describes, camera move; exclude on-screen text/faces/logos. E.g. 'technician reaches for the control dial as the screen flickers red'.
8. **min_duration**: Minimum acceptable duration in seconds (narration length + 0.5s buffer).
9. **preferred_duration**: Ideal duration with breathing room (narration length + 1.5s buffer).
10. **mood**: One of "tension", "neutral", "dramatic", or "resolution".
11. **closing_line**: (ONLY on the LAST scene) A punchy closing line for the end tag.
12. **youtube_title**: (ONLY on the FIRST scene) 2-3 YouTube title options that would drive clicks.

SCRIPT:
{script_text}
"""


class ScriptSegment(BaseTool):
    """Segments a script into scenes via Claude API for video production."""

    name = "script_segment"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "script_analysis"
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

    capabilities = ["script_analysis", "scene_segmentation"]
    supports = {
        "structured_output": True,
        "pacing_analysis": True,
        "search_query_generation": True,
    }
    best_for = [
        "segmenting documentary scripts into production-ready scenes",
        "generating search queries and visual descriptions per scene",
        "pacing analysis for tech-disaster narratives",
    ]
    not_good_for = [
        "offline production",
        "scripts under 50 words",
    ]

    input_schema = {
        "type": "object",
        "required": ["script_text"],
        "properties": {
            "script_text": {
                "type": "string",
                "description": "Full script text to segment into scenes",
            },
            "target_duration_seconds": {
                "type": "integer",
                "description": "Target total video duration in seconds (optional guidance)",
            },
            "channel_style": {
                "type": "string",
                "default": "tech_disaster_documentary",
                "description": "Channel style to guide segmentation tone",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2, retryable_errors=["rate_limit", "timeout", "overloaded"]
    )
    idempotency_key_fields = ["script_text", "channel_style"]
    side_effects = ["calls Anthropic Claude API"]
    user_visible_verification = [
        "Review scene boundaries and pacing assignments for narrative coherence",
    ]

    # Cost constants (Claude Sonnet pricing per token, approximate)
    _INPUT_COST_PER_MTOK = 3.0  # $3.00 per million input tokens
    _OUTPUT_COST_PER_MTOK = 15.0  # $15.00 per million output tokens
    _CHARS_PER_TOKEN = 4.0  # rough estimate

    PROMPT_TEMPLATE = _PROMPT_TEMPLATE

    def get_status(self) -> ToolStatus:
        """Check for Anthropic SDK and API key."""
        try:
            __import__("anthropic")
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Estimate cost based on input token count."""
        script_text = inputs.get("script_text", "")
        input_tokens = len(script_text) / self._CHARS_PER_TOKEN + 500  # prompt overhead
        # Estimate output at ~2x input for structured JSON
        output_tokens = input_tokens * 2
        cost = (
            (input_tokens / 1_000_000) * self._INPUT_COST_PER_MTOK
            + (output_tokens / 1_000_000) * self._OUTPUT_COST_PER_MTOK
        )
        return round(cost, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Segment a script into scenes via Claude API."""
        script_text = inputs.get("script_text", "")
        if not script_text or len(script_text.strip()) < 50:
            return ToolResult(
                success=False,
                error="Script text is too short. Provide at least 50 characters.",
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="ANTHROPIC_API_KEY not set. " + self.install_instructions,
            )

        start = time.time()

        try:
            result = self._call_claude(inputs, api_key)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Script segmentation failed: {exc}",
            )

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    def _call_claude(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        """Call Claude API with structured output for scene segmentation."""
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        script_text = inputs["script_text"]
        channel_style = inputs.get("channel_style", "tech_disaster_documentary")
        target_duration = inputs.get("target_duration_seconds")

        duration_guidance = ""
        if target_duration:
            duration_guidance = (
                f"\nTarget total video duration: {target_duration} seconds. "
                f"Adjust scene count and pacing to fit this target."
            )

        prompt = self.PROMPT_TEMPLATE.format(
            channel_style=channel_style,
            duration_guidance=duration_guidance,
            script_text=script_text,
        )

        # Use Claude Sonnet for cost efficiency with structured output
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _SCENE_SCHEMA,
                },
            },
        )

        # Extract structured response
        response_text = response.content[0].text
        segment_plan = json.loads(response_text)

        # Validate scenes
        scenes = segment_plan.get("scenes", [])
        if not scenes:
            return ToolResult(
                success=False,
                error="Claude returned an empty scene list. Script may be too short or ambiguous.",
            )

        # Compute totals
        total_duration = sum(s.get("duration_seconds", 0) for s in scenes)
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)

        # Extract input/output token counts for cost tracking
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        actual_cost = (
            (input_tokens / 1_000_000) * self._INPUT_COST_PER_MTOK
            + (output_tokens / 1_000_000) * self._OUTPUT_COST_PER_MTOK
        )

        # Log to API logger
        try:
            from tools.api_logger import api_logger
            api_logger.log_call(
                tool=self.name,
                api="anthropic",
                endpoint="/v1/messages",
                input_units=input_tokens,
                input_unit_type="tokens",
                output_units=output_tokens,
                output_unit_type="tokens",
                cost_usd=round(actual_cost, 4),
                latency_ms=round((time.time() - (time.time() - 0.01)) * 1000),
                status=200,
                metadata={"stage": "segment", "scene_count": len(scenes)},
            )
        except Exception:
            pass  # logging is best-effort

        return ToolResult(
            success=True,
            data={
                "segment_plan": scenes,
                "scene_count": len(scenes),
                "total_duration_seconds": round(total_duration, 1),
                "total_words": total_words,
                "estimated_wpm": 150,
                "channel_style": channel_style,
                "cost_estimate": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "estimated_cost_usd": round(actual_cost, 4),
                },
            },
            model="claude-sonnet-4-6",
        )
