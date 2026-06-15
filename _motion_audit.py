"""Second-opinion audit: run the Motion Director as a fresh independent pass over
every AUTHORED ai_video beat and compare its camera read to the human's.

No blank beats exist in the script, so instead of filling gaps this surfaces
DIVERGENCES — beats where the AI director would move the camera differently than
the hand-authored motion. Agreement = validation; divergence = a beat worth a look.

Run:  python _motion_audit.py [script.yaml]
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor

import tools.base_tool  # noqa: F401  (loads .env)
from lib.scored_script import load_scored_script
from lib.motion_director import author_for_segment

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else "projects/therac-25-test/script_v5/scored_script.yaml"

# Coarse camera-direction classifier — enough to tell push vs pull vs static vs
# orbit/track, which is where a meaningful divergence shows up.
_CLASSES = [
    ("orbit",  r"orbit|arc(?:s|ing)? around|circl"),
    ("track",  r"track|follow|walking pace|alongside"),
    ("pull",   r"pull[- ]?back|pulls? back|dolly out|zoom out|widen|draws? back"),
    ("push",   r"push[- ]?in|push(?:es|ing)? in|dolly in|zoom in|macro push|moves? in|closer|tighten"),
    ("tilt",   r"tilt"),
    ("crane",  r"crane"),
    ("rack",   r"rack focus"),
    ("static", r"static|locked[- ]?off|holds? (?:absolutely |dead )?still|completely still|utterly inert|no (?:zoom|camera) move"),
]


def classify(motion: str) -> str:
    t = (motion or "").lower()
    for name, pat in _CLASSES:
        if re.search(pat, t):
            return name
    return "other"


def audit_one(seg):
    gold = seg.visual.ai_motion
    seg.visual.ai_motion = None  # blank it → fresh independent authoring
    ai = author_for_segment(seg)
    return {
        "id": seg.id,
        "intent": seg.visual.editorial_intent or seg.editorial_intent or "-",
        "gold": gold, "ai": ai,
        "gold_dir": classify(gold), "ai_dir": classify(ai),
    }


def main() -> int:
    s = load_scored_script(SCRIPT)
    targets = [seg for seg in s.segments if seg.visual.type == "ai_video"
               and (seg.visual.ai_motion or "").strip()]
    print(f"Auditing {len(targets)} authored ai_video beats in {SCRIPT}\n")

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(audit_one, targets))
    rows.sort(key=lambda r: r["id"])

    agree = [r for r in rows if r["gold_dir"] == r["ai_dir"] and r["gold_dir"] != "other"]
    diverge = [r for r in rows if r["gold_dir"] != r["ai_dir"]]

    print(f"CAMERA-READ AGREEMENT: {len(agree)}/{len(rows)} beats — the AI director "
          f"chose the same camera direction as you (validation).")
    print(f"DIVERGENCES: {len(diverge)} beats where the reads differ:\n")
    for r in diverge:
        print(f"  {r['id']}  intent={r['intent']}   you={r['gold_dir']}  AI={r['ai_dir']}")
        print(f"      YOU: {(r['gold'] or '')[:150]}")
        print(f"      AI : {r['ai'][:150]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
