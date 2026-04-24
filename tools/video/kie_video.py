"""Kie.ai video generation adapter.

Kie.ai is an API aggregator providing access to Veo 3.1, Runway,
Kling, and Seedance through a single API key at 30-50% lower pricing
than direct provider APIs.

Supports text-to-video and image-to-video generation with async
task polling.  Generated videos are stored for 14 days.

Requires KIE_API_KEY environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

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

_VEO_GENERATE_URL = "https://api.kie.ai/api/v1/veo/generate"
_VEO_STATUS_URL = "https://api.kie.ai/api/v1/veo/record-info"
_RUNWAY_GENERATE_URL = "https://api.kie.ai/api/v1/runway/generate"
_RUNWAY_STATUS_URL = "https://api.kie.ai/api/v1/runway/record-info"


class KieVideo(BaseTool):
    """Generate video clips via Kie.ai (Veo 3.1, Runway, Kling)."""

    name = "kie_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "kie"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_API_KEY"]
    install_instructions = (
        "Set KIE_API_KEY in .env:\n"
        "  KIE_API_KEY=your_key_here\n"
        "Get a key at https://kie.ai/api-key"
    )

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the video to generate (max 1800 chars)",
            },
            "model": {
                "type": "string",
                "enum": ["veo3", "veo3_fast", "veo3_lite", "runway"],
                "default": "veo3_fast",
                "description": "Generation model. veo3=highest quality, veo3_fast=balanced, veo3_lite=cheapest, runway=best camera control",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "default": "16:9",
            },
            "duration": {
                "type": "integer",
                "enum": [5, 10],
                "default": 5,
                "description": "Video duration in seconds (Runway only: 5 or 10)",
            },
            "resolution": {
                "type": "string",
                "enum": ["720p", "1080p"],
                "default": "720p",
            },
            "image_url": {
                "type": "string",
                "description": "Optional reference image URL for image-to-video",
            },
            "output_path": {
                "type": "string",
                "description": "Where to save the downloaded video",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500, network_required=True,
    )
    retry_policy = RetryPolicy(
        max_retries=1, retryable_errors=["timeout", "429"],
    )

    # Poll settings
    _POLL_INITIAL_WAIT = 10   # seconds before first check
    _POLL_INTERVAL = 15       # seconds between subsequent checks
    _MAX_POLL_TIME = 900      # 15 minutes max wait

    def get_status(self):
        from tools.base_tool import ToolStatus
        if not os.environ.get("KIE_API_KEY"):
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", "veo3_fast")
        costs = {"veo3": 0.80, "veo3_fast": 0.40, "veo3_lite": 0.20, "runway": 1.50}
        return costs.get(model, 0.50)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not set. " + self.install_instructions)

        start = time.time()
        model = inputs.get("model", "veo3_fast")
        prompt = inputs.get("prompt", "")
        if not prompt:
            return ToolResult(success=False, error="prompt is required")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Route to correct endpoint
        if model == "runway":
            return self._generate_runway(inputs, headers, start)
        else:
            return self._generate_veo(inputs, headers, start)

    def _generate_veo(self, inputs: dict[str, Any], headers: dict, start: float) -> ToolResult:
        """Generate via Veo 3.1 endpoint."""
        body: dict[str, Any] = {
            "prompt": inputs["prompt"],
            "model": inputs.get("model", "veo3_fast"),
            "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
            "resolution": inputs.get("resolution", "720p"),
        }

        if inputs.get("image_url"):
            body["imageUrls"] = [inputs["image_url"]]
            body["generationType"] = "REFERENCE_2_VIDEO"

        # Submit generation task
        try:
            r = requests.post(_VEO_GENERATE_URL, headers=headers, json=body, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return ToolResult(success=False, error=f"Veo generation request failed: {exc}")

        if data.get("code") != 200:
            return ToolResult(success=False, error=f"Veo API error: {data.get('msg', data)}")

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            return ToolResult(success=False, error="No taskId in response")

        _log.info("Veo task submitted: %s", task_id)

        # Poll for completion
        video_url = self._poll_veo(task_id, headers)
        if not video_url:
            return ToolResult(success=False, error=f"Veo generation timed out or failed (task: {task_id})")

        # Download video
        output_path = self._download_video(video_url, inputs.get("output_path"))
        if not output_path:
            return ToolResult(success=False, error=f"Failed to download video from {video_url}")

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "output": output_path,
                "model": inputs.get("model", "veo3_fast"),
                "task_id": task_id,
                "video_url": video_url,
                "provider": "kie.ai/veo",
            },
            artifacts=[output_path],
            duration_seconds=elapsed,
            cost_usd=self.estimate_cost(inputs),
        )

    def _generate_runway(self, inputs: dict[str, Any], headers: dict, start: float) -> ToolResult:
        """Generate via Runway endpoint."""
        body: dict[str, Any] = {
            "prompt": inputs["prompt"],
            "duration": inputs.get("duration", 5),
            "quality": inputs.get("resolution", "720p"),
            "aspectRatio": inputs.get("aspect_ratio", "16:9"),
            "waterMark": "",
            "callBackUrl": "",
        }

        if inputs.get("image_url"):
            body["imageUrl"] = inputs["image_url"]

        try:
            r = requests.post(_RUNWAY_GENERATE_URL, headers=headers, json=body, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return ToolResult(success=False, error=f"Runway generation request failed: {exc}")

        if data.get("code") != 200:
            return ToolResult(success=False, error=f"Runway API error: {data.get('msg', data)}")

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            return ToolResult(success=False, error="No taskId in response")

        _log.info("Runway task submitted: %s", task_id)

        # Poll for completion
        video_url = self._poll_runway(task_id, headers)
        if not video_url:
            return ToolResult(success=False, error=f"Runway generation timed out or failed (task: {task_id})")

        output_path = self._download_video(video_url, inputs.get("output_path"))
        if not output_path:
            return ToolResult(success=False, error=f"Failed to download video from {video_url}")

        elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "output": output_path,
                "model": "runway",
                "task_id": task_id,
                "video_url": video_url,
                "provider": "kie.ai/runway",
            },
            artifacts=[output_path],
            duration_seconds=elapsed,
            cost_usd=self.estimate_cost(inputs),
        )

    def _poll_veo(self, task_id: str, headers: dict) -> str | None:
        """Poll Veo task status until complete. Returns video URL or None."""
        deadline = time.time() + self._MAX_POLL_TIME
        time.sleep(self._POLL_INITIAL_WAIT)  # first wait is shorter

        while time.time() < deadline:
            try:
                r = requests.get(
                    _VEO_STATUS_URL,
                    params={"taskId": task_id},
                    headers=headers,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json().get("data", {})

                flag = data.get("successFlag", 0)
                if flag == 1:  # Success
                    # URLs may be at top level or nested in response object
                    response_obj = data.get("response", {})
                    urls = (
                        response_obj.get("resultUrls")
                        or data.get("resultUrls")
                        or []
                    )
                    if isinstance(urls, str):
                        urls = json.loads(urls)
                    if urls:
                        return urls[0]
                elif flag in (2, 3):  # Failed
                    _log.warning("Veo task %s failed (flag=%d)", task_id, flag)
                    return None
                # flag 0 = still generating
                _log.info("Veo task %s still generating...", task_id)
            except Exception as exc:
                _log.warning("Poll error for %s: %s", task_id, exc)
            time.sleep(self._POLL_INTERVAL)

        _log.warning("Veo task %s timed out after %ds", task_id, self._MAX_POLL_TIME)
        return None

    def _poll_runway(self, task_id: str, headers: dict) -> str | None:
        """Poll Runway task status until complete."""
        deadline = time.time() + self._MAX_POLL_TIME
        time.sleep(self._POLL_INITIAL_WAIT)

        while time.time() < deadline:
            try:
                r = requests.get(
                    _RUNWAY_STATUS_URL,
                    params={"taskId": task_id},
                    headers=headers,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json().get("data", {})

                video_url = data.get("video_url") or data.get("videoUrl")
                if video_url:
                    return video_url

                status = data.get("status", "")
                if status in ("failed", "error"):
                    _log.warning("Runway task %s failed", task_id)
                    return None

                _log.info("Runway task %s still generating...", task_id)
            except Exception as exc:
                _log.warning("Poll error for %s: %s", task_id, exc)
            time.sleep(self._POLL_INTERVAL)

        return None

    @staticmethod
    def _download_video(url: str, output_path: str | None = None) -> str | None:
        """Download video from URL to local path."""
        if not output_path:
            output_path = f"generated_video_{int(time.time())}.mp4"

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
            if out.stat().st_size > 1024:
                return str(out)
        except Exception as exc:
            _log.warning("Download failed: %s", exc)

        return None
