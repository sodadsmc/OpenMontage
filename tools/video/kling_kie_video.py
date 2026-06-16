"""Kling (Kuaishou) image-to-video via the Kie.ai aggregator — with FIRST-LAST-FRAME.

Kling 3.0 on Kie is the controllable lane for EXPLANATORY beats: supply a START frame
and an END frame and the model only interpolates between them, so exact object counts,
per-object state, a locked camera, and the channel style are all pinned BY CONSTRUCTION
(the precision lives in the two hand-authored keyframes, not the model). This is what
open i2v (Grok) structurally cannot do — Grok has no end-frame input.

Same Kie Jobs API as the Grok adapter: POST /api/v1/jobs/createTask -> taskId -> poll
/api/v1/jobs/recordInfo. FLF is passed as a 2-element ``image_urls`` array [start, end]
with ``multi_shots: false`` (single image_urls = ordinary first-frame i2v). The poll
state machine + resultJson parsing are shared with grok_kie_video.
"""
from __future__ import annotations

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
# Reuse the proven Kie Jobs-API poll helpers (state-based; reads resultJson.resultUrls).
from tools.video.grok_kie_video import _extract_url, _state_of

_log = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.kie.ai"
FLF_MODEL = "kling-3.0/video"          # the Kie Kling model that honors a [first, last] image_urls array
# Kling 3.0 modes -> resolution; std=720p matches the rest of the pipeline and is cheapest.
_COST_PER_SECOND = {"std": 0.084, "pro": 0.168, "4K": 0.34}  # USD/s, approx (std ~14 Kie credits/s)
MIN_DURATION, MAX_DURATION = 3, 15
_POLL_INITIAL_WAIT = 6
_POLL_INTERVAL = 8
_MAX_POLL_TIME = 600
PROMPT_CAP = 500                       # Kling 3.0 per-shot prompt cap on Kie


