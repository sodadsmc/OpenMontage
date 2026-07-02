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


def _channel_look() -> str:
    """The channel MEDIUM string (duotone / ink / halftone / grain). Shown to the
    director so it composes a prompt COMPATIBLE with the fixed house look — the medium
    is appended downstream regardless, but a vivid content prompt can overpower it."""
    try:
        from lib.channel_style import prompt_suffix
        return prompt_suffix()
    except Exception:
        return ""


def _style_block(scene: dict) -> str:
    """The fixed channel LOOK + this segment's MOOD, with hard rules so the re-plan
    stays on-style. Without this the director writes a pure-content prompt (e.g. a
    brightly-lit lab with a clean 'hero' in a white coat) that renders full-colour and
    breaks the duotone channel — the exact failure this block prevents."""
    look = _channel_look() or ("graphic-novel duotone: deep navy + warm amber, bold ink, "
                               "cross-hatch and halftone, heavy grain, illustrated not photoreal")
    mood = scene.get("ai_style") or "(inherit the channel default mood)"
    return (
        "CHANNEL LOOK — every shot is rendered in this FIXED medium; your prompt MUST be "
        "compatible with it and must never fight it:\n"
        f"  {look}\n"
        f"SEGMENT MOOD to preserve (reuse unless the re-plan truly changes the beat):\n"
        f"  {mood}\n"
        "STYLE HARD RULES:\n"
        "- Render in the warm AMBER-on-navy 'old book page' duotone: bold ink, heavy halftone-dot "
        "and film-grain print texture, keyed off ONE warm amber light source. Match the TONE of "
        "the scene's canonical reference image (some beats are bright amber paper, others sink "
        "toward navy shadow) — do NOT force pure black, and never render full-colour or photoreal.\n"
        "- Keep it object/atmosphere-forward (period-1985 machinery, screens, hardware, paper) "
        "rather than a brightly-lit human. No clean 'hero' character, no white or vividly-coloured "
        "clothing as a focal mass; any people are illustrated and secondary, never a large detailed "
        "face dominating the frame.\n"
        "- Echo the palette and texture in words (amber-on-navy, ink, cross-hatch, halftone, grain) "
        "inside revised_prompt so the look holds against the content."
    )


RULES = """\
You are the DIRECTOR for an AI-generated, depiction-first narrated documentary
(hand-inked graphic-novel style). A scene is GENERATED to DEPICT what the
narration literally describes — not atmospheric b-roll.

The human reviewer's notes are INTENT. Do NOT paste them into the prompt. Re-derive
a rule-compliant plan that satisfies the intent. Apply these rules:

LANE DECISION TREE (pick exactly one primary lane per scene; beats inside a scene
may split — that is a "mixed" scene):
- MANIM/sketch: a mechanism, a labeled diagram, or a precise taught count.
- FLF state-morph: ONE LEGIBLE element changes to a specific new state the viewer must
  watch (a glyph X->E, an error code appearing, a needle to a reading, 6->3). NOT for a
  beam firing, a light flaring, or a machine activating — that is gross MOTION, so use GROK.
- FLF drain (RARE): a "going cold/dark" full-stop; reads like a fade — use sparingly.
- GROK i2v (default): a subject physically ACTS and a camera move stages the verb.

HARD RULES:
- Depiction: name the subject and show the narrated ACTION. Non-depictable
  characterization ("an experienced operator") informs HOW the subject moves
  (practiced, no hesitation) — it is not a literal on-screen element.
- Grok screen trap: Grok paints a cursor + garbled text on any legible CRT/UI.
  A legible on-screen glyph/error must be an FLF state-morph or a text overlay
  card, NEVER a raw Grok screen.
- Grok needs a CONCRETE physical scene (a named subject doing a visible action in a place).
  Grok CANNOT render an abstract idea — a "matching pattern", a comparison, "X resembles Y",
  a labeled chart, data, or anything that only reads as a diagram. Route those to MANIM (a
  diagram beat) or rewrite the grok beat as a concrete shot (NOT "the burns match the collimator
  pattern" -> instead "extreme close-up of the rectangular collimator aperture, hard-edged shadow").
- Keyframe carries content: the specific stuff (the person on the table, the 3
  folders, the error code) lives in the keyframe; the generator only adds MOTION.
- Motion = the narration verb: state the subject's one concrete action + a
  motivated camera move.
- SHOT LIST: FIRST decompose the narration into action_sequence = the distinct
  physical BEATS, in order. Split genuinely SEPARATE actions, but KEEP ONE
  CONTINUOUS MOTION AS A SINGLE BEAT — a rise interrupted by a strike is ONE beat,
  not two (splitting it makes the figure read as "already standing" in one shot and
  "struck out of nowhere" in the next). e.g. "Cox is getting off the table when it
  hits him, and he pounds on the door" is TWO beats: ["struck mid-rise as he tries
  to get off the table", "staggers to the sealed door and pounds on it"]. Do NOT
  compress unrelated movements into one vague summary ("receives radiation"), and do
  NOT split one continuous movement into micro-beats.
  THEN, when action_sequence has MULTIPLE actions, emit ONE beat per action in
  narration order (a shot list) and seed "beats" one-to-one from action_sequence —
  never merge sequential actions into a single beat. The "prefer a single lane"
  preference below applies ONLY to a SINGLE-action beat; a multi-action sequence
  MUST be a shot list ("mixed").
- Legs <= 6s, chained (Grok extension 500s past ~6s).
- depiction_mode: "literal" by default (depict the action); "atmospheric" only
  when the beat is genuinely a mood beat.

DISPATCH PREFERENCE (so the fix can actually generate a take) — applies to a
SINGLE-action beat only (a multi-action sequence MUST be a shot list per SHOT LIST above):
- Auto-generation supports the GROK and FLF lanes only. For a SINGLE-action beat, PREFER a
  single grok or flf lane that depicts the beat. A live-action depiction of the moment is
  usually better than a diagram. Choose "mixed" or "manim" ONLY when a labeled diagram or an
  exact taught count is essential and cannot be carried by one live-action or FLF shot — or
  when the narration is a multi-action sequence that requires a shot list.
- When you DO choose "mixed", fill "beats" (see schema) with EVERY beat in order — all the
  dispatchable beats (grok/flf) are generated and concatenated into one COMPLETE take; only a
  "manim" beat becomes a labeled placeholder. (Also set "primary_shot" for back-compat.)
"""

