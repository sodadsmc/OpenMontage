"""Runway Gen-4.5 AI video generation tool.

Primary AI video generation for narrated-documentary scenes — generates short
clips that depict the specific subject and action the narration describes (not
gap-fill). Uses the scene's ai_prompt with style modifiers for a dark/moody
documentary aesthetic.
"""

from __future__ import annotations

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

# Style modifier presets appended to prompts
_STYLE_MODIFIERS: dict[str, str] = {
    "cinematic_dark": (
        "Cinematic, dark moody lighting, shallow depth of field, "
        "desaturated color grade, documentary aesthetic. "
        "No text overlays, no human faces, no brand logos."
    ),
    "cinematic_neutral": (
        "Cinematic framing, neutral color grade, professional documentary style. "
        "No text overlays, no human faces, no brand logos."
    ),
    "tech_noir": (
        "Tech noir aesthetic, cool blue-green tones, high contrast, "
        "digital grain, circuit board textures in shadows. "
        "No text overlays, no human faces, no brand logos."
    ),
}

# Approximate cost per second of generated video
_COST_PER_SECOND = 0.10  # ~$0.50-1.00 per 5-10s clip


class RunwayGapFill(BaseTool):
    """Generates AI video clips via Runway Gen-4.5 to fill footage gaps."""

    name = "runway_gapfill"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "runway"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:RUNWAY_API_KEY"]
    install_instructions = (
        "Set RUNWAY_API_KEY to your Runway API secret:\n"
        "  export RUNWAY_API_KEY=your_key_here\n"
        "Get a key at https://dev.runwayml.com/\n"
        "Pricing: ~$0.50-1.00 per 5-10s generated clip."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["video_generation", "gap_fill"]
    supports = {
        "gap_fill": True,
        "style_modifiers": list(_STYLE_MODIFIERS.keys()),
        "duration_range": "5-10 seconds",
    }
    best_for = [
        "primary AI video generation for narrated-documentary scenes",
        "depicting the specific subject/action the narration describes",
        "creating AI visuals from a scene's ai_prompt",
    ]
    not_good_for = [
        "long-form video generation (>10s)",
        "real-time generation",
        "budget-constrained projects (use stock footage instead)",
    ]
    fallback_tools = ["kling_video", "veo_video", "minimax_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "duration_seconds", "output_path"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Scene ai_prompt depicting the narration's subject/action. "
                    "Should describe: shot type, subject, action, camera movement."
                ),
            },
            "duration_seconds": {
                "type": "number",
                "minimum": 5,
                "maximum": 10,
                "description": "Clip duration in seconds (5 or 10)",
            },
            "output_path": {
                "type": "string",
                "description": "Path to write the generated video clip",
            },
            "style": {
                "type": "string",
                "default": "cinematic_dark",
                "enum": ["cinematic_dark", "cinematic_neutral", "tech_noir"],
                "description": "Visual style modifier applied to the prompt",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        retryable_errors=["rate_limit", "timeout", "THROTTLED"],
    )
    idempotency_key_fields = ["prompt", "duration_seconds", "style"]
    side_effects = ["writes video file to output_path", "calls Runway API"]
    user_visible_verification = [
        "Watch generated clip for visual quality and documentary mood consistency",
    ]

    def get_status(self) -> ToolStatus:
        """Check for Runway API key."""
        if os.environ.get("RUNWAY_API_KEY") or os.environ.get("RUNWAYML_API_SECRET"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def _get_api_key(self) -> str | None:
        """Retrieve Runway API key from environment."""
        return os.environ.get("RUNWAY_API_KEY") or os.environ.get("RUNWAYML_API_SECRET")

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Estimate cost based on clip duration."""
        duration = inputs.get("duration_seconds", 5)
        return round(_COST_PER_SECOND * duration, 2)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Estimate generation time (Runway Gen-4.5 typically 30-90s)."""
        return 60.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Generate a gap-fill video clip via Runway Gen-4.5."""
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error=(
                    "RUNWAY_API_KEY not set. To use AI gap-filling:\n"
                    + self.install_instructions
                ),
            )

        start = time.time()

        try:
            result = self._generate(inputs, api_key)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Runway gap-fill generation failed: {exc}",
            )

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    def _build_prompt(self, raw_prompt: str, style: str) -> str:
        """Combine the raw prompt with style modifiers and safety guardrails."""
        style_suffix = _STYLE_MODIFIERS.get(style, _STYLE_MODIFIERS["cinematic_dark"])

        # Enforce prompt template: shot type, subject, action, camera movement
        # and things to avoid
        full_prompt = f"{raw_prompt.rstrip('.')}. {style_suffix}"

        return full_prompt

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        """Call Runway Gen-4.5 API to generate a gap-fill clip."""
        import requests

        raw_prompt = inputs["prompt"]
        duration = int(inputs.get("duration_seconds", 5))
        output_path = Path(inputs["output_path"])
        style = inputs.get("style", "cinematic_dark")

        # Runway API only accepts 5 or 10 second durations
        if duration not in (5, 10):
            duration = 5 if duration < 8 else 10

        full_prompt = self._build_prompt(raw_prompt, style)

        # Use gen4_turbo for gap-fill (balanced quality/cost)
        task_payload = {
            "model": "gen4_turbo",
            "promptText": full_prompt,
            "duration": duration,
            "ratio": "1280:720",  # 16:9 for documentary
            "watermark": False,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

        # Submit generation task
        submit_response = requests.post(
            "https://api.dev.runwayml.com/v1/text_to_video",
            headers=headers,
            json=task_payload,
            timeout=30,
        )
        submit_response.raise_for_status()
        task_id = submit_response.json()["id"]

        # Poll for completion (max ~5 minutes)
        video_url = None
        for _ in range(60):
            time.sleep(5)
            poll_response = requests.get(
                f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
                headers=headers,
                timeout=15,
            )
            poll_response.raise_for_status()
            task_data = poll_response.json()
            status = task_data["status"]

            if status == "SUCCEEDED":
                video_url = task_data["output"][0]
                break
            if status == "FAILED":
                failure_code = task_data.get("failureCode", "unknown")
                return ToolResult(
                    success=False,
                    error=(
                        f"Runway generation failed ({failure_code}): "
                        f"{task_data.get('failure', 'unknown error')}"
                    ),
                )
            # PENDING, THROTTLED, RUNNING — keep polling

        if not video_url:
            return ToolResult(
                success=False,
                error="Runway generation timed out after 5 minutes.",
            )

        # Download the generated video
        video_response = requests.get(video_url, timeout=120)
        video_response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(video_response.content)

        if not output_path.exists():
            return ToolResult(
                success=False,
                error=f"Failed to write output file: {output_path}",
            )

        return ToolResult(
            success=True,
            data={
                "provider": "runway",
                "model": "gen4_turbo",
                "operation": "gap_fill",
                "prompt": full_prompt,
                "raw_prompt": raw_prompt,
                "style": style,
                "duration_seconds": duration,
                "output": str(output_path),
                "output_path": str(output_path),
                "task_id": task_id,
                "format": "mp4",
                "cost_estimate_usd": self.estimate_cost(inputs),
            },
            artifacts=[str(output_path)],
            model="gen4_turbo",
        )
