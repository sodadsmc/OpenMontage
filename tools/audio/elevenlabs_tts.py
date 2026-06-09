"""ElevenLabs text-to-speech provider tool."""

from __future__ import annotations

import base64
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


class ElevenLabsTTS(BaseTool):
    name = "elevenlabs_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "elevenlabs"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set the ELEVENLABS_API_KEY environment variable:\n"
        "  export ELEVENLABS_API_KEY=your_key_here\n"
        "Get a key at https://elevenlabs.io"
    )
    fallback = "openai_tts"
    fallback_tools = ["openai_tts", "piper_tts"]
    agent_skills = ["elevenlabs", "text-to-speech"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
        "ssml_support",
        "pronunciation_control",
    ]
    supports = {
        "voice_cloning": True,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
    }
    best_for = [
        "high-quality narration",
        "voice-sensitive spokesperson videos",
        "multilingual spoken delivery",
    ]
    not_good_for = [
        "fully offline production",
        "privacy-constrained local-only workflows",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Text to convert to speech"},
            "voice_id": {
                "type": "string",
                "description": "ElevenLabs voice ID (default: Rachel)",
            },
            "model_id": {
                "type": "string",
                "default": "eleven_multilingual_v2",
                "description": "TTS model to use",
            },
            "stability": {
                "type": "number",
                "default": 0.5,
                "minimum": 0,
                "maximum": 1,
            },
            "similarity_boost": {
                "type": "number",
                "default": 0.75,
                "minimum": 0,
                "maximum": 1,
            },
            "style": {
                "type": "number",
                "default": 0.0,
                "minimum": 0,
                "maximum": 1,
            },
            "output_path": {"type": "string"},
            "output_format": {
                "type": "string",
                "default": "mp3_44100_128",
                "enum": ["mp3_44100_128", "mp3_44100_192", "pcm_16000", "pcm_24000"],
            },
            "pronunciation_dictionary_id": {
                "type": "string",
                "description": "ElevenLabs pronunciation dictionary ID for custom term pronunciation",
            },
            "pronunciation_dictionary_version_id": {
                "type": "string",
                "description": "Version ID of the pronunciation dictionary",
            },
            "with_timestamps": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Use the with-timestamps endpoint and save character-level "
                    "alignment data alongside the audio"
                ),
            },
            "timestamps_output_path": {
                "type": "string",
                "description": (
                    "Where to write the alignment JSON when with_timestamps is set "
                    "(default: output_path with .alignment.json suffix)"
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["text", "voice_id", "model_id"]
    side_effects = ["writes audio file to output_path", "calls ElevenLabs API"]
    user_visible_verification = ["Listen to generated audio for natural speech quality"]

    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

    def get_status(self) -> ToolStatus:
        if os.environ.get("ELEVENLABS_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(len(inputs.get("text", "")) * 0.0003, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="No ElevenLabs API key. " + self.install_instructions)

        start = time.time()
        try:
            result = self._generate(inputs, api_key)
        except Exception as exc:
            return ToolResult(success=False, error=f"TTS generation failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    @staticmethod
    def _sanitize_for_tts(text: str) -> str:
        """Clean text for natural TTS rendering.

        Replaces typographic characters that some TTS engines read
        literally (em-dash → "dash", curly quotes → silence) with
        plain equivalents that produce natural speech pauses.
        """
        replacements = {
            "\u2014": ", ",   # em-dash → comma pause
            "\u2013": ", ",   # en-dash → comma pause
            "\u2026": "...",  # horizontal ellipsis → three dots
            "\u2018": "'",    # left single curly quote
            "\u2019": "'",    # right single curly quote
            "\u201C": '"',    # left double curly quote
            "\u201D": '"',    # right double curly quote
            "\u2012": ", ",   # figure dash
            "\u2015": ", ",   # horizontal bar
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        return text

    @staticmethod
    def _build_tts_body(text: str, model_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Build the TTS request body, optionally with pronunciation dictionary."""
        body: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": inputs.get("stability", 0.5),
                "similarity_boost": inputs.get("similarity_boost", 0.75),
                "style": inputs.get("style", 0.0),
            },
        }

        # Attach pronunciation dictionary if provided
        pdict_id = inputs.get("pronunciation_dictionary_id")
        pdict_version = inputs.get("pronunciation_dictionary_version_id")
        if pdict_id and pdict_version:
            body["pronunciation_dictionary_locators"] = [
                {
                    "pronunciation_dictionary_id": pdict_id,
                    "version_id": pdict_version,
                }
            ]

        return body

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        import requests

        text = self._sanitize_for_tts(inputs["text"])
        voice_id = inputs.get("voice_id", self.DEFAULT_VOICE_ID)
        model_id = inputs.get("model_id", "eleven_multilingual_v2")
        output_format = inputs.get("output_format", "mp3_44100_128")
        with_timestamps = bool(inputs.get("with_timestamps", False))

        # The with-timestamps variant takes the SAME request body (so voice
        # settings and pronunciation dictionary locators still apply) but
        # returns JSON {audio_base64, alignment, normalized_alignment}
        # instead of raw audio bytes.
        endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        if with_timestamps:
            endpoint += "/with-timestamps"

        response = requests.post(
            endpoint,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json" if with_timestamps else "audio/mpeg",
            },
            json=self._build_tts_body(text, model_id, inputs),
            params={"output_format": output_format},
            timeout=120,
        )
        response.raise_for_status()

        ext = "mp3" if "mp3" in output_format else "wav"
        output_path = Path(inputs.get("output_path", f"tts_output.{ext}"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "provider": self.provider,
            "model": model_id,
            "voice_id": voice_id,
            "text_length": len(text),
            "output": str(output_path),
            "format": output_format,
        }
        artifacts = [str(output_path)]

        if with_timestamps:
            payload = response.json()
            audio_b64 = payload.get("audio_base64")
            if not audio_b64:
                return ToolResult(
                    success=False,
                    error="with-timestamps response missing audio_base64 field",
                )
            output_path.write_bytes(base64.b64decode(audio_b64))

            timestamps_path = Path(
                inputs.get("timestamps_output_path")
                or output_path.with_suffix(".alignment.json")
            )
            timestamps_path.parent.mkdir(parents=True, exist_ok=True)
            alignment_doc = {
                "text": text,
                "alignment": payload.get("alignment"),
                "normalized_alignment": payload.get("normalized_alignment"),
            }
            timestamps_path.write_text(
                json.dumps(alignment_doc, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            data["timestamps_path"] = str(timestamps_path)
            artifacts.append(str(timestamps_path))
        else:
            output_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            model=model_id,
        )