_SCHEMA_HINT = """\
Return ONLY this JSON object:
{
  "described_action": {
    "subjects": [".."], "setting": "..", "action_sequence": ["..ONE concrete physical action per entry; enumerate EVERY movement verb separately, in order; do NOT compress compound movements into a summary.."],
    "props": [".."], "on_screen_text": "..(or empty)..", "manner": "..",
    "characterization": "..", "depiction_mode": "literal|atmospheric",
    "lane_plan": [{"beat": "..", "lane": "grok|flf_state_morph|flf_drain|manim"}]
  },
  "revised_prompt": "..a concrete generation prompt that DEPICTS the action..",
  "lane": "grok|flf_state_morph|flf_drain|manim|mixed",
  "rationale": "..which rules fired and what changed vs the current take..",
  "ai_style": "..the MOOD for this beat — reuse the SEGMENT MOOD unless the re-plan truly changes it; phrase it to reinforce the duotone amber-on-navy channel look (e.g. 'cold clinical dread, oppressive dark, a single amber glow against deep navy')..",
  "gate_precheck": {"narration_alignment": "match|partial|mismatch", "subject_named": true},
  "flf": null,
  "primary_shot": null,
  "beats": null,
  "chained": false
}

When (and only when) lane is "flf_state_morph" or "flf_drain", set "flf" to:
  {"start_prompt": "channel-style keyframe the shot STARTS on — subject present/lit, one contained composition, locked camera",
   "transition": "the start->end change in <=500 chars (one change; camera locked)",
   "drain": 0.85, "band": null, "anchor": "fresh", "morph": null}

Pick ONE of two end-frame techniques:
1. DRAIN (default; for going cold / dims / goes dark / 'N of M go dark'):
   - drain 0.8-0.92 = strong (subject/region goes dead); lower = partial dimming.
   - band [lo,hi] (fractions of frame height) dims only a vertical region — e.g.
     [0.50,0.62] dims the front/bottom group for a count reveal; null = whole frame.
   - leave "morph" null.
2. CONTENT MORPH (for a LEGIBLE on-screen glyph/text change — X->E, an error code
   APPEARING, a number changing): the legible text is composited deterministically
   (Grok/Nano can't render clean screen text). Set "morph" and author start_prompt
   as a clean glowing screen with NO text:
     "morph": {"box": [x,y,w,h] as fractions where the glyph sits (e.g. a centered
                console readout [0.40,0.42,0.20,0.16]),
               "start_text": "X" (or "" if nothing is shown before),
               "end_text": "E" (or the code that appears, e.g. "MALFUNCTION 54"),
               "color": "amber"}
   When you set "morph", the drain/band fields are ignored.
- anchor "fresh", or a bible asset_id to ground the start keyframe on a canonical.
For any non-FLF lane, "flf" must be null.

When lane is "mixed", set "beats" to the ORDERED list of beats — EVERY beat is generated and
concatenated into ONE complete take (grok/flf beats are generated; a "manim" beat becomes a
labeled placeholder for a manual pass). Order the beats to track the narration, and SEED
"beats" ONE-TO-ONE from described_action.action_sequence: emit exactly one beat per action in
that sequence, in order (do not merge two actions into one beat, do not drop an action). Each beat:
  {"lane": "grok" | "flf_state_morph" | "flf_drain" | "manim",
   "desc": "3-6 word operator label (e.g. 'dose comparison diagram')",
   "prompt": "a standalone, concrete channel-style generation prompt for JUST this beat",
   "motion": "REQUIRED — the ONE concrete subject action for THIS beat + a motivated camera "
             "move that stages it (e.g. 'Cox shoves up off the table and swings his legs down; "
             "handheld camera rises with him'). NEVER a generic zoom/push.",
   "weight": 0.5,   # this beat's share of the scene duration; the weights sum to ~1.0
   "flf": null}     # the flf object (start_prompt/transition/...) when lane is flf, else null
Also set "primary_shot" to the single most important auto-dispatchable beat (back-compat).
Set "chained": true when the beats are ONE subject acting continuously in ONE setting (a physical
action sequence — e.g. a person rises, is struck, crosses to a door, and pounds on it): dispatch then
grounds each beat's keyframe on the PREVIOUS beat's so the figure and room stay consistent across the
cuts. Set "chained": false for a montage of different subjects or places.
For any non-"mixed" lane, "beats" and "primary_shot" must be null.
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
            # 2.5-flash is a THINKING model — thinking tokens draw down this same budget, so it
            # must be generous or the JSON truncates mid-string (matches lib/narration_gate's 16384).
            temperature=0.4, max_output_tokens=8192, response_mime_type="application/json"
        ),
    )
    return model.generate_content(prompt).text


def _parse(text: str) -> dict:
    """Lenient JSON parse: strip code fences, isolate the outermost object, and repair the
    common LLM defect (a trailing comma before } or ]) that yields 'Expecting property name'."""
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))  # drop trailing commas


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
        "ai_style": scene.get("ai_style"),
        "flf": None,
        "primary_shot": None,
        "_source": "fallback",
    }


_STRICT = ("\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY one strict JSON "
           "object — double-quoted keys and strings, NO trailing commas, NO comments, NO prose.")

_SHOTLIST_REASK = ("\n\nIMPORTANT: your described_action.action_sequence lists MULTIPLE sequential "
                   "actions, but you merged them into fewer beats. Set lane=\"mixed\" and emit "
                   "\"beats\" ONE-TO-ONE with action_sequence — exactly one beat per action, in "
                   "order, each with its own \"motion\". Do not merge or drop any action.")

_DECOMPOSE = """You are a documentary shot-list assistant. Read the narration and list the distinct \
on-screen PHYSICAL BEATS, in order — one entry per beat a camera can SHOW a subject DOING. Split \
genuinely SEPARATE actions, but KEEP ONE CONTINUOUS MOTION AS A SINGLE BEAT: a rise interrupted by a \
strike is ONE beat (not "starts to rise" + "is struck"); a stagger that ends in a pound is ONE beat. \
OMIT narration-only facts (dates, statistics, dose numbers, names with no action). Return ONLY JSON: \
{"actions": ["..", ".."]}.

