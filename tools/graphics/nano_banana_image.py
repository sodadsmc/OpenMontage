"""Nano Banana 2 image generation via the Kie.ai aggregator.

"Nano Banana" is Google's Gemini image model line. We access it through Kie.ai's
unified Jobs API (the same KIE_API_KEY already used for hero video), which serves
Nano Banana 2 cheaply (text-to-image, and reference/edit when given image URLs).

Flow (async): POST /api/v1/jobs/createTask -> taskId -> poll /api/v1/jobs/recordInfo
-> download the resulting image URL.

NOTE: Kie.ai's edit/reference input (`image_input`) takes PUBLIC URLs and Kie.ai
provides no file-upload endpoint. So this tool only uses reference images when
given URLs; the documentary keyframe flow anchors on the locally-stored canonical
reference image directly (see lib/visual_router._nano_keyframe), which needs no
upload. Set KIE_BASE_URL / NANO_BANANA_MODEL to override defaults.
"""

from __future__ import annotations

import json
import logging
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

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "google/nano-banana"  # validated working on Kie.ai; override with NANO_BANANA_MODEL
DEFAULT_BASE = "https://api.kie.ai"   # override with KIE_BASE_URL
COST_PER_IMAGE = 0.04

_POLL_INITIAL_WAIT = 4
_POLL_INTERVAL = 6
_MAX_POLL_TIME = 300


def _extract_result_url(record_data: dict) -> str | None:
    """Pull the first result image URL out of a Kie.ai recordInfo `data` object.

    Tolerates the Jobs-API shape (resultJson string -> resultUrls) and the
    model-endpoint shape (resultUrls / response.resultUrls).
    """
    # Jobs API: resultJson is a JSON string with resultUrls
    rj = record_data.get("resultJson")
    if rj:
        try:
            parsed = json.loads(rj) if isinstance(rj, str) else rj
            urls = parsed.get("resultUrls") or parsed.get("resultUrl")
            if urls:
                return urls[0] if isinstance(urls, list) else urls
        except Exception:  # noqa: BLE001
            pass
    # Top-level resultUrls
    urls = record_data.get("resultUrls")
    if isinstance(urls, str):
        try:
            urls = json.loads(urls)
        except Exception:  # noqa: BLE001
            urls = [urls]
    if urls:
        return urls[0]
    # Nested response.resultUrls (veo-style)
    resp = record_data.get("response") or {}
    urls = resp.get("resultUrls")
    if urls:
        return urls[0] if isinstance(urls, list) else urls
    return None


def _state_of(record_data: dict) -> str:
    """Normalize Kie.ai job state to one of: success | failed | pending."""
    state = str(record_data.get("state", "")).lower()
    flag = record_data.get("successFlag")
    if state in ("success", "succeed", "succeeded", "completed") or flag == 1:
        return "success"
    if state in ("fail", "failed", "error") or flag in (2, 3):
        return "failed"
    return "pending"


