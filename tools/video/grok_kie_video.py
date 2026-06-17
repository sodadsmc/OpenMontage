"""Grok Imagine image-to-video via the Kie.ai aggregator.

Grok Imagine Video 1.5 (xAI) ranks #1 on the image-to-video Arena and is cheap
(~$0.017/s). Served via Kie.ai's unified Jobs API with the same KIE_API_KEY used
for Nano Banana images and the other Kie video models. Its strong image-anchoring
(it continues the reference frame rather than reinterpreting it) makes it ideal
for Asset-Bible-anchored documentary shots — and it needs no GPU box.

Flow (async): POST /api/v1/jobs/createTask -> taskId -> poll /api/v1/jobs/recordInfo
-> download the resulting video URL. Reference images are passed as public URLs
(the video_selector uploads a local reference via lib/image_host).
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

DEFAULT_BASE = "https://api.kie.ai"      # override with KIE_BASE_URL
I2V_MODEL = "grok-imagine/image-to-video"
T2V_MODEL = "grok-imagine/text-to-video"
# Kie's Grok Imagine wrapper accepts 6-30s per clip (Grok video-1.5, May 2026 —
# https://docs.kie.ai/market/grok-imagine/image-to-video). Longer single clips mean
# fewer split-and-concat seams per segment. GROK_KIE_MAX_SECONDS rolls back to 15
# if Kie ever rejects long requests (the failure surfaces as a createTask error).
MIN_DURATION = 6
MAX_DURATION = int(os.environ.get("GROK_KIE_MAX_SECONDS", "30"))
COST_PER_SECOND = 0.017
_POLL_INITIAL_WAIT = 6
_POLL_INTERVAL = 8
_MAX_POLL_TIME = 600


def _state_of(rec: dict) -> str:
    state = str(rec.get("state", "")).lower()
    flag = rec.get("successFlag")
    if state in ("success", "succeed", "succeeded", "completed") or flag == 1:
        return "success"
    if state in ("fail", "failed", "error") or flag in (2, 3):
        return "failed"
    return "pending"


def _extract_url(rec: dict) -> str | None:
    rj = rec.get("resultJson")
    if rj:
        try:
            parsed = json.loads(rj) if isinstance(rj, str) else rj
            urls = parsed.get("resultUrls") or parsed.get("resultUrl")
            if urls:
                return urls[0] if isinstance(urls, list) else urls
        except Exception:  # noqa: BLE001
            pass
    urls = rec.get("resultUrls")
    if isinstance(urls, str):
        try:
            urls = json.loads(urls)
        except Exception:  # noqa: BLE001
            urls = [urls]
    if urls:
        return urls[0]
    resp = rec.get("response") or {}
    urls = resp.get("resultUrls")
    if urls:
        return urls[0] if isinstance(urls, list) else urls
    return None


class GrokKieVideo(BaseTool):
    name = "grok_kie_video"
    version = "0.2.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "grok-kie"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_API_KEY"]
    install_instructions = (
        "Set KIE_API_KEY in .env (same key as Nano Banana + other Kie models).\n"
        "  Get one at https://kie.ai/api-key\n"
        "  Grok Imagine image-to-video (model grok-imagine/image-to-video), ~$0.017/s."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "image_to_video": True,
        "text_to_video": True,
        "reference_image": True,
        "native_audio": True,
        "cinematic_quality": True,
    }
    best_for = [
        "cheap high-quality image-to-video (#1 image-to-video Arena)",
        "shots anchored to a reference image (strong frame consistency)",
        "documentary b-roll at 720p without a GPU box",
    ]
    not_good_for = [
        "1080p/4K output (max 720p)",
        "faces under fast motion (softening)",
        "deterministic/seeded reproduction",
    ]
    fallback_tools = ["wan_video", "kie_video", "seedance_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Scene + motion description"},
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video"],
                "default": "image_to_video",
            },
            "image_url": {"type": "string", "description": "Reference image PUBLIC URL (i2v anchor)"},
            "image_urls": {
                "type": "array", "items": {"type": "string"},
                "description": "Reference image PUBLIC URLs (up to 7).",
            },
            "duration": {
                "type": "integer", "minimum": 6, "maximum": 30, "default": 6,
                "description": "Clip seconds (clamped to 6-30; cap via GROK_KIE_MAX_SECONDS).",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1", "2:3", "3:2"],
                "default": "16:9",
            },
            "resolution": {"type": "string", "enum": ["480p", "720p"], "default": "720p"},
            "mode": {"type": "string", "enum": ["normal", "fun", "spicy"], "default": "normal"},
            "model": {"type": "string", "description": "Override the Kie.ai model id"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "429"])
    idempotency_key_fields = ["prompt", "duration", "operation"]
    side_effects = ["writes video file to output_path", "calls Kie.ai Jobs API"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KIE_API_KEY") else ToolStatus.UNAVAILABLE

    def _duration(self, inputs: dict[str, Any]) -> int:
        try:
            d = int(float(inputs.get("duration", MIN_DURATION)))
        except (TypeError, ValueError):
            d = MIN_DURATION
        d = max(MIN_DURATION, min(MAX_DURATION, d))
        # Empirical (video-1.5 via Kie, June 2026): durations above 10s snap DOWN
        # to discrete steps — a 14s request returns a 10s clip, which then fails
        # the duration gate and freeze-pads 4s at assembly. Round UP to the next
        # supported step (10/15/20/25/30); the assembly trim cuts the excess, and
        # a few extra cents per shot beats a frozen tail.
        if d > 10:
            d = min(MAX_DURATION, ((d + 4) // 5) * 5)
        return d

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(max(0.10, COST_PER_SECOND * self._duration(inputs)), 3)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 90.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not set. " + self.install_instructions)
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' is required")

        import requests

        start = time.time()
        base = (os.environ.get("KIE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        urls: list[str] = []
        if inputs.get("image_url"):
            urls.append(inputs["image_url"])
        if inputs.get("image_urls"):
            urls.extend(inputs["image_urls"])
        if inputs.get("reference_image_path") or inputs.get("image_path"):
            _log.warning("grok_kie_video: ignoring local image path — Kie.ai needs public URLs")

        op = inputs.get("operation") or ("image_to_video" if urls else "text_to_video")
        model = inputs.get("model") or (I2V_MODEL if op == "image_to_video" else T2V_MODEL)
        dur = self._duration(inputs)

        input_obj: dict[str, Any] = {
            "prompt": prompt,
            "duration": dur,
            "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
            "resolution": inputs.get("resolution", "720p"),
            "mode": inputs.get("mode", "normal"),
        }
        if op == "image_to_video" and urls:
            input_obj["image_urls"] = urls[:7]

        try:
            r = requests.post(
                f"{base}/api/v1/jobs/createTask",
                headers=headers, json={"model": model, "input": input_obj}, timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            # Precise out-of-credits / auth / rate-limit detection: tag the error with a
            # phrase the quality gate treats as a HARD STOP (1 attempt, abort batch) so a
            # systemic failure can't burn 3 retries x every remaining shot.
            status = getattr(getattr(e, "response", None), "status_code", None)
            _hard = {401: "unauthorized", 402: "payment required (insufficient credits)",
                     403: "forbidden", 429: "too many requests (rate limit)"}
            if status in _hard:
                return ToolResult(success=False,
                                  error=f"Grok createTask HARD STOP {status} {_hard[status]}: {e}")
            return ToolResult(success=False, error=f"Grok createTask failed: {e}")
        if data.get("code") not in (200, 0, None):
            return ToolResult(success=False, error=f"Kie.ai API error: {data.get('msg', data)}")
        task_id = (data.get("data") or {}).get("taskId") or (data.get("data") or {}).get("task_id")
        if not task_id:
            return ToolResult(success=False, error=f"No taskId in response: {str(data)[:200]}")

        video_url, credits = self._poll(base, headers, task_id)
        if not video_url:
            return ToolResult(success=False, error=f"Grok generation timed out/failed (task {task_id})")

        output_path = self._download(video_url, inputs.get("output_path"))
        if not output_path:
            return ToolResult(success=False, error=f"Failed to download video from {video_url}")

        try:
            from lib.cost_ledger import log as _cost_log
            _cost_log("grok-kie", op, self.estimate_cost(inputs), credits=credits,
                      duration_s=dur, task=task_id)
        except Exception:  # noqa: BLE001
            pass

        return ToolResult(
            success=True,
            data={
                "output": output_path, "provider": "grok-kie", "model": model,
                "operation": op, "prompt": prompt, "duration_requested": dur,
                "task_id": task_id, "video_url": video_url, "format": "mp4",
            },
            artifacts=[output_path],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )

    def _poll(self, base: str, headers: dict, task_id: str) -> tuple[str | None, float | None]:
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
                st = _state_of(rec)
                if st == "success":
                    return _extract_url(rec), rec.get("creditsConsumed")
                if st == "failed":
                    _log.warning("grok_kie_video: task %s failed", task_id)
                    return None, rec.get("creditsConsumed")
            except Exception as exc:  # noqa: BLE001
                _log.warning("grok_kie_video: poll error for %s: %s", task_id, exc)
            time.sleep(_POLL_INTERVAL)
        return None, None

    @staticmethod
    def _download(url: str, output_path: str | None) -> str | None:
        import requests
        out = Path(output_path or f"grok_video_{int(time.time())}.mp4")
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
            _log.warning("grok_kie_video: download failed: %s", exc)
        return None
