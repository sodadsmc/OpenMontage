"""Nano Banana 2 image generation + editing via the Google Gemini API.

"Nano Banana" is Google's Gemini image model line. Unlike Imagen (text-to-image
only, via the ``:predict`` endpoint), Nano Banana uses the Gemini
``:generateContent`` endpoint and supports REFERENCE/EDIT mode (inline source
images) — which is exactly what the Asset Bible needs: generate one canonical
reference image, then produce per-shot keyframes in ``edit`` mode that inherit
the locked look before image-to-video.

The model id is configurable via the ``NANO_BANANA_MODEL`` env var so it tracks
Google's current Nano Banana 2 id without a code change.
"""

from __future__ import annotations

import base64
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

# Default model id — override with NANO_BANANA_MODEL when Google's id changes.
DEFAULT_MODEL = "gemini-3-pro-image-preview"
COST_PER_IMAGE = 0.04

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _mime_for(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")


def _extract_image(response: dict) -> tuple[str | None, str]:
    """Pull the first inline image (base64, mime) out of a generateContent response.

    Tolerates both camelCase (REST) and snake_case (proto) key spellings.
    """
    for cand in response.get("candidates", []):
        content = cand.get("content", {}) or {}
        for part in content.get("parts", []) or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return inline["data"], mime
    return None, "image/png"


class NanoBananaImage(BaseTool):
    name = "nano_banana_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "nano_banana"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # checked dynamically via env var
    install_instructions = (
        "Set GEMINI_API_KEY (or GOOGLE_API_KEY) to your Google AI API key.\n"
        "  Get one at https://aistudio.google.com/apikey\n"
        "  Optionally set NANO_BANANA_MODEL to the current Nano Banana 2 model id "
        f"(default: {DEFAULT_MODEL})."
    )
    agent_skills = ["flux-best-practices"]

    capabilities = [
        "generate_image",
        "text_to_image",
        "image_edit",
        "reference_image",
    ]
    supports = {
        "image_edit": True,
        "reference_image": True,
        "multi_reference": True,
        "aspect_ratio": True,
        "seed": False,
        "negative_prompt": False,
    }
    best_for = [
        "consistent character/location references (Asset Bible canonical images)",
        "image-to-image edits that keep a locked subject",
        "per-shot keyframes derived from a canonical reference",
    ]
    not_good_for = [
        "deterministic/seeded reproduction (no seed control)",
        "offline generation",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Image description or edit instruction"},
            "generation_mode": {
                "type": "string",
                "enum": ["generate", "edit"],
                "default": "generate",
                "description": "Use 'edit' when providing one or more source images.",
            },
            "image_path": {"type": "string", "description": "Single local source image path (edit/reference mode)."},
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple local source image paths for multi-reference edits.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "3:4", "4:3", "9:16", "16:9"],
                "default": "16:9",
            },
            "model": {"type": "string", "description": "Override the model id for this call."},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "aspect_ratio", "generation_mode"]
    side_effects = ["writes image file to output_path", "calls Google Gemini API"]
    user_visible_verification = ["Inspect the generated image for relevance, period accuracy, and consistency"]

    def _get_api_key(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return COST_PER_IMAGE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="No Google API key found. " + self.install_instructions)

        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' is required")

        import requests

        start = time.time()
        model = inputs.get("model") or os.environ.get("NANO_BANANA_MODEL", DEFAULT_MODEL)
        aspect = inputs.get("aspect_ratio", "16:9")

        # Collect source images for edit / reference mode
        src_paths: list[str] = []
        if inputs.get("image_path"):
            src_paths.append(inputs["image_path"])
        if inputs.get("image_paths"):
            src_paths.extend(inputs["image_paths"])

        parts: list[dict[str, Any]] = []
        for sp in src_paths:
            p = Path(sp)
            if not p.exists():
                return ToolResult(success=False, error=f"Source image not found: {sp}")
            parts.append({
                "inlineData": {
                    "mimeType": _mime_for(p),
                    "data": base64.b64encode(p.read_bytes()).decode("ascii"),
                }
            })
        parts.append({"text": prompt})

        body: dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        if aspect:
            body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}

        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json=body,
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
            img_b64, mime = _extract_image(data)
            if img_b64 is None:
                return ToolResult(
                    success=False,
                    error=f"No image in Nano Banana response: {str(data)[:300]}",
                )
            ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
            output_path = Path(inputs.get("output_path") or f"nano_banana_image{ext}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(img_b64))
        except Exception as e:
            return ToolResult(success=False, error=f"Nano Banana generation failed: {e}")

        return ToolResult(
            success=True,
            data={
                "provider": "nano_banana",
                "model": model,
                "prompt": prompt,
                "generation_mode": inputs.get("generation_mode", "generate"),
                "aspect_ratio": aspect,
                "reference_images": src_paths,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