class NanoBananaImage(BaseTool):
    name = "nano_banana_image"
    version = "0.2.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "nano_banana"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_API_KEY"]
    install_instructions = (
        "Set KIE_API_KEY in .env (same key as Kie.ai video):\n"
        "  KIE_API_KEY=your_key_here\n"
        "  Get one at https://kie.ai/api-key\n"
        "  Optional: NANO_BANANA_MODEL (default google/nano-banana), KIE_BASE_URL."
    )
    agent_skills = ["flux-best-practices"]

    capabilities = ["generate_image", "text_to_image", "image_edit", "reference_image"]
    supports = {
        "image_edit": True,          # reference via image_urls (public URLs only)
        "reference_image": True,
        "multi_reference": True,
        "aspect_ratio": True,
        "seed": False,
        "negative_prompt": False,
    }
    best_for = [
        "consistent character/location references (Asset Bible canonical images)",
        "cheap text-to-image via the Kie.ai aggregator",
        "reference edits when source images are already public URLs",
    ]
    not_good_for = [
        "editing a LOCAL image (Kie.ai requires public URLs, no upload endpoint)",
        "deterministic/seeded reproduction",
        "offline generation",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Image description (max ~20k chars)"},
            "generation_mode": {
                "type": "string",
                "enum": ["generate", "edit"],
                "default": "generate",
            },
            "image_url": {"type": "string", "description": "Single reference image PUBLIC URL"},
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reference image PUBLIC URLs (up to 14). Kie.ai needs URLs, not local files.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"],
                "default": "16:9",
            },
            "resolution": {"type": "string", "enum": ["1K", "2K", "4K"], "default": "2K"},
            "model": {"type": "string", "description": "Override the Kie.ai model id"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout", "429"])
    idempotency_key_fields = ["prompt", "aspect_ratio", "generation_mode"]
    side_effects = ["writes image file to output_path", "calls Kie.ai Jobs API"]
    user_visible_verification = ["Inspect the image for relevance, period accuracy, and consistency"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KIE_API_KEY") else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return COST_PER_IMAGE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not set. " + self.install_instructions)
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' is required")

        import requests

        start = time.time()
        # `or` chains so an empty env var (KEY= in .env) falls through to the default.
        base = (os.environ.get("KIE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        model = inputs.get("model") or os.environ.get("NANO_BANANA_MODEL") or DEFAULT_MODEL
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        input_obj: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
            "resolution": inputs.get("resolution", "2K"),
            "output_format": "png",
        }
        ref_urls: list[str] = []
        if inputs.get("image_url"):
            ref_urls.append(inputs["image_url"])
        if inputs.get("image_urls"):
            ref_urls.extend(inputs["image_urls"])
        # Local paths can't be used (Kie.ai needs public URLs) — warn and ignore.
        if inputs.get("image_path") or inputs.get("image_paths"):
            _log.warning("nano_banana(kie): ignoring local image_path(s) — Kie.ai needs public URLs")
        if ref_urls:
            input_obj["image_input"] = ref_urls

        # 1. Create task
        try:
            r = requests.post(
                f"{base}/api/v1/jobs/createTask",
                headers=headers,
                json={"model": model, "input": input_obj},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, error=f"Nano Banana createTask failed: {e}")
        if data.get("code") not in (200, 0, None):
            return ToolResult(success=False, error=f"Kie.ai API error: {data.get('msg', data)}")
        task_id = (data.get("data") or {}).get("taskId") or (data.get("data") or {}).get("task_id")
        if not task_id:
            return ToolResult(success=False, error=f"No taskId in response: {str(data)[:200]}")

        # 2. Poll
        image_url = self._poll(base, headers, task_id)
        if not image_url:
            return ToolResult(success=False, error=f"Nano Banana generation timed out/failed (task {task_id})")

        # 3. Download
        output_path = Path(inputs.get("output_path") or "nano_banana_image.png")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            dl = requests.get(image_url, timeout=120)
            dl.raise_for_status()
            output_path.write_bytes(dl.content)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, error=f"Image download failed: {e}")

        return ToolResult(
            success=True,
            data={
                "provider": "nano_banana", "model": model, "prompt": prompt,
                "generation_mode": inputs.get("generation_mode", "generate"),
                "aspect_ratio": input_obj["aspect_ratio"], "task_id": task_id,
                "image_url": image_url, "output": str(output_path),
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )

    def _poll(self, base: str, headers: dict, task_id: str) -> str | None:
        import requests
        deadline = time.time() + _MAX_POLL_TIME
        time.sleep(_POLL_INITIAL_WAIT)
        while time.time() < deadline:
            try:
                r = requests.get(
                    f"{base}/api/v1/jobs/recordInfo",
                    params={"taskId": task_id}, headers=headers, timeout=15,
                )
                r.raise_for_status()
                rec = r.json().get("data", {}) or {}
                state = _state_of(rec)
                if state == "success":
                    return _extract_result_url(rec)
                if state == "failed":
                    _log.warning("nano_banana(kie): task %s failed", task_id)
                    return None
            except Exception as exc:  # noqa: BLE001
                _log.warning("nano_banana(kie): poll error for %s: %s", task_id, exc)
            time.sleep(_POLL_INTERVAL)
        return None
