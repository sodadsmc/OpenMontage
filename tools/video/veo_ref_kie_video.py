"""Veo 3.1 REFERENCE_2_VIDEO via the Kie.ai aggregator — the hard-shot identity lane.

Veo's reference mode takes 1-3 reference IMAGES and renders a video whose subjects match
them — which is what the Nano+Grok lane structurally cannot guarantee: Grok animates a
single keyframe and re-invents any identity the keyframe undersells (the Therac-25 kept
drifting to a C-arm/CT-donut despite sheet+tokens+gold plate). This lane exists for the
few shots per episode where a recurring character AND the machine must both read on-model:
pass the beat's styled keyframe (pose/composition/amber style) plus the machine/character
model sheets as references, and identity is pinned BY CONSTRUCTION.

NOT the generic Kie Jobs API: Veo has a dedicated endpoint pair on Kie —
POST /api/v1/veo/generate -> taskId -> poll GET /api/v1/veo/record-info (successFlag
0=generating 1=success 2/3=failed; result in data.response.resultUrls). Clips are a fixed
4/6/8 s (default 8) with background audio; the conform stage trims to the word-timed slot.
Reference URLs MUST be freshly hosted — stored sheet URLs live on expiring temp hosts.
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

DEFAULT_BASE = "https://api.kie.ai"
VEO_MODEL = os.environ.get("VEO_KIE_MODEL", "veo3_fast")   # veo3 | veo3_fast | veo3_lite
# Kie prices Veo at ~25% of Google's rate; veo3_fast w/ audio ≈ $0.0375/s -> ~$0.30 per 8s clip.
_COST_PER_SECOND = {"veo3_fast": 0.04, "veo3": 0.19, "veo3_lite": 0.02}
_DURATIONS = (4, 6, 8)
MAX_REFS = 3                     # Veo 3.1 reference mode takes 1-3 images (Kie docs say 1-2 for
                                 # some modes — the adapter sends what it's given, capped at 3)
_POLL_INITIAL_WAIT = 15
_POLL_INTERVAL = 10
_MAX_POLL_TIME = 900             # Veo renders take minutes, not seconds
PROMPT_CAP = 2000


class VeoRefKieVideo(BaseTool):
    name = "veo_ref_kie_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "veo-ref-kie"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:KIE_API_KEY"]
    install_instructions = (
        "Set KIE_API_KEY in .env (same key as Grok/Kling/Nano Banana).\n"
        "  Veo 3.1 reference-to-video; ~$0.30-0.40 per 8s veo3_fast clip."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["reference_to_video"]
    supports = {
        "reference_to_video": True,   # the differentiator: identity refs, not a start frame
        "image_to_video": False,      # do NOT catch ordinary i2v traffic — grok-kie owns that
        "text_to_video": False,       # refs are REQUIRED — must not enter the plain-t2v pool
        "reference_image": True,
        "native_audio": True,
    }
    best_for = [
        "HARD shots where a recurring character and the machine must both stay on-model",
        "identity-pinned action (the reference images ARE the identity contract)",
    ]
    not_good_for = [
        "ordinary i2v beats (use grok-kie — 5-10x cheaper)",
        "first-last-frame state morphs (use kling-kie)",
    ]
    fallback_tools = ["grok_kie_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "reference_image_urls"],
        "properties": {
            "prompt": {"type": "string", "description": f"Shot description (<= {PROMPT_CAP} chars)"},
            "reference_image_urls": {
                "type": "array", "items": {"type": "string"},
                "description": f"1-{MAX_REFS} PUBLIC image URLs: the beat keyframe (style/pose) "
                               "first, then the machine/character model sheets (identity)",
            },
            "duration": {"type": "string", "enum": [str(d) for d in _DURATIONS], "default": "8"},
            "model": {"type": "string", "enum": ["veo3", "veo3_fast", "veo3_lite"], "default": VEO_MODEL},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"], "default": "16:9"},
            "resolution": {"type": "string", "enum": ["720p", "1080p"], "default": "720p"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout", "429"])
    idempotency_key_fields = ["prompt", "reference_image_urls", "duration", "model"]
    side_effects = ["writes video file to output_path", "calls Kie.ai Veo API"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KIE_API_KEY") else ToolStatus.UNAVAILABLE

    def _duration(self, inputs: dict[str, Any]) -> int:
        try:
            d = int(float(inputs.get("duration", "8")))
        except (TypeError, ValueError):
            d = 8
        # Snap UP to the nearest Veo duration so the conform trim has material.
        return min((v for v in _DURATIONS if v >= d), default=8)

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        rate = _COST_PER_SECOND.get(inputs.get("model", VEO_MODEL), 0.04)
        return round(rate * self._duration(inputs), 3)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 300.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not set. " + self.install_instructions)
        prompt = (inputs.get("prompt") or "")[:PROMPT_CAP]
        refs = [u for u in (inputs.get("reference_image_urls") or []) if u][:MAX_REFS]
        if not refs:
            return ToolResult(success=False, error="'reference_image_urls' (1-3 public URLs) is required")

        import requests

        start = time.time()
        base = (os.environ.get("KIE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        model = inputs.get("model", VEO_MODEL)
        payload: dict[str, Any] = {
            "prompt": prompt,
            "imageUrls": refs,
            "model": model,
            "generationType": "REFERENCE_2_VIDEO",
            "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
            "resolution": inputs.get("resolution", "720p"),
            "duration": self._duration(inputs),
        }

        try:
            r = requests.post(f"{base}/api/v1/veo/generate", headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            _hard = {401: "unauthorized", 402: "payment required (insufficient credits)",
                     403: "forbidden", 429: "too many requests (rate limit)"}
            if status in _hard:
                return ToolResult(success=False, error=f"Veo generate HARD STOP {status} {_hard[status]}: {e}")
            return ToolResult(success=False, error=f"Veo generate failed: {e}")
        if data.get("code") not in (200, 0, None):
            return ToolResult(success=False, error=f"Kie.ai Veo API error: {data.get('msg', data)}")
        task_id = (data.get("data") or {}).get("taskId")
        if not task_id:
            return ToolResult(success=False, error=f"No taskId in response: {str(data)[:200]}")

        video_url, err = self._poll(base, headers, task_id)
        if not video_url:
            return ToolResult(success=False, error=f"Veo generation failed (task {task_id}): {err or 'timed out'}")

        # Ledger BEFORE download: Kie charged us the moment the render succeeded — a local
        # download failure must not leave the spend unrecorded.
        try:
            from lib.cost_ledger import log as _cost_log
            _cost_log("veo-ref-kie", "reference_to_video", self.estimate_cost(inputs),
                      duration_s=self._duration(inputs), task=task_id, refs=len(refs))
        except Exception:  # noqa: BLE001
            pass

        output_path = self._download(video_url, inputs.get("output_path"))
        if not output_path:
            return ToolResult(success=False, error=f"Failed to download video from {video_url}")

        return ToolResult(
            success=True,
            data={"output": output_path, "provider": "veo-ref-kie", "model": model,
                  "operation": "reference_to_video", "task_id": task_id,
                  "video_url": video_url, "format": "mp4", "reference_count": len(refs)},
            artifacts=[output_path],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )

    def _poll(self, base: str, headers: dict, task_id: str) -> tuple[str | None, str | None]:
        """Poll the DEDICATED Veo record endpoint. successFlag: 0 generating, 1 success,
        2 failed, 3 created-but-generation-failed. Result URL in data.response.resultUrls."""
        import requests
        deadline = time.time() + _MAX_POLL_TIME
        time.sleep(_POLL_INITIAL_WAIT)
        last_err = None
        while time.time() < deadline:
            try:
                r = requests.get(f"{base}/api/v1/veo/record-info",
                                 params={"taskId": task_id}, headers=headers, timeout=15)
                r.raise_for_status()
                rec = (r.json() or {}).get("data") or {}
                flag = rec.get("successFlag")
                if flag == 1:
                    resp = rec.get("response") or {}
                    if isinstance(resp, str):          # defensively handle a JSON-encoded payload
                        try:
                            resp = json.loads(resp)
                        except Exception:  # noqa: BLE001
                            resp = {}
                    urls = (resp.get("resultUrls") or resp.get("fullResultUrls")
                            or resp.get("originUrls") or []) if isinstance(resp, dict) else []
                    if isinstance(urls, str):
                        try:
                            urls = json.loads(urls)
                        except Exception:  # noqa: BLE001
                            urls = [urls]
                    if not urls:
                        return None, "successFlag=1 but no resultUrls in response"
                    return urls[0], None
                if flag in (2, 3):
                    last_err = f"{rec.get('errorCode')} {rec.get('errorMessage')}"
                    _log.warning("veo_ref_kie_video: task %s failed: %s", task_id, last_err)
                    return None, last_err
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                _log.warning("veo_ref_kie_video: poll error for %s: %s", task_id, exc)
            time.sleep(_POLL_INTERVAL)
        return None, last_err

    @staticmethod
    def _download(url: str, output_path: str | None) -> str | None:
        import requests
        out = Path(output_path or f"veo_ref_{int(time.time())}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(url, stream=True, timeout=300)
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
            if out.stat().st_size > 1024:
                return str(out)
        except Exception as exc:  # noqa: BLE001
            _log.warning("veo_ref_kie_video: download failed: %s", exc)
        return None