EXAMPLE
Narration: "The operator presses P. The machine fires a second time. Cox is getting off the table \
when it hits him. He pounds on the treatment room door. Simulations put the dose at 25,000 rads."
{"actions": ["the operator presses the P key", "the machine beam fires", "Cox is struck mid-rise as \
he tries to get off the table", "Cox staggers to the sealed door and pounds on it"]}
(Rise+strike are ONE continuous motion -> one beat. The 25,000-rads simulation is narration-only -> omitted.)

NARRATION: "__NARR__"
"""


def _decompose_actions(narration: str) -> list[str]:
    """Focused pre-pass: extract the granular on-screen action list from the narration.

    A single-purpose call is far more reliable than asking the director to decompose AND plan
    in one shot (gemini-flash compresses to a 2-action summary in the combined call). Best-effort:
    returns [] on any failure so the director still runs.
    """
    narration = (narration or "").strip()
    if not narration:
        return []
    try:
        out = _parse(_gemini(_DECOMPOSE.replace("__NARR__", narration.replace('"', "'"))))
        acts = out.get("actions") if isinstance(out, dict) else None
        return [a.strip() for a in (acts or []) if isinstance(a, str) and a.strip()]
    except Exception:
        return []


_CHAINED_CLASSIFY = """You classify shot continuity for a documentary's visuals. Read the narration \
and decide whether the beats form ONE CONTINUOUS CONNECTED SCENE in a single place/time (each shot \
flows from the previous one, so grounding each shot on the last keeps the figure and room consistent) \
— versus a MONTAGE that jumps between different places, times, or unrelated people.

