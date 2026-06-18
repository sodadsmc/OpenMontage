"""Video template extractor — Gemini full-video analysis.

Uploads a complete video to Gemini and runs three focused analysis passes
to extract a reusable Production Bible:

1. **Structure & Narrative** — format type, hook, narrative arc, scene functions
2. **Visual & Production** — shot language, B-roll strategy, color, transitions
3. **Audio & Pacing** — music, narration style, SFX, silence, pacing rhythm

The output is a ``video_template`` artifact that describes HOW the video
works independent of its specific topic, plus a ``template_segment_plan``
with ``[TOPIC]`` placeholders ready for the narrated-documentary pipeline.

Requires a ``GOOGLE_API_KEY`` with Gemini API access.  Uses the full video
file (not keyframes) so Gemini can analyze motion, transitions, and audio.
Cost: ~$0.06 per 10-minute video with Gemini 2.5 Flash.

Supplements VideoAnalyzer's mechanical data (exact timestamps, motion
classification, audio energy) with semantic/creative understanding.
"""
from __future__ import annotations

import json
import logging
import os
import re
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

_log = logging.getLogger(__name__)


class TemplateExtractor(BaseTool):
    """Extract a reusable Production Bible from a reference video via Gemini."""

    name = "template_extractor"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "template_extraction"
    provider = "google"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["python:google.generativeai", "env:GOOGLE_API_KEY"]
    install_instructions = (
        "Set the GOOGLE_API_KEY environment variable:\n"
        "  export GOOGLE_API_KEY=your_key_here\n"
        "Get a key at https://aistudio.google.com/apikey"
    )
    agent_skills = ["video-understand"]

    input_schema = {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "YouTube URL or local video file path to analyze."
                ),
            },
            "analysis_brief_path": {
                "type": "string",
                "description": (
                    "Path to existing VideoAnalysisBrief JSON. If provided, "
                    "skips running VideoAnalyzer and reuses the brief."
                ),
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for output artifacts.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=2000, network_required=True,
    )
    retry_policy = RetryPolicy(
        max_retries=1,
        backoff_seconds=10.0,
        retryable_errors=["rate_limit", "timeout", "503", "429"],
    )
    side_effects = [
        "uploads video to Google Gemini API",
        "downloads video via yt-dlp if source is a URL",
    ]

    # ------------------------------------------------------------------
    # Gemini prompt templates
    # ------------------------------------------------------------------

    PASS1_STRUCTURE = """\
You are an expert video producer analyzing a reference video to extract its
reusable format as a Production Bible.

## Reference Video
**Title:** {title}
**Duration:** {duration_seconds:.0f} seconds
**Channel:** {channel}

### Full Transcript
{transcript}

### Scene Boundaries (from automated detection)
{scene_data}

## PASS 1: Structure & Narrative Analysis

Analyze the video's overall structure and narrative technique. Focus on:

1. **Format Classification**
   - primary_format: one of [narrator_over_footage, talking_head, animation, screen_recording, mixed, documentary, vlog, essay]
   - sub_format: more specific description
   - production_tier: one of [amateur, prosumer, professional, broadcast]

2. **Hook Analysis** (first 5-15 seconds)
   - technique: one of [dramatic_statement, question, statistic, cold_open, anecdote, contrast, mystery, visual_shock, quote]
   - duration_seconds: how long the hook lasts
   - description: what specifically happens
   - reusable_pattern: topic-agnostic description of this hook technique

3. **Narrative Arc**
   - structure_type: one of [linear_chronological, mystery_reveal, problem_solution, compare_contrast, thesis_evidence, journey, cause_effect, inverted_pyramid]
   - act_breakdown: array of acts with act_name, purpose, duration_pct (% of total)
   - tension_curve: description of how tension builds and releases

4. **Scene-by-Scene Template** — for each distinct segment:
   - scene_index: sequential number
   - timestamp_range: {{start, end}} in seconds
   - narrative_function: one of [hook, context_setting, introduce_subject, build_tension, escalation, crisis_peak, investigation, evidence, resolution, lesson, call_to_action, transition]
   - narration_summary: one sentence describing what's said
   - pacing: one of [establishing, escalation, crisis, resolution]
   - mood: the emotional tone
   - energy_level: one of [low, medium, high, peak]
   - duration_seconds: length of this segment
   - reusable_pattern: topic-agnostic description of what this slot should contain

5. **Retention Hooks** — moments designed to keep viewers watching
   - timestamp, technique, purpose

Return ONLY valid JSON matching this structure:
{{
  "format_classification": {{}},
  "hook_analysis": {{}},
  "narrative_arc": {{}},
  "scene_templates": [{{}}],
  "retention_hooks": [{{}}]
}}
"""

    PASS2_VISUAL = """\
You are analyzing the same reference video for its visual production strategy.

## PASS 2: Visual & Production Analysis

You MUST use EXACTLY these {scene_count} scenes from the structural analysis.
Do NOT create new scenes or subdivide further.

### Scenes to analyze:
{scene_list}

For each scene above, provide:
- scene_index: MUST match the indices above
- visual_strategy: one of [stock_footage, archival, diagram, text_card, animation, screen_recording, talking_head, b_roll_literal, b_roll_metaphorical, b_roll_atmospheric, generated_footage, mixed]
- b_roll_relationship: one of [literal, metaphorical, atmospheric, evidential, contrast]
- visual_description: MAX 8 words
- shot_language: {{shot_size, camera_movement, lighting_key}} — one word each

Also provide ONE overall_visual_strategy object:
- dominant_visual_type, color_palette ({{primary_colors: [max 3], mood}}), typography ({{has_on_screen_text, style, frequency}}), transition_patterns (max 3), motion_profile: one of [mostly_static, ken_burns, motion_clips, animation, mixed]

CRITICAL: Exactly {scene_count} entries in scene_visuals. 8 words max per visual_description.

Return ONLY valid JSON:
{{
  "scene_visuals": [
    {{
      "scene_index": 0,
      "visual_strategy": "",
      "b_roll_relationship": "",
      "visual_description": "",
      "shot_language": {{"shot_size": "", "camera_movement": "", "lighting_key": ""}}
    }}
  ],
  "overall_visual_strategy": {{}}
}}
"""

    PASS3_AUDIO = """\
You are analyzing the same reference video for its audio design and pacing.

## PASS 3: Audio Design & Pacing Analysis

Analyze the audio layers and narration technique:

1. **Narration Style**
   - tone: description of narrator's delivery
   - pace_wpm: estimated words per minute
   - vocabulary_level: one of [simple, accessible, technical, academic]
   - rhetorical_devices: list of techniques used (e.g., rhetorical questions, repetition, contrast)
   - person: one of [first, second, third, mixed]
   - emotional_register: how emotionally charged the delivery is
   - sentence_structure: patterns in sentence length and complexity

2. **Audio Design**
   - music_presence: true/false
   - music_style: genre/mood of the music
   - music_entry_point_seconds: when music first appears
   - ducking_pattern: how music relates to narration volume
   - sfx_usage: description of sound effect patterns
   - silence_strategy: how strategic pauses are used

3. **Pacing Analysis**
   - pattern_interrupt_frequency: estimated seconds between major changes
   - cut_rhythm: description of editing pace and variation
   - narration_density: how much of the runtime has active narration vs silence/music

4. **Strengths** — 3-5 things that make this video effective
5. **Weaknesses** — 2-3 areas that could be improved

Return ONLY valid JSON:
{{
  "narration_style": {{}},
  "audio_design": {{}},
  "pacing_analysis": {{}},
  "strengths": [],
  "weaknesses": []
}}
"""

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.06  # ~$0.05 upload + $0.005 per cached pass

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 120.0  # ~2 min for upload + 3 passes

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        source = inputs.get("source", "")
        output_dir = Path(inputs.get("output_dir", "projects/template_output"))
        output_dir.mkdir(parents=True, exist_ok=True)

        # ---- Load or run VideoAnalyzer ------------------------------------
        brief_path = inputs.get("analysis_brief_path")
        brief: dict[str, Any] = {}
        video_path: str | None = None

        if brief_path and Path(brief_path).is_file():
            with open(brief_path) as f:
                brief = json.load(f)
            # Try to find the downloaded video
            video_path = brief.get("source", {}).get("local_path")
        else:
            # Run VideoAnalyzer to get brief + downloaded video
            try:
                from tools.analysis.video_analyzer import VideoAnalyzer
                va = VideoAnalyzer()
                va_result = va.execute({
                    "source": source,
                    "analysis_depth": "standard",
                    "output_dir": str(output_dir / "video_analysis"),
                })
                if not va_result.success:
                    return ToolResult(
                        success=False,
                        error=f"VideoAnalyzer failed: {va_result.error}",
                    )
                brief = va_result.data or {}
                # Save brief
                brief_out = output_dir / "video_analysis_brief.json"
                with open(brief_out, "w") as f:
                    json.dump(brief, f, indent=2)
                video_path = brief.get("source", {}).get("local_path")
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"Failed to run VideoAnalyzer: {exc}",
                )

        # If we still don't have a video file, download it
        if not video_path or not Path(video_path).is_file():
            video_path = self._download_video(source, output_dir)
            if not video_path:
                return ToolResult(
                    success=False,
                    error="Could not obtain video file for Gemini upload.",
                )

        # ---- Extract metadata from brief -----------------------------------
        source_meta = brief.get("source", {})
        title = source_meta.get("title", Path(video_path).stem)
        channel = source_meta.get("uploader") or source_meta.get("channel", "Unknown")
        duration = float(source_meta.get("duration_seconds", 0))

        transcript = brief.get("narration_transcript", {}).get("full_text", "")
        if not transcript:
            segments = brief.get("narration_transcript", {}).get("segments", [])
            transcript = " ".join(s.get("text", "") for s in segments)

        scenes = brief.get("structure_analysis", {}).get("scenes", [])
        scene_data = json.dumps(scenes, indent=2) if scenes else "No scene data available."

        # ---- Configure Gemini -----------------------------------------------
        try:
            import google.generativeai as genai
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

        # ---- Upload full video to Gemini -----------------------------------
        _log.info("Uploading video to Gemini: %s", video_path)
        try:
            uploaded = genai.upload_file(
                path=video_path,
                display_name=f"template_ref_{Path(video_path).stem}",
            )
            while uploaded.state.name == "PROCESSING":
                time.sleep(3)
                uploaded = genai.get_file(uploaded.name)

            if uploaded.state.name == "FAILED":
                return ToolResult(
                    success=False,
                    error="Gemini failed to process the video file.",
                )
            _log.info("Video uploaded and active: %s", uploaded.name)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to upload video to Gemini: {exc}",
            )

        # ---- Run 3 analysis passes -----------------------------------------
        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=16384,
                response_mime_type="application/json",
            ),
        )

        fmt_kwargs = {
            "title": title,
            "duration_seconds": duration,
            "channel": channel,
            "transcript": transcript[:15000],  # cap to avoid token overflow
            "scene_data": scene_data[:5000],
        }

        pass1 = self._run_pass(model, uploaded, self.PASS1_STRUCTURE.format(**fmt_kwargs), "structure")

        # Build Pass 2 prompt with the exact scene list from Pass 1
        # so Gemini doesn't invent its own scene granularity
        scene_templates = pass1.get("scene_templates", []) if pass1 else []
        scene_list_str = "\n".join(
            f"- Scene {s.get('scene_index', i)}: "
            f"{s.get('timestamp_range', {}).get('start', '?')}s–"
            f"{s.get('timestamp_range', {}).get('end', '?')}s "
            f"({s.get('narrative_function', '')})"
            for i, s in enumerate(scene_templates)
        )
        pass2_prompt = self.PASS2_VISUAL.format(
            scene_count=len(scene_templates),
            scene_list=scene_list_str or "Use scenes from the video structure.",
        )
        pass2 = self._run_pass(model, uploaded, pass2_prompt, "visual")
        pass3 = self._run_pass(model, uploaded, self.PASS3_AUDIO, "audio")

        # ---- Cleanup uploaded video -----------------------------------------
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass

        if not pass1:
            return ToolResult(
                success=False,
                error="Gemini Pass 1 (structure) failed. Cannot extract template.",
            )

        # ---- Merge passes into video_template -------------------------------
        template = {
            "version": "1.0",
            "source_reference": {
                "title": title,
                "url": source,
                "duration_seconds": duration,
                "channel": channel,
            },
            "format_classification": pass1.get("format_classification", {}),
            "hook_analysis": pass1.get("hook_analysis", {}),
            "narrative_arc": pass1.get("narrative_arc", {}),
            "scene_templates": pass1.get("scene_templates", []),
            "retention_hooks": pass1.get("retention_hooks", []),
            "narration_style": pass3.get("narration_style", {}) if pass3 else {},
            "visual_strategy": pass2.get("overall_visual_strategy", {}) if pass2 else {},
            "audio_design": pass3.get("audio_design", {}) if pass3 else {},
            "strengths": pass3.get("strengths", []) if pass3 else [],
            "weaknesses": pass3.get("weaknesses", []) if pass3 else [],
        }

        # Merge per-scene visual data into scene_templates
        if pass2:
            scene_visuals = {sv["scene_index"]: sv for sv in pass2.get("scene_visuals", [])}
            for st in template["scene_templates"]:
                sv = scene_visuals.get(st.get("scene_index"))
                if sv:
                    st["visual_strategy"] = sv.get("visual_strategy", st.get("visual_strategy", ""))
                    st["b_roll_relationship"] = sv.get("b_roll_relationship", "")
                    st["visual_description"] = sv.get("visual_description", "")
                    st["shot_language"] = sv.get("shot_language", {})

        # Merge pacing analysis
        if pass3 and pass3.get("pacing_analysis"):
            template["pacing_analysis"] = pass3["pacing_analysis"]

        # ---- Generate template_segment_plan ---------------------------------
        segment_plan = self._generate_segment_plan(template)

        # ---- Save artifacts -------------------------------------------------
        template_path = output_dir / "video_template.json"
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)

        segment_plan_path = output_dir / "template_segment_plan.json"
        with open(segment_plan_path, "w", encoding="utf-8") as f:
            json.dump(segment_plan, f, indent=2, ensure_ascii=False)

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "video_template_path": str(template_path),
                "template_segment_plan_path": str(segment_plan_path),
                "format": template.get("format_classification", {}).get("primary_format", "unknown"),
                "scene_count": len(template.get("scene_templates", [])),
                "passes_completed": sum(1 for p in [pass1, pass2, pass3] if p),
                "model": "gemini-2.5-flash",
            },
            artifacts=[str(template_path), str(segment_plan_path)],
            duration_seconds=elapsed,
            cost_usd=self.estimate_cost(inputs),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_pass(
        model: Any,
        video_file: Any,
        prompt: str,
        pass_name: str,
    ) -> dict[str, Any] | None:
        """Run one Gemini analysis pass and parse the JSON response."""
        try:
            response = model.generate_content([video_file, prompt])
            text = response.text.strip()

            # Strip markdown fences if present
            if "```" in text:
                match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
                if match:
                    text = match.group(1).strip()
                else:
                    lines = text.split("\n")
                    lines = [l for l in lines if not l.strip().startswith("```")]
                    text = "\n".join(lines).strip()

            return json.loads(text)
        except Exception as exc:
            _log.warning("Gemini pass '%s' failed: %s", pass_name, exc)
            return None

    @staticmethod
    def _generate_segment_plan(template: dict[str, Any]) -> dict[str, Any]:
        """Convert video_template into a pipeline-ready segment_plan with placeholders."""
        scenes = []
        for st in template.get("scene_templates", []):
            idx = st.get("scene_index", len(scenes) + 1)

            # Build generic search queries from the visual strategy
            visual_strat = st.get("visual_strategy", "stock_footage")
            mood = st.get("mood", "neutral")
            queries = []
            if visual_strat in ("stock_footage", "b_roll_literal", "b_roll_atmospheric"):
                queries.append(f"[TOPIC] {mood} cinematic")
                queries.append(f"[TOPIC] {visual_strat.replace('_', ' ')}")
            elif visual_strat in ("archival", "diagram"):
                queries.append("[TOPIC] historical archive")
                queries.append("[TOPIC] diagram schematic")
            elif visual_strat == "text_card":
                queries.append("[TOPIC] key fact statistic")
            else:
                queries.append(f"[TOPIC] {mood}")

            scenes.append({
                "scene_id": f"scene_{idx:02d}",
                "narration": f"[TOPIC-SPECIFIC: {st.get('reusable_pattern', st.get('narration_summary', ''))}]",
                "duration_seconds": st.get("duration_seconds", 20),
                "pacing": st.get("pacing", "establishing"),
                "search_queries": queries,
                "visual_description": st.get("visual_description", f"[{visual_strat}] matching {mood} mood"),
                "ai_fallback_prompt": f"[GENERATE: {visual_strat} visual matching {mood} mood for [TOPIC]]",
                "mood": mood,
                "narrative_function": st.get("narrative_function", ""),
                # legacy alias; prefer narration_mode (Scene Library)
                "b_roll_relationship": st.get("b_roll_relationship", "literal"),
                "energy_level": st.get("energy_level", "medium"),
            })

        return {
            "version": "1.0",
            "scenes": scenes,
            "metadata": {
                "source": "template_extractor",
                "reference_title": template.get("source_reference", {}).get("title", ""),
                "reference_url": template.get("source_reference", {}).get("url", ""),
                "format": template.get("format_classification", {}).get("primary_format", ""),
                "narrative_arc": template.get("narrative_arc", {}).get("structure_type", ""),
            },
        }

    @staticmethod
    def _download_video(source: str, output_dir: Path) -> str | None:
        """Download video via yt-dlp if source is a URL."""
        import shutil
        import subprocess

        if not source.startswith(("http://", "https://")):
            return source if Path(source).is_file() else None

        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            _log.warning("yt-dlp not found — cannot download video")
            return None

        out_path = output_dir / "reference_video.mp4"
        try:
            subprocess.run(
                [
                    yt_dlp,
                    "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
                    "--merge-output-format", "mp4",
                    "-o", str(out_path),
                    source,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
            return str(out_path) if out_path.is_file() else None
        except Exception as exc:
            _log.warning("yt-dlp download failed: %s", exc)
            return None
