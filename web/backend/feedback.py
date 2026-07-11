"""Human feedback as an append-only event log — the training record.

Every review action is one immutable line in projects/<id>/feedback/events.jsonl.
Nothing is ever mutated; the per-scene UI state is DERIVED by folding the log.
This is deliberately the rich-log-now / decide-the-training-use-later design:
the chain regenerate_requested -> (director revision) -> take -> verdict is what
later trains the gate and the director, so we keep the whole stream.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from web.backend.scenes import PROJECTS_DIR

_EVENT_TYPES = {
    "human_verdict",
    "human_note",
    "human_suggestion",
    "regenerate_requested",
    "revision_drafted",     # actor=director: the rule-applied plan for a regenerate
    "revision_approved",    # actor=human: pre-spend approval of a drafted revision
    "revision_rejected",    # actor=human
    "take_generated",       # actor=system: a regenerated take landed (links revision->take->score)
    "take_deleted",         # actor=human: a take dropped from the review list (file kept on disk)
    "scene_reset",          # actor=human: start the scene fresh — clears the folded UI state below
    "keyframes_authored",   # actor=system: the chained keyframe SET for a revision was previewed
    # --- stills-first workflow (dashboard v2, 2026-07-10) ---
    "sheet_approved",       # actor=human: an entity's reference sheet vetted vs real photos
    "sheet_rejected",       # actor=human: sheet sent back for a re-roll (payload.hint)
    "still_note",           # actor=human: a note on ONE authored still (payload.idx, .note)
    "stills_approved",      # actor=human: the scene's still set + animatic signed off (GATE)
    "stills_unapproved",    # actor=human: gate reopened (stills changed after approval)
    "animatic_built",       # actor=system: scene or episode storyboard shot rendered
    "project_imported",     # actor=human: scored_script registered + validated for a new video
    "take_promoted",        # actor=human: THIS take copied to the canonical the build reads
}


def _fb_dir(project_id: str):
    d = PROJECTS_DIR / project_id / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _events_path(project_id: str):
    return _fb_dir(project_id) / "events.jsonl"


def append_event(project_id: str, *, actor: str, type: str,
                 scene_id: str | None = None, shot_id: str | None = None,
                 payload: dict | None = None) -> dict:
    if type not in _EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")
    ev = {
        "event_id": uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "type": type,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "payload": payload or {},
    }
    with _events_path(project_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def read_events(project_id: str) -> list[dict]:
    p = _events_path(project_id)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def empty_state() -> dict:
    return {
        "verdict": None,
        "notes": [],
        "suggestions": [],
        "regenerate_requests": [],
        "revisions": [],
        "takes": [],
        "event_count": 0,
    }


def scene_states(project_id: str) -> dict[str, dict]:
    """Fold the event log into per-scene UI state (latest verdict wins)."""
    states: dict[str, dict] = {}
    for ev in read_events(project_id):
        sid = ev.get("scene_id")
        if not sid:
            continue
        st = states.setdefault(sid, empty_state())
        st["event_count"] += 1
        t = ev.get("type")
        p = ev.get("payload", {})
        if t == "human_verdict":
            st["verdict"] = p.get("verdict")
        elif t == "human_note":
            st["notes"].append({"text": p.get("text", ""), "ts": ev["ts"]})
        elif t == "human_suggestion":
            st["suggestions"].append({
                "text": p.get("text", ""),
                "change_type": p.get("change_type"),
                "ts": ev["ts"],
            })
        elif t == "regenerate_requested":
            st["regenerate_requests"].append({
                "notes": p.get("notes", []),
                "target": p.get("target", "scene"),
                "status": "queued",
                "ts": ev["ts"],
            })
        elif t == "revision_drafted":
            st["revisions"].append({
                "id": p.get("revision_id"),
                "revision": p.get("revision"),
                "notes": p.get("notes", []),
                "status": "drafted",
                "ts": ev["ts"],
            })
        elif t in ("revision_approved", "revision_rejected"):
            rid = p.get("revision_id")
            new_status = "approved" if t.endswith("approved") else "rejected"
            for r in st["revisions"]:
                if r["id"] == rid:
                    r["status"] = new_status
        elif t == "keyframes_authored":
            # Attach the previewed stills to their revision so the UI can SHOW an already-paid
            # keyframe set (before this, the grid lived only in the authoring browser session
            # and the operator couldn't find the stills again without paying to re-author).
            rid = p.get("revision_id")
            for r in st["revisions"]:
                if r["id"] == rid:
                    r["keyframes"] = p.get("keyframes") or []
        elif t == "take_generated":
            st["takes"].append(p)
        elif t == "take_deleted":
            tk = p.get("take")
            st["takes"] = [x for x in st["takes"] if x.get("take") != tk]
        elif t == "scene_reset":
            # Start fresh: clear the folded state (notes/verdict/revisions/takes) so the next
            # director pass isn't polluted by stale notes. The raw event log is preserved.
            st.update(verdict=None, notes=[], suggestions=[], regenerate_requests=[],
                      revisions=[], takes=[])
    return states