Return ONLY JSON: {"chained": true|false}.
- true  = one continuous event in one location. A figure and/or setting carries across the beats EVEN \
IF several things act (an operator, a machine, and a patient can all be part of ONE room's event). \
Narrated facts or statistics layered over that continuous action do NOT make it a montage.
- false = the beats jump between different LOCATIONS, DATES, or unrelated PEOPLE (a montage / timeline), \
or there is no connected physical scene at all.

EXAMPLE (true)
Narration: "The operator presses P. The machine fires a second time. Cox is getting off the table when \
it hits him. He pounds on the treatment room door. Simulations later put the doses at 16 to 25 thousand rads."
{"chained": true}
EXAMPLE (false)
Narration: "June 1985. Katie Yarbrough, Marietta, Georgia. July 1985. Frances Hill, Hamilton, Ontario. \
December 1985. Yakima, Washington."
{"chained": false}

NARRATION: "__NARR__"
"""


def _classify_chained(narration: str):
    """Focused pre-pass: is the on-screen action ONE continuous single-subject sequence (chainable),
    or a montage / statistical beat? A single-purpose call is far more reliable than folding this
    into the director's combined plan (the combined flag is ~50/50 on real action sequences).
    Returns True/False, or None on any failure so the caller can fall back."""
    narration = (narration or "").strip()
    if not narration:
        return None
    try:
        out = _parse(_gemini(_CHAINED_CLASSIFY.replace("__NARR__", narration.replace('"', "'"))))
        v = out.get("chained") if isinstance(out, dict) else None
        return bool(v) if isinstance(v, bool) else None
    except Exception:
        return None


def director_pass(scene: dict, notes: list[str], suggestions: list[dict], *, attempts: int = 3) -> dict:
    """Re-plan a scene's visual from the narration + the human's notes (as instructions).

    Retries the Gemini call on a parse/shape failure (re-asking for strict JSON) before falling
    back — a malformed-JSON reply must NOT silently degrade to 'append the notes to the prompt'.
    """
    # Dedicated decomposition pre-pass: a focused "list every physical action" call, far more
    # reliable than asking the director to decompose AND plan at once. Its list is authoritative.
    actions = _decompose_actions(scene.get("narration", ""))
    action_directive = ""
    if len(actions) > 1:
        action_directive = (
            "\n\nACTION SHOT-LIST (authoritative, derived from the narration): "
            + json.dumps(actions) +
            "\nSet lane=\"mixed\", set described_action.action_sequence to EXACTLY this list, and "
            "emit \"beats\" ONE-TO-ONE with it — one beat per action, in order, do NOT merge or "
            "drop any action. Each beat gets its own concrete prompt and its own \"motion\" verb.")
    base = (f"{RULES}\n{_style_block(scene)}\n{_SCHEMA_HINT}\n\n"
            f"{_context_block(scene, notes, suggestions)}{action_directive}")
    err = None
    asked_shotlist = False
    for attempt in range(max(1, attempts)):
        try:
            rev = _parse(_gemini(base if attempt == 0 else base + _STRICT))
            if isinstance(rev, dict) and rev.get("revised_prompt"):
                # Shot-list enforcement: for a multi-action segment, the model must emit one
                # beat per action. If it collapsed the sequence into fewer beats, re-ask ONCE
                # (via the existing retry path) before accepting — merged actions are the
                # "action shots come out wrong" failure.
                seq = ((rev.get("described_action") or {}).get("action_sequence") or [])
                beats = rev.get("beats") or []
                target = max(len(seq), len(actions))  # decomposed action list is authoritative
                if not asked_shotlist and target > 1 and len(beats) < target:
                    asked_shotlist = True
                    err = (f"shot-list: {target} sequential actions but emitted "
                           f"{len(beats)} beat(s); must emit one beat per action")
                    base += _SHOTLIST_REASK
                    continue
                rev["_source"] = f"gemini:{_model_name()}"
                # Always carry a MOOD so dispatch can re-apply the house art-direction;
                # default to the segment's canonical mood if the model omitted it.
                if not rev.get("ai_style"):
                    rev["ai_style"] = scene.get("ai_style")
                # Chained-keyframe cohesion. The director's own flag is flaky on action sequences
                # (it correctly rejects montages but is ~50/50 on real sequences), so decide with a
                # FOCUSED classifier prepass — reliable the same way _decompose_actions is — and fall
                # back to the director's flag / a structural default only if it is unavailable.
                if rev.get("lane") == "mixed" and len(rev.get("beats") or []) > 1:
                    cc = _classify_chained(scene.get("narration", ""))
                    if cc is not None:
                        rev["chained"] = cc
                    elif rev.get("chained") is None:
                        rev["chained"] = bool((rev.get("described_action") or {}).get("setting"))
                else:
                    rev["chained"] = False
                if attempt:
                    rev["_attempts"] = attempt + 1
                return rev
            err = "model returned an unexpected shape"
        except Exception as e:  # network / key / quota / parse — try again, then degrade
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
