"""Per-project WORLD CONTRACT — the physical/continuity invariants of the story
world, declared once and injected into EVERY generation prompt automatically.

Why this exists (taum-sauk-2005 post-mortem, 2026-07): of ~50 operator rejections
across five acts, roughly 14 were the same handful of world facts being violated
over and over, each time re-explained by hand in one beat's prompt while the next
batch forgot it again:

  * the reservoir must sit FLUSH with the top of the parapet wall  (8 rejections —
    the operator eventually drew a red line on a screenshot)
  * water/debris must move DOWNHILL, never uphill or back into the lake  (4)
  * the reservoir is KIDNEY-shaped, never circular  (1)
  * the dam is INTACT before the breach moment  (1 — a pre-breach beat showed the
    dam already broken)

Prompt text alone was never the problem; the problem was that the knowledge lived
in a conversation instead of in the pipeline. A contract is:

  1. authored ONCE per project (or derived from the research brief),
  2. matched to beats by TAGS auto-derived from the beat's own prompt text (so it
     works on plans that predate the contract — no re-authoring),
  3. gated by story PHASE (pre-breach / breach / aftermath / rebuilt), so
     "the wall is intact" stops applying after the wall fails,
  4. appended to keyframe AND motion prompts by the executor, every time.

Machine-checkable invariants can additionally declare a `check`, run by
`lib.beat_lint`; the rest are prevention-by-construction.

CLI:
  python -m lib.world_contract <project_id> [--beat seg_010_b3]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Tag vocabulary -> the words that imply it. Derived from the taum plans, but the
# vocabulary is generic documentary-scene language, not project-specific.
_TAG_WORDS: dict[str, tuple[str, ...]] = {
    "reservoir": ("reservoir", "lake", "basin", "impoundment", "pool"),
    "wall": ("parapet", "wall", "crest", "coping", "rim", "dike", "dam"),
    "water": ("water", "flood", "torrent", "wave", "spill", "cascade", "surge",
              "current", "waterline", "overtop"),
    "flow": ("pour", "pouring", "flows", "flowing", "rushing", "cascad", "spill",
             "drain", "sluice", "races", "racing", "tumbl"),
    "aerial": ("aerial", "from above", "overhead", "drone", "bird's", "birds-eye"),
    "night": ("night", "dark", "midnight", "moonlit", "starlight"),
    "interior": ("control room", "console", "office", "bedroom", "indoors", "desk"),
    "person": ("operator", "worker", "workers", "rescuer", "rescuers", "man",
               "woman", "child", "children", "baby", "crew", "figure", "figures",
               "hand", "hands", "people", "family", "survivor", "survivors"),
    "structure": ("house", "home", "cabin", "foundation", "building", "plant"),
    "instrument": ("gauge", "probe", "sensor", "indicator", "mast", "needle",
                   "dial", "readout", "screen", "crt"),
    "document": ("email", "e-mail", "list", "report", "paperwork", "clipboard",
                 "memo", "printout", "page"),
}


def derive_tags(*texts: str) -> set[str]:
    """Tags implied by a beat's own prompt text (case-insensitive substring)."""
    blob = " ".join(t or "" for t in texts).lower()
    tags = set()
    for tag, words in _TAG_WORDS.items():
        if any(w in blob for w in words):
            tags.add(tag)
    return tags


def contract_path(project_id: str) -> Path:
    return ROOT / "projects" / project_id / "artifacts" / "world_contract.json"


def load(project_id: str) -> dict[str, Any]:
    """Load a project's contract, or an empty contract if it has none."""
    p = contract_path(project_id)
    if not p.is_file():
        _log.info("world_contract: none for %s (%s)", project_id, p)
        return {"project": project_id, "phases": {}, "invariants": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _scene_num(scene_id: str) -> int:
    m = re.search(r"(\d+)", scene_id or "")
    return int(m.group(1)) if m else -1


def phase_of(contract: dict, scene_id: str) -> str | None:
    """Which story phase a scene sits in.

    `phases` maps a phase name to one inclusive scene-number range or a LIST of
    them — non-contiguous is the normal case, because documentaries open on the
    disaster and then jump back in time:
        {"breach": [[1, 4], [18, 20]], "pre-breach": [[5, 17]], ...}
    """
    n = _scene_num(scene_id)
    for name, rng in (contract.get("phases") or {}).items():
        spans = rng if rng and isinstance(rng[0], (list, tuple)) else [rng]
        for span in spans:
            try:
                lo, hi = int(span[0]), int(span[1])
            except Exception:  # noqa: BLE001
                continue
            if lo <= n <= hi:
                return name
    return None


def _matches(inv: dict, tags: set[str], phase: str | None) -> bool:
    when = inv.get("applies_when") or {}
    any_ = set(when.get("tags_any") or [])
    all_ = set(when.get("tags_all") or [])
    none_ = set(when.get("tags_none") or [])
    if any_ and not (any_ & tags):
        return False
    if all_ and not all_.issubset(tags):
        return False
    if none_ & tags:
        return False
    phases = inv.get("phases")
    if phases and phase is not None and phase not in phases:
        return False
    return True


def clauses_for(contract: dict, scene_id: str, *texts: str,
                extra_tags: set[str] | None = None) -> list[dict]:
    """The invariants that apply to this beat, most important first."""
    tags = derive_tags(*texts) | (extra_tags or set())
    phase = phase_of(contract, scene_id)
    hits = [inv for inv in (contract.get("invariants") or [])
            if _matches(inv, tags, phase)]
    hits.sort(key=lambda i: -int(i.get("priority", 0)))
    return hits


def apply(prompt: str, contract: dict, scene_id: str, *texts: str,
          extra_tags: set[str] | None = None, limit: int = 4) -> str:
    """Append the applicable MANDATORY clauses to a generation prompt.

    `limit` caps how many clauses ride along — prompts have finite attention, and
    the clauses are priority-sorted so the load-bearing ones survive.
    """
    hits = clauses_for(contract, scene_id, *(texts or (prompt,)),
                       extra_tags=extra_tags)[:limit]
    if not hits:
        return prompt
    clauses = "; ".join(h["prompt_clause"].strip().rstrip(".") for h in hits
                        if h.get("prompt_clause"))
    if not clauses:
        return prompt
    return f"{(prompt or '').rstrip('. ')}. MUST HOLD: {clauses}."


def _main() -> None:
    ap = argparse.ArgumentParser(description="Inspect a project's world contract")
    ap.add_argument("project_id")
    ap.add_argument("--scene", default=None, help="scene id, e.g. seg_010")
    ap.add_argument("--text", default=None, help="beat prompt text to match against")
    a = ap.parse_args()
    c = load(a.project_id)
    print(f"project: {c.get('project')}  invariants: {len(c.get('invariants') or [])}")
    for name, rng in (c.get("phases") or {}).items():
        spans = rng if rng and isinstance(rng[0], (list, tuple)) else [rng]
        pretty = ", ".join(f"{s[0]}-{s[1]}" for s in spans)
        print(f"  phase {name}: scenes {pretty}")
    if a.scene:
        print(f"\nphase of {a.scene}: {phase_of(c, a.scene)}")
        if a.text:
            print(f"tags: {sorted(derive_tags(a.text))}")
            for h in clauses_for(c, a.scene, a.text):
                print(f"  [{h.get('id')}] {h.get('prompt_clause')}")
            print("\nprompt with contract:\n " + apply(a.text, c, a.scene))


if __name__ == "__main__":
    _main()
