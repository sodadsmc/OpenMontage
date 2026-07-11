"""Gemini Omni Flash video adapter — first-party Google Interactions API.

⚠ EXPENSIVE + NO DRY RUNS: every Interactions call generates a FULL video and
bills for it — an innocuous probe prompt produced a default-length 10s clip
($1.00) during evaluation. $0.10/second of 720p. NEVER route automatically;
this adapter is reachable ONLY by explicit provider="omni-flash" or the
dashboard's "omni edit" button.

What it earns its price on (evaluated 2026-07-10, $2.27 session):
- COUNTED events / precise choreography: "fires EXACTLY TWICE" landed first
  try (brightness-curve verified: two 1.0s bursts, still beat between) after
  Grok failed the same beat 4/4 and Veo 3x. Judge on $/LANDED-take.
- SEQUENTIAL EDITS: pass interaction_id of a prior generation (or a video
  input) + an edit note -> a surgical revision (region-diff verified: only
  the named element changed). Up to 3 sequential edits per session. Caveat:
  sub-second timing drift is possible (a burst moved 0.6s in eval) — re-verify
  word-timed beats after an edit.
- Native synced audio (48kHz AAC) — bank the stem for the SFX/mix stage.

⚠ SAFETY GATE: Google blocks patient-harm reaction language ("he convulses",
even "flinches at each activation"). Keep humans passive in the prompt and
carry the reaction in a different lane, or stage the beat without people.

Clips are ≤10s (10s is also the billed DEFAULT when no duration is stated —
always state a duration in the prompt).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

_log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = os.environ.get("OMNI_FLASH_MODEL", "models/gemini-omni-flash-preview")
USD_PER_SECOND = 0.10
_TOKENS_PER_SECOND = 5792          # measured: 34752 tokens / 6.0s clip
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".mp4": "video/mp4"}


class OmniFlashVideo(BaseTool):
    name = "omni_flash_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "omni-flash"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:GOOGLE_API_KEY"]
    install_instructions = (
        "Set GOOGLE_API_KEY in .env (same key as the Gemini image lane).\n"
        "  $0.10/second of 720p video — EXPLICIT routing only, no dry runs."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["image_to_video", "video_edit"]
    supports = {
        # Everything False on purpose: this lane must NEVER be picked by the
        # selector's auto pools. Reach it with provider="omni-flash" only.
        "image_to_video": False,
        "text_to_video": False,
        "reference_to_video": False,
        "video_edit": True,
        "native_audio": True,
        "explicit_only": True,
    }
    best_for = [
        "COUNTED events / precise multi-phase choreography (fires EXACTLY twice)",
        "surgical edits of a near-approved clip (change X, keep everything else)",
    ]
    not_good_for = [
        "ordinary i2v beats (grok-kie is ~6x cheaper per second)",
        "patient-harm reactions (Google safety blocks them — stage passively)",
        "clips over 10 seconds",
    ]
    fallback_tools = ["veo_ref_kie_video", "grok_kie_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string",
                       "description": "Shot/edit description. ALWAYS state a duration "
                                      "(e.g. '6 seconds.') or you are billed for 10s. "
                                      "Keep humans passive (safety gate)."},
            "image_path": {"type": "string", "description": "Start frame (local path, sent inline)"},
            "video_path": {"type": "string", "description": "Input clip to EDIT (local path, <=10s)"},
            "previous_interaction_id": {"type": "string",
                                        "description": "Continue a prior omni session (sequential edit)"},
            "output_path": {"type": "string"},
        },
    }
    output_schema = {"type": "object"}
    examples = []

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # worst case if the prompt forgets a duration: the 10s default
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*second", inputs.get("prompt", ""))
        return round(float(m.group(1)) * USD_PER_SECOND, 2) if m else 1.00

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        key = os.environ.get("GOOGLE_API_KEY")
        if not key:
            return ToolResult(success=False, error="GOOGLE_API_KEY not set. " + self.install_instructions)
        prompt = (inputs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(success=False, error="'prompt' is required")

        content: list[dict] = [{"type": "text", "text": prompt}]
        for k, typ in (("image_path", "image"), ("video_path", "video")):
            p = inputs.get(k)
            if p:
                p = Path(p)
                if not p.is_file():
                    return ToolResult(success=False, error=f"{k} not found: {p}")
                mime = _MIME.get(p.suffix.lower())
                if not mime:
                    return ToolResult(success=False, error=f"unsupported {k} type: {p.suffix}")
                content.append({"type": typ, "mime_type": mime,
                                "data": base64.b64encode(p.read_bytes()).decode()})

        body: dict[str, Any] = {"model": MODEL,
                                "input": [{"type": "user_input", "content": content}]}
        if inputs.get("previous_interaction_id"):
            body["previous_interaction_id"] = inputs["previous_interaction_id"]

        start = time.time()
        req = urllib.request.Request(
            f"{_BASE}/interactions?key={key}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=900))
        except urllib.error.HTTPError as e:
            detail = e.read()[:400].decode(errors="replace")
            hint = ""
            if "Input blocked" in detail:
                hint = (" — Google's safety gate: remove human reaction/harm language "
                        "(keep people passive; even 'flinches' is blocked).")
            return ToolResult(success=False, error=f"omni-flash {e.code}: {detail}{hint}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"omni-flash request failed: {exc}")

        video_bytes = None
        for step in d.get("steps", []):
            for c in (step.get("content") or []):
                if c.get("type") == "video" and c.get("data"):
                    video_bytes = base64.b64decode(c["data"])
        if not video_bytes:
            return ToolResult(success=False,
                              error=f"omni-flash returned no video (status {d.get('status')})")

        out = Path(inputs.get("output_path") or f"omni_{d['id'][-8:]}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(video_bytes)

        vtokens = next((m["tokens"] for m in d.get("usage", {})
                        .get("output_tokens_by_modality", [])
                        if m.get("modality") == "video"), 0)
        seconds = round(vtokens / _TOKENS_PER_SECOND, 2) if vtokens else None
        cost = round((seconds or 10.0) * USD_PER_SECOND, 3)
        from lib.cost_ledger import log as _ledger_log
        _ledger_log("omni-flash",
                    "video_edit" if (inputs.get("video_path") or
                                     inputs.get("previous_interaction_id"))
                    else "image_to_video",
                    cost, note=f"{seconds}s, interaction {d['id'][-12:]}")

        return ToolResult(
            success=True,
            data={"output": str(out), "provider": "omni-flash",
                  "interaction_id": d["id"], "seconds": seconds,
                  "format": "mp4", "has_audio": True},
            artifacts=[str(out)],
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
        )
