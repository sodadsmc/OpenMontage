"""PRE-FLIGHT LINT for a beat plan — every check here is $0 and runs BEFORE any
paid generation.

Rationale (taum-sauk-2005 post-mortem): almost every operator rejection was
visible in the PLAN before a cent was spent. The plan said "the wall crest sinks"
with no sink verb; it said "workers at the rim" with no character sheet; four
consecutive beats in one scene described the same wide shot; a pre-breach beat
described the level dropping while the project's world contract says the
reservoir is always brim-full. None of that needs a model to detect — it needs
someone to read the plan against the rules. That is this module.

Checks
  role            every beat declares a shot role (drives the motion gate + lane)
  contract        world-contract invariants that apply, and any beat text that
                  CONTRADICTS one (declared via `conflict_words`)
  duplicate       near-identical keyframe prompts inside a scene (or adjacent
                  scenes) — the "why is it the same picture four times" note
  entity          a beat with a human subject but no character sheet to ground it
  motion          a footage-role beat whose motion prompt has no motion verb
  screen          legible-screen/document content routed to open i2v (grok trap)

Exit code is non-zero if any ERROR-severity finding exists, so it can gate a
batch in CI or a shell chain.

CLI:
  python -m lib.beat_lint <project_id> [--plans PATH] [--json OUT] [--strict]
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from lib import world_contract as wc

ROOT = Path(__file__).resolve().parent.parent

# Shot roles. The motion floor a beat must clear depends on its role — an
# establishing/hero shot reading static is a rejection; a card or a 2s insert
# reading quiet is correct.
ROLES = {
    "establishing": "wide scene-setter; needs real camera or environment motion",
    "hero": "the beat the scene is built around; needs unmistakable motion",
    "action": "a subject physically doing the narration verb",
    "insert": "close detail (gauge, hand, screen); quiet motion is fine",
    "card": "authored diagram / document card; boil + reveal, not footage",
    "connective": "short bridge under ~2.5s; low motion acceptable",
}
FOOTAGE_ROLES = {"establishing", "hero", "action"}

_MOTION_VERBS = (
    "walk", "run", "step", "climb", "lift", "carry", "reach", "turn", "pour",
    "spill", "flow", "rush", "race", "surge", "rise", "drop", "fall", "sink",
    "collapse", "crack", "tear", "burst", "slam", "swing", "drift", "pan",
    "push", "pull", "tilt", "sway", "ripple", "slosh", "churn", "spray",
    "flicker", "blink", "pulse", "scroll", "type", "dial", "wade", "splash",
    "settle", "creep", "crumble", "tumble", "roar", "hurry", "lower", "glide",
    "advance", "shrink", "thin", "widen", "fill", "empty", "boil",
    # added after the first lint pass flagged real motion as missing — keep this
    # list generous: a false "no motion verb" error trains people to ignore the linter
    "breath", "shift", "track", "lash", "whip", "wrap", "rub", "stir", "hold",
    "press", "cut", "saw", "sweep", "crash", "shake", "tremble", "quiver",
    "drip", "trickle", "bob", "float", "swirl", "lean", "kneel", "stand",
    "gesture", "point", "wave", "nod", "blow", "flutter", "crawl", "slide",
)
_STATIC_WORDS = ("static", "still image", "no motion", "motionless", "frozen")
# a conflict word preceded by one of these is a CONSTRAINT, not a violation
# ("never back into the lake" is the rule being stated, not broken)
_NEGATORS = ("never", "not ", "no ", "without", "n't", "avoid", "rather than",
             "instead of", "unbroken", "un")


def _conflict_hit(blob: str, word: str) -> bool:
    """True if `word` appears as a real claim: whole-word, not negated, and not
    swallowed by a larger word ('unbroken wall' must not match 'broken wall')."""
    for m in re.finditer(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])",
                         blob.lower()):
        pre = blob.lower()[max(0, m.start() - 28):m.start()]
        if any(n in pre for n in _NEGATORS):
            continue
        return True
    return False


def suggest_role(beat: dict) -> str:
    """Infer a shot role from the beat's own text, so plans authored before roles
    existed can be backfilled ($0) instead of hand-tagged."""
    kf = (beat.get("keyframe_prompt") or "").lower()
    mot = (beat.get("motion_prompt") or "").lower()
    lane = (beat.get("lane") or "").lower()
    blob = f"{kf} {mot}"
    tags = wc.derive_tags(blob)
    if any(k in lane for k in ("card", "doc", "sketch", "diagram")):
        return "card"
    if "document" in tags and not (tags & {"water", "person"}):
        return "card"
    if any(k in kf for k in ("close on", "close-up", "closeup", "tight on", "detail")):
        return "insert"
    if "instrument" in tags and "aerial" not in tags:
        return "insert"
    if "aerial" in tags or any(k in kf for k in ("wide", "establishing", "panorama",
                                                "vast", "from above")):
        return "establishing"
    if "person" in tags:
        return "action"
    if tags & {"water", "flow"}:
        return "hero"
    return "connective"


def _load_plans(project_id: str, plans: str | None) -> list[dict]:
    p = Path(plans) if plans else (
        ROOT / "projects" / project_id / "artifacts" / "beat_plans.json")
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower())


def lint_plan(project_id: str, plans: str | None = None) -> list[dict]:
    """Return findings: {severity, check, beat, detail}."""
    data = _load_plans(project_id, plans)
    contract = wc.load(project_id)
    out: list[dict] = []

    def add(sev, check, beat, detail):
        out.append({"severity": sev, "check": check, "beat": beat, "detail": detail})

    for scene in data:
        sid = scene.get("scene_id", "?")
        beats = scene.get("beats") or []
        phase = wc.phase_of(contract, sid)
        texts = []
        for i, b in enumerate(beats, 1):
            name = f"{sid}_b{i}"
            kf = b.get("keyframe_prompt", "") or ""
            mot = b.get("motion_prompt", "") or ""
            blob = f"{kf} {mot}"
            texts.append((name, _norm(kf)))
            tags = wc.derive_tags(blob)

            # --- role -------------------------------------------------------
            role = b.get("role")
            if not role:
                add("error", "role", name,
                    "no shot role declared; add one of " + ", ".join(sorted(ROLES)))
            elif role not in ROLES:
                add("error", "role", name, f"unknown role {role!r}")

            # --- world contract --------------------------------------------
            # a beat may deliberately break an invariant (the approved seg_006
            # hero shot starts the level LOW and raises it); declare it, with a
            # reason, via `contract_exempt` and the clause is dropped for that beat
            exempt = set(b.get("contract_exempt") or [])
            hits = [h for h in wc.clauses_for(contract, sid, blob)
                    if h.get("id") not in exempt]
            if exempt and not b.get("exempt_reason"):
                add("warn", "contract", name,
                    f"contract_exempt={sorted(exempt)} with no `exempt_reason` — "
                    "an undocumented exemption is how invariants quietly rot")
            for inv in hits:
                for w in (inv.get("conflict_words") or []):
                    if _conflict_hit(blob, w):
                        add("error", "contract", name,
                            f"beat text says {w!r} which CONTRADICTS invariant "
                            f"[{inv.get('id')}] ({inv.get('prompt_clause','')[:70]}...)")
            if tags & {"reservoir", "wall"} and phase is None:
                add("warn", "contract", name,
                    f"scene {sid} is in no world-contract phase, so phase-gated "
                    "invariants (water level, intact wall) will NOT be applied")

            # --- entity grounding for humans -------------------------------
            sheets = b.get("sheets") or []
            if "person" in tags and not any("subj" in str(s) for s in sheets):
                add("warn", "entity", name,
                    "human subject in the prompt but no subj_* character sheet in "
                    "`sheets` — ungrounded people drift (this is how the "
                    "'disembodied hands' shots happened)")

            # --- motion ----------------------------------------------------
            if role in FOOTAGE_ROLES:
                if not any(v in mot.lower() for v in _MOTION_VERBS):
                    add("error", "motion", name,
                        f"role={role} but the motion prompt names no motion verb: "
                        f"{mot[:60]!r}")
                for sw in _STATIC_WORDS:
                    if sw in mot.lower():
                        add("warn", "motion", name,
                            f"role={role} but motion prompt contains {sw!r}")

            # --- legible-screen trap ---------------------------------------
            lane = (b.get("lane") or "").lower()
            if tags & {"document"} and ("grok" in lane or not lane):
                add("warn", "screen", name,
                    "document/legible-text content on an open-i2v lane — grok "
                    "garbles text; route to an authored card (sketch lane)")

        # --- near-duplicate keyframes within the scene ---------------------
        # (env hygiene is checked once, after the scene loop)
        for a in range(len(texts)):
            for b2 in range(a + 1, len(texts)):
                r = difflib.SequenceMatcher(None, texts[a][1], texts[b2][1]).ratio()
                if r >= 0.82:
                    add("warn", "duplicate", texts[b2][0],
                        f"keyframe prompt is {r:.0%} identical to {texts[a][0]} — "
                        "the scene will read as one held picture; vary the framing "
                        "(wide -> subject -> detail)")

    # --- environment hygiene (once) -----------------------------------------
    # A debugging override left set across sessions cost six days: IMAGE_HOST was
    # pinned to tmpfiles, KIE then could not FETCH the keyframes, and ~40 grok
    # failures were misread as a provider outage (they were `failMsg: "Upload
    # failed"`). Any generation-affecting override is a pre-flight warning.
    import os as _os
    for var, why in (
        ("IMAGE_HOST", "pins the keyframe host; a stale value made KIE unable to "
                       "fetch refs and looked exactly like a provider outage"),
        ("GEMINI_IMAGE_MODEL", "overrides the stills model"),
        ("CHANNEL_STYLE", "overrides the house style"),
        ("AI_GROK_LEG_CAP", "overrides the clip-length cap"),
    ):
        if _os.environ.get(var):
            add("warn", "env", "-",
                f"{var}={_os.environ[var]!r} is set — {why}. Unset it unless this "
                "batch genuinely needs the override.")
    return out


def _main() -> int:
    ap = argparse.ArgumentParser(description="Pre-flight lint a beat plan ($0)")
    ap.add_argument("project_id")
    ap.add_argument("--plans", default=None)
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on warnings too")
    ap.add_argument("--fix-roles", action="store_true",
                    help="backfill inferred shot roles into the plan file")
    a = ap.parse_args()
    if a.fix_roles:
        p = Path(a.plans) if a.plans else (
            ROOT / "projects" / a.project_id / "artifacts" / "beat_plans.json")
        data = json.loads(p.read_text(encoding="utf-8"))
        n = 0
        for scene in data:
            for b in scene.get("beats") or []:
                if not b.get("role"):
                    b["role"] = suggest_role(b); n += 1
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"backfilled {n} shot role(s) into {p.name}\n")
    f = lint_plan(a.project_id, a.plans)
    errs = [x for x in f if x["severity"] == "error"]
    warns = [x for x in f if x["severity"] == "warn"]
    for x in errs + warns:
        print(f"[{x['severity'].upper():5}] {x['check']:9} {x['beat']:14} {x['detail']}")
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s)")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(f, indent=1), encoding="utf-8")
    return 1 if errs or (a.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(_main())
