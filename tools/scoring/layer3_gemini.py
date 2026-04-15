"""Layer 3 scoring: Gemini Pro video review.

Uploads candidate clips to Gemini Pro for comparative ranking on
relevance, tonal fit, technical quality, and usability (watermarks,
text overlays, faces). This is the most expensive gate — only send
clips that survived Layer 1 and Layer 2.

Requires a GOOGLE_API_KEY with Gemini API access.
"""
from __future__ import annotations

import json
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
    ToolTier,
)


class LayerThreeGemini(BaseTool):
    """Comparative video review via Gemini Pro.

    Inputs
    ------
    candidates : list[dict]
        Each dict must have ``clip_id`` (str) and ``path`` (str).
        Maximum 5 candidates per call to stay within API limits.
    visual_description : str
        What the scene should depict.
    mood : str
        Desired emotional tone (e.g. "somber", "urgent", "hopeful").
    scene_context : str
        Surrounding narrative context for tonal judgment.

    Returns
    -------
    ToolResult with ``data`` containing:
        ranked : list[dict]
            Candidates ranked best-to-worst, each with ``clip_id``,
            ``rank``, ``relevance_score``, ``tonal_fit_score``,
            ``technical_quality_score``, ``usability_score``,
            ``composite_score``, and ``reasoning``.
        best_clip_id : str
            The top-ranked clip ID for convenience.
    """

    name = "layer3_gemini"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "footage_scoring"
    provider = "google"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["python:google.generativeai", "env:GOOGLE_API_KEY"]
    install_instructions = (
        "Install the Google Generative AI SDK and set your API key:\n"
        "  pip install google-generativeai\n"
        "  export GOOGLE_API_KEY=your_key_here\n"
        "Get a key at https://aistudio.google.com/apikey"
    )

    capabilities = ["footage_scoring", "video_review"]
    best_for = [
        "subjective quality judgment",
        "tonal and mood assessment",
        "watermark and overlay detection",
        "comparative multi-clip ranking",
    ]
    not_good_for = [
        "fast first-pass filtering (use layer1_metadata)",
        "large batch scoring (cost adds up quickly)",
    ]

    input_schema = {
        "type": "object",
        "required": ["candidates", "visual_description", "mood", "scene_context"],
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": ["clip_id", "path"],
                    "properties": {
                        "clip_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
            },
            "visual_description": {"type": "string"},
            "mood": {"type": "string"},
            "scene_context": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=5.0,
        retryable_errors=["rate_limit", "timeout", "503", "429"],
    )
    side_effects = ["uploads video files to Google Gemini API"]

    MAX_CANDIDATES = 5

    # ------------------------------------------------------------------
    # Prompt template — version-controlled class constant
    # ------------------------------------------------------------------

    REVIEW_PROMPT_TEMPLATE = """\
You are an expert documentary editor reviewing candidate footage clips for a scene.

## Scene Requirements
- **Visual description**: {visual_description}
- **Mood / tone**: {mood}
- **Scene context**: {scene_context}

## Candidate Clips
{candidate_list}

## Task
Compare ALL candidate clips and rank them from best to worst for this scene.

For each clip, evaluate on four axes (score each 0.0 to 1.0):

1. **Relevance** — How well does the visual content match the description?
2. **Tonal fit** — Does the clip's mood, color palette, and pacing match the desired tone?
3. **Technical quality** — Resolution, stability, lighting, focus, compression artifacts.
4. **Usability** — Absence of watermarks, burned-in text overlays, faces that need releases, \
logos, or other elements that make the clip unusable in a documentary context.

## Output Format
Return ONLY valid JSON — no markdown fences, no commentary outside the JSON.

{{
  "ranked": [
    {{
      "clip_id": "<id>",
      "rank": 1,
      "relevance_score": 0.0,
      "tonal_fit_score": 0.0,
      "technical_quality_score": 0.0,
      "usability_score": 0.0,
      "composite_score": 0.0,
      "reasoning": "Brief explanation of ranking."
    }}
  ]
}}

Composite score = 0.35 * relevance + 0.25 * tonal_fit + 0.25 * technical_quality + 0.15 * usability.
Rank 1 is the best clip. Include ALL candidates in the output.
"""

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Approximate cost per call: $0.02-0.03 depending on clip count/duration."""
        n = len(inputs.get("candidates", []))
        # Base cost ~$0.01 for text, plus ~$0.005 per video clip uploaded
        return round(0.01 + n * 0.005, 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Gemini video upload + inference: ~15-30s depending on clip count."""
        n = len(inputs.get("candidates", []))
        return max(10.0, n * 6.0)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Upload clips to Gemini Pro and get comparative ranking."""
        start = time.time()

        # ---- Validate inputs ------------------------------------------------
        candidates = inputs.get("candidates")
        visual_description = inputs.get("visual_description")
        mood = inputs.get("mood")
        scene_context = inputs.get("scene_context")

        if not candidates:
            return ToolResult(success=False, error="No candidates provided.")
        if len(candidates) > self.MAX_CANDIDATES:
            return ToolResult(
                success=False,
                error=f"Maximum {self.MAX_CANDIDATES} candidates per call, got {len(candidates)}.",
            )
        if not visual_description:
            return ToolResult(success=False, error="visual_description is required.")
        if not mood:
            return ToolResult(success=False, error="mood is required.")
        if not scene_context:
            return ToolResult(success=False, error="scene_context is required.")

        # Verify all video files exist
        for c in candidates:
            if not Path(c["path"]).is_file():
                return ToolResult(
                    success=False,
                    error=f"Video file not found for {c['clip_id']}: {c['path']}",
                )

        # ---- Import and configure Gemini ------------------------------------
        try:
            import google.generativeai as genai  # type: ignore
            import os

            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                return ToolResult(
                    success=False,
                    error="GOOGLE_API_KEY not set. " + self.install_instructions,
                )
            genai.configure(api_key=api_key)
        except ImportError as exc:
            return ToolResult(
                success=False,
                error=f"Failed to import google.generativeai: {exc}",
            )

        # ---- Upload video files ---------------------------------------------
        uploaded_files: list[Any] = []
        try:
            for c in candidates:
                uploaded = genai.upload_file(
                    path=c["path"],
                    display_name=c["clip_id"],
                )
                # Wait for processing to complete
                while uploaded.state.name == "PROCESSING":
                    time.sleep(2)
                    uploaded = genai.get_file(uploaded.name)

                if uploaded.state.name == "FAILED":
                    return ToolResult(
                        success=False,
                        error=f"Gemini failed to process video for {c['clip_id']}.",
                    )
                uploaded_files.append(uploaded)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to upload video to Gemini: {exc}",
            )

        # ---- Build prompt ---------------------------------------------------
        candidate_list_str = "\n".join(
            f"- **Clip {i+1}** (ID: {c['clip_id']}): attached as video file"
            for i, c in enumerate(candidates)
        )

        prompt = self.REVIEW_PROMPT_TEMPLATE.format(
            visual_description=visual_description,
            mood=mood,
            scene_context=scene_context,
            candidate_list=candidate_list_str,
        )

        # ---- Call Gemini Pro ------------------------------------------------
        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            content_parts = uploaded_files + [prompt]
            response = model.generate_content(
                content_parts,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=2048,
                ),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Gemini API call failed: {exc}",
            )

        # ---- Clean up uploaded files ----------------------------------------
        for uf in uploaded_files:
            try:
                genai.delete_file(uf.name)
            except Exception:
                pass  # Best-effort cleanup

        # ---- Parse response -------------------------------------------------
        try:
            response_text = response.text.strip()
            # Strip markdown fences if Gemini wraps the JSON
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                # Remove first and last fence lines
                lines = [l for l in lines if not l.strip().startswith("```")]
                response_text = "\n".join(lines).strip()

            result_data = json.loads(response_text)
            ranked = result_data.get("ranked", [])
        except (json.JSONDecodeError, AttributeError) as exc:
            return ToolResult(
                success=False,
                error=f"Failed to parse Gemini response as JSON: {exc}. Raw: {response.text[:500]}",
            )

        if not ranked:
            return ToolResult(
                success=False,
                error="Gemini returned empty ranking.",
            )

        # Ensure ranking is sorted by rank
        ranked.sort(key=lambda x: x.get("rank", 999))
        best_clip_id = ranked[0].get("clip_id", "")

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "ranked": ranked,
                "best_clip_id": best_clip_id,
                "candidates_reviewed": len(candidates),
                "model": "gemini-2.0-flash",
            },
            duration_seconds=elapsed,
            cost_usd=self.estimate_cost(inputs),
            model="gemini-2.0-flash",
        )