class KlingKieVideo(BaseTool):
    name = "kling_kie_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "kling-kie"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_API_KEY"]
    install_instructions = (
        "Set KIE_API_KEY in .env (same key as Grok/Nano Banana).\n"
        "  Kling 3.0 image-to-video with first-last-frame; ~$0.084/s std (720p)."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["image_to_video", "first_last_frame"]
    supports = {
        "image_to_video": True,
        "first_last_frame": True,      # the differentiator vs grok-kie
        "reference_image": True,
        "native_audio": True,
    }
    best_for = [
        "EXPLANATORY beats needing exact counts / per-object state / a locked camera",
        "first-last-frame interpolation between two hand-authored keyframes",
        "controllable transitions Grok i2v cannot direct",
    ]
    not_good_for = ["free atmospheric motion (use grok-kie)", "multi-step arcs in one clip"]
    fallback_tools = ["grok_kie_video", "kling_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "image_url"],
        "properties": {
            "prompt": {"type": "string", "description": f"Transition description (<= {PROMPT_CAP} chars; no negative_prompt on Kling 3.0)"},
            "image_url": {"type": "string", "description": "START frame PUBLIC URL"},
            "end_image_url": {"type": "string", "description": "END frame PUBLIC URL — enables first-last-frame"},
            "duration": {"type": "string", "enum": [str(d) for d in range(MIN_DURATION, MAX_DURATION + 1)], "default": "5"},
            "mode": {"type": "string", "enum": ["std", "pro", "4K"], "default": "std"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"], "default": "16:9"},
            "sound": {"type": "boolean", "default": False},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "429"])
    idempotency_key_fields = ["prompt", "image_url", "end_image_url", "duration"]
    side_effects = ["writes video file to output_path", "calls Kie.ai Jobs API"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KIE_API_KEY") else ToolStatus.UNAVAILABLE

    def _duration(self, inputs: dict[str, Any]) -> str:
        try:
            d = int(float(inputs.get("duration", "5")))
        except (TypeError, ValueError):
            d = 5
        return str(max(MIN_DURATION, min(MAX_DURATION, d)))

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        rate = _COST_PER_SECOND.get(inputs.get("mode", "std"), 0.084)
        return round(rate * int(self._duration(inputs)), 3)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 120.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not set. " + self.install_instructions)
        prompt = (inputs.get("prompt") or "")[:PROMPT_CAP]
        start_url = inputs.get("image_url")
        if not start_url:
            return ToolResult(success=False, error="'image_url' (start frame) is required")

        import requests

        start = time.time()
        base = (os.environ.get("KIE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # FLF: image_urls = [start, end]; first-frame-only: [start]. multi_shots MUST be present.
        image_urls = [start_url]
        end_url = inputs.get("end_image_url")
        if end_url:
            image_urls.append(end_url)
        input_obj: dict[str, Any] = {
            "prompt": prompt,
            "image_urls": image_urls,
            "multi_shots": False,
            "mode": inputs.get("mode", "std"),
            "duration": self._duration(inputs),
            "sound": bool(inputs.get("sound", False)),
        }
        if inputs.get("aspect_ratio"):
            input_obj["aspect_ratio"] = inputs["aspect_ratio"]

        try:
            r = requests.post(f"{base}/api/v1/jobs/createTask", headers=headers,
                              json={"model": FLF_MODEL, "input": input_obj}, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            _hard = {401: "unauthorized", 402: "payment required (insufficient credits)",
                     403: "forbidden", 429: "too many requests (rate limit)"}
            if status in _hard:
                return ToolResult(success=False, error=f"Kling createTask HARD STOP {status} {_hard[status]}: {e}")
            return ToolResult(success=False, error=f"Kling createTask failed: {e}")
        if data.get("code") not in (200, 0, None):
            return ToolResult(success=False, error=f"Kie.ai API error: {data.get('msg', data)}")
        task_id = (data.get("data") or {}).get("taskId")
        if not task_id:
            return ToolResult(success=False, error=f"No taskId in response: {str(data)[:200]}")

        video_url, credits = self._poll(base, headers, task_id)
        if not video_url:
            return ToolResult(success=False, error=f"Kling generation timed out/failed (task {task_id})")

        output_path = self._download(video_url, inputs.get("output_path"))
        if not output_path:
            return ToolResult(success=False, error=f"Failed to download video from {video_url}")

        return ToolResult(
            success=True,
            data={"output": output_path, "provider": "kling-kie", "model": FLF_MODEL,
                  "operation": "first_last_frame" if end_url else "image_to_video",
                  "task_id": task_id, "video_url": video_url, "format": "mp4",
                  "credits_consumed": credits},
            artifacts=[output_path],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=FLF_MODEL,
        )

    def _poll(self, base: str, headers: dict, task_id: str) -> tuple[str | None, float | None]:
        import requests
        deadline = time.time() + _MAX_POLL_TIME
        time.sleep(_POLL_INITIAL_WAIT)
        while time.time() < deadline:
            try:
                r = requests.get(f"{base}/api/v1/jobs/recordInfo",
                                 params={"taskId": task_id}, headers=headers, timeout=15)
                r.raise_for_status()
                rec = r.json().get("data", {}) or {}
                st = _state_of(rec)
                if st == "success":
                    return _extract_url(rec), rec.get("creditsConsumed")
                if st == "failed":
                    _log.warning("kling_kie_video: task %s failed: %s %s",
                                 task_id, rec.get("failCode"), rec.get("failMsg"))
                    return None, rec.get("creditsConsumed")
            except Exception as exc:  # noqa: BLE001
                _log.warning("kling_kie_video: poll error for %s: %s", task_id, exc)
            time.sleep(_POLL_INTERVAL)
        return None, None

    @staticmethod
    def _download(url: str, output_path: str | None) -> str | None:
        import requests
        out = Path(output_path or f"kling_flf_{int(time.time())}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(url, stream=True, timeout=180)
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
            if out.stat().st_size > 1024:
                return str(out)
        except Exception as exc:  # noqa: BLE001
            _log.warning("kling_kie_video: download failed: %s", exc)
        return None
