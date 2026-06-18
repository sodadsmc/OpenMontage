"""Director pass — turn human review INTENT into a rule-compliant revision.

This is the seam the regenerate flow runs through: the human's notes are not
injected into a prompt verbatim. They are handed to the DIRECTOR, which re-derives
a depiction-first plan under the channel rules (lane decision tree, motion = the
narration verb, keyframe-carries-content, the Grok-screen trap, name the subject)
and emits a structured `described_action` + a revised prompt + a rationale. The UI
shows that as a pre-spend approval card before anything is generated.

Uses Gemini (the repo standard) when GOOGLE_API_KEY is present; degrades to a
deterministic restatement so the flow still works offline. When it degrades, the
reason is attached as `_error` so the operator can tell key/network/parse apart.

Config:
    OPENMONTAGE_DIRECTOR_MODEL   gemini model id (default gemini-2.5-flash)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re

_DEFAULT_MODEL = "gemini-2.5-flash"


def _model_name() -> str:
    return os.environ.get("OPENMONTAGE_DIRECTOR_MODEL", _DEFAULT_MODEL)


RULES = """\
You are the DIRECTOR for an AI-generated, depiction-first narrated documentary
(hand-inked graphic-novel style). A scene is GENERATED to DEPICT what the
narration literally describes — not atmospheric b-roll.

The human reviewer's notes are INTENT. Do NOT paste them into the prompt. Re-derive
a rule-compliant plan that satisfies the intent. Apply these rules:

LANE DECISION TREE (pick exactly one primary lane per scene; beats inside a scene
may split — that is a "mixed" scene):
- MANIM/sketch: a mechanism, a labeled diagram, or a precise taught count.
- FLF state-morph: ONE element changes to a specific new state the viewer must
  watch (a glyph X->E, an error code appearing, a needle to a reading, 6->3).
- FLF drain (RARE): a "going cold/dark" full-stop; reads like a fade — use sparingly.
- GROK i2v (default): a subject physically ACTS and a camera move stages the verb.

HARD RULES:
- Depiction: name the subject and show the narrated ACTION. Non-depictable
  characterization ("an experienced operator") informs HOW the subject moves
  (practiced, no hesitation) — it is not a literal on-screen element.
- Grok screen trap: Grok paints a cursor + garbled text on any legible CRT/UI.
  A legible on-screen glyph/error must be an FLF state-morph or a text overlay
  card, NEVER a raw Grok screen.
- Keyframe carries content: the specific stuff (the person on the table, the 3
  folders, the error code) lives in the keyframe; the generator only adds MOTION.
- Motion = the narration verb: state the subject's one concrete action + a
  motivated camera move.
- Legs <= 6s, chained (Grok extension 500s past ~6s).
- depiction_mode: "literal" by default (depict the action); "atmospheric" only
  when the beat is genuinely a mood beat.
"""

_SCHEMA_HINT = """\
Return ONLY this JSON object:
{
  "described_action": {
    "subjects": [".."], "setting": "..", "action_sequence": ["..ordered concrete beats.."],
    "props": [".."], "on_screen_text": "..(or empty)..", "manner": "..",
    "characterization": "..", "depiction_mode": "literal|atmospheric",
    "lane_plan": [{"beat": "..", "lane": "grok|flf_state_morph|flf_drain|manim"}]
  },
  "revised_prompt": "..a concrete generation prompt that DEPICTS the action..",
  "lane": "grok|flf_state_morph|flf_drain|manim|mixed",
  "rationale": "..which rules fired and what changed vs the current take..",
  "gate_precheck": {"narration_alignment": "match|partial|mismatch", "subject_named": true}
}
"""


def _gemini(prompt: str) -> str:
    """Call Gemini. Raises (caught by director_pass) so the reason is recorded."""
    from lib.env_loader import load_env
    load_env()
    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY not set")
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(
        _model_name(),
        generation_config=genai.types.GenerationConfig(
            temperature=0.4, max_output_tokens=2048, response_mime_type="application/json"
        ),
    )
    return model.generate_content(prompt).text


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    return json.loads(text)


def _context_block(scene: dict, notes: list[str], suggestions: list[dict]) -> str:
    current = ""
    if scene.get("shots"):
        current = scene["shots"][0].get("prompt", "")
    gate = scene.get("auto_gate") or {}
    sugg_lines = [f"- ({s.get('change_type', 'other')}) {s.get('text', '')}" for s in suggestions]
    return (
        f"SCENE {scene.get('number')} ({scene.get('id')}), lane={scene.get('lane')}, "
        f"narration_mode={scene.get('narration_mode')}\n"
        f"NARRATION: \"{scene.get('narration', '')}\"\n"
        f"CURRENT PROMPT: \"{current}\"\n"
        f"AUTO-GATE: verdict={gate.get('verdict')} score={gate.get('score')} "
        f"missing={gate.get('missing')}\n"
        f"HUMAN NOTES (intent to satisfy):\n" + ("\n".join(f"- {n}" for n in notes) or "- (none)") + "\n"
        f"HUMAN SUGGESTIONS:\n" + ("\n".join(sugg_lines) or "- (none)")
    )


def _fallback(scene: dict, notes: list[str], suggestions: list[dict]) -> dict:
    """Deterministic restatement when no LLM is available — keeps the flow usable."""
    current = scene["shots"][0]["prompt"] if scene.get("shots") else scene.get("narration", "")
    mode = "atmospheric" if scene.get("narration_mode") == "evocative" else "literal"
    lane_map = {"ai_video": "grok", "manim_animation": "manim"}
    lane = lane_map.get(scene.get("lane"), "grok")
    extra = "; incorporate: " + " / ".join(notes) if notes else ""
    return {
        "described_action": {
            "subjects": [], "setting": "", "action_sequence": [scene.get("narration", "")],
            "props": [], "on_screen_text": "", "manner": "", "characterization": "",
            "depiction_mode": mode,
            "lane_plan": [{"beat": scene.get("narration", "")[:80], "lane": lane}],
        },
        "revised_prompt": (current + extra).strip(),
        "lane": lane,
        "rationale": ("Offline fallback: restated the current prompt to depict the narration "
                      "and folded your notes in as constraints. A full director pass applies the "
                      "lane tree + motion rules — run the server in a shell with GOOGLE_API_KEY "
                      "and network for that."),
        "gate_precheck": {"narration_alignment": "partial", "subject_named": False},
        "_source": "fallback",
    }


def director_pass(scene: dict, notes: list[str], suggestions: list[dict]) -> dict:
    prompt = f"{RULES}\n{_SCHEMA_HINT}\n\n{_context_block(scene, notes, suggestions)}"
    err = None
    try:
        rev = _parse(_gemini(prompt))
        if isinstance(rev, dict) and "revised_prompt" in rev:
            rev["_source"] = f"gemini:{_model_name()}"
            return rev
        err = "model returned an unexpected shape"
    except Exception as e:  # network / key / quota / parse — record why we degraded
        err = f"{type(e).__name__}: {e}"
    rev = _fallback(scene, notes, suggestions)
    if err:
        rev["_error"] = err[:300]
    return rev


def director_status() -> dict:
    """Report whether the real Gemini director is available (for /api/health)."""
    from lib.env_loader import load_env
    load_env()
    return {
        "model": _model_name(),
        "google_api_key_loaded": bool(os.environ.get("GOOGLE_API_KEY")),
        "sdk_installed": importlib.util.find_spec("google.generativeai") is not None,
    }
