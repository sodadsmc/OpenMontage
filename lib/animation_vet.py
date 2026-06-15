"""Gemini vetting for narration-synced animations.

Uploads a rendered animation whose AUDIO TRACK is the narration, and asks Gemini
to watch + listen together and report (1) narration<->animation SYNC mismatches
and (2) concrete POLISH + MOTION suggestions. This is the "does the picture match
the words, and where can we add motion" pass for diagram/animation beats — the
analogue of lib.quality_gate for AI footage.

Fail-soft: no GOOGLE_API_KEY / SDK / error -> returns {"skipped": ...} so a vet
call never blocks a build.

CLI:
  python -m lib.animation_vet <video.mp4> --align <seg.alignment.json>
  python -m lib.animation_vet <video.mp4> --narration "the spoken text..."
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"
UPLOAD_TIMEOUT_S = 240

VET_RUBRIC = """You are reviewing a narration-SYNCED ANIMATED DIAGRAM for a documentary. The video's AUDIO TRACK is the narration — watch the animation and listen to the narration TOGETHER, as a viewer would.

Assess two things, strictly and specifically:

1. NARRATION <-> ANIMATION SYNC. For each thing the narrator says, does the matching visual appear / draw / animate at the RIGHT MOMENT — as it is spoken, not seconds before or after? List every MISMATCH you can find: quote what the narrator says, describe what is on screen at that moment, and classify the issue as "early" (visual leads the words), "late" (visual lags), "missing" (narrated but never shown), or "unsupported" (on screen but never narrated). Be picky about timing — a reveal that lands 1-2s off is a real mismatch.

2. POLISH & MOTION. Give concrete, specific suggestions to make it more polished and MORE ENGAGING WITH MOTION. Call out every static stretch where purposeful motion would help — a draw-on, a beam/particle that travels, a pulse on the key object, a slow push/zoom, a transition, a label that animates in. Say WHAT to animate, WHERE (which beat / rough timestamp), and WHY it helps. Also flag anything cluttered, hard to read, or off-style.

The narration script is:
\"\"\"{narration}\"\"\"

Score sync 1-10 (10 = every beat lands on its word) and polish 1-10 (10 = nothing to add).
Return ONLY JSON:
{{"sync_score": N,
  "mismatches": [{{"narration": "...", "on_screen": "...", "issue": "early|late|missing|unsupported", "fix": "..."}}],
  "polish_score": N,
  "suggestions": [{{"where": "beat or ~timestamp", "issue": "...", "motion_idea": "what to animate and how"}}],
  "overall": "2-4 sentence verdict"}}
"""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    return json.loads(text)


def vet_animation(video_path: str | Path, narration: str,
                  model: str = _DEFAULT_MODEL) -> dict:
    """Vet a narration-synced animation with Gemini. Returns the parsed verdict
    (or {"skipped"/"error": ...} on a fail-soft path)."""
    try:
        import google.generativeai as genai
    except ImportError:
        return {"skipped": "google-generativeai not installed"}
    api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"skipped": "no GOOGLE_API_KEY/GEMINI_API_KEY"}

    video_path = Path(video_path)
    if not video_path.exists():
        return {"error": f"video not found: {video_path}"}

    uploaded = None
    try:
        genai.configure(api_key=api_key)
        gem = genai.GenerativeModel(
            model,
            generation_config=genai.types.GenerationConfig(
                temperature=0.4, max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        uploaded = genai.upload_file(path=str(video_path), display_name=video_path.stem)
        deadline = time.time() + UPLOAD_TIMEOUT_S
        while uploaded.state.name == "PROCESSING":
            if time.time() > deadline:
                raise TimeoutError(f"upload still PROCESSING after {UPLOAD_TIMEOUT_S}s")
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"upload ended in state {uploaded.state.name}")

        resp = gem.generate_content([uploaded, VET_RUBRIC.format(narration=narration.strip())])
        out = _parse_json(resp.text)
        out.setdefault("sync_score", 0)
        out.setdefault("polish_score", 0)
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("animation vet failed: %s", str(exc)[:200])
        return {"error": str(exc)[:200]}
    finally:
        if uploaded is not None:
            try:
                genai.delete_file(uploaded.name)
            except Exception:  # noqa: BLE001
                pass


def _print(v: dict) -> None:
    if v.get("skipped") or v.get("error"):
        print("VET:", v.get("skipped") or v.get("error"))
        return
    print(f"\n== ANIMATION VET ==  sync {v.get('sync_score')}/10   polish {v.get('polish_score')}/10")
    print(f"  {v.get('overall', '')}\n")
    mm = v.get("mismatches") or []
    print(f"  SYNC MISMATCHES ({len(mm)}):")
    for m in mm:
        print(f"   [{m.get('issue')}] narr: {str(m.get('narration', ''))[:70]}")
        print(f"        on-screen: {str(m.get('on_screen', ''))[:70]}")
        print(f"        fix: {str(m.get('fix', ''))[:90]}")
    sg = v.get("suggestions") or []
    print(f"\n  POLISH / MOTION SUGGESTIONS ({len(sg)}):")
    for s in sg:
        print(f"   @{s.get('where')}: {str(s.get('issue', ''))[:75]}")
        print(f"        -> {str(s.get('motion_idea', ''))[:95]}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m lib.animation_vet")
    ap.add_argument("video")
    ap.add_argument("--narration", help="narration text")
    ap.add_argument("--align", help="alignment JSON to pull narration text from")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    a = ap.parse_args(argv)
    try:
        import tools.base_tool  # noqa: F401  (loads .env)
    except ImportError:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    narration = a.narration or ""
    if a.align and not narration:
        d = json.loads(Path(a.align).read_text(encoding="utf-8"))
        narration = d.get("text", "")
    v = vet_animation(a.video, narration, model=a.model)
    if a.json:
        print(json.dumps(v, indent=2, ensure_ascii=False))
    else:
        _print(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
