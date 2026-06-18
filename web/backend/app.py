"""OpenMontage scene-review dashboard — FastAPI backend.

Read-only over the project artifacts (scenes + media) plus an append-only
feedback capture layer (verdict / note / suggestion / regenerate-request).

Run from the repo root:
    pip install -r web/requirements.txt
    uvicorn web.backend.app:app --reload --port 8000
    open http://localhost:8000
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env so the director pass can reach GOOGLE_API_KEY (gemini). Best-effort.
try:
    from lib.env_loader import load_env
    load_env()
except Exception:
    pass

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from web.backend import director as director_mod  # noqa: E402
from web.backend import feedback as fb  # noqa: E402
from web.backend import scenes as scenes_mod  # noqa: E402
from web.backend import takes as takes_mod  # noqa: E402

app = FastAPI(title="OpenMontage Scene Review")
API = "/api"

_VERDICTS = {"approve", "needs_work", "reject"}
_CHANGE_TYPES = {"prompt", "motion", "seed", "keyframe", "lane", "duration", "other"}


# ---- read endpoints -------------------------------------------------------

@app.get(API + "/health")
def health():
    return {
        "director": director_mod.director_status(),
        "dispatch": {
            "kie_api_key": bool(os.environ.get("KIE_API_KEY")),
            "disabled": os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1",
            "lanes": sorted(takes_mod.DISPATCHABLE_LANES),
        },
    }


@app.get(API + "/projects")
def get_projects():
    return scenes_mod.list_projects()


@app.get(API + "/projects/{pid}/scenes")
def get_scenes(pid: str):
    return scenes_mod.load_scenes(pid)


@app.get(API + "/projects/{pid}/scenes/{sid}")
def get_scene(pid: str, sid: str):
    data = scenes_mod.load_scenes(pid)
    for s in data["scenes"]:
        if s["id"] == sid:
            return s
    raise HTTPException(404, f"scene {sid} not found")


@app.get(API + "/projects/{pid}/cost")
def get_cost(pid: str):
    return scenes_mod.cost_summary(pid)


@app.get(API + "/projects/{pid}/events")
def get_events(pid: str):
    return fb.read_events(pid)


# ---- capture endpoints (append to the feedback event log) -----------------

@app.post(API + "/projects/{pid}/scenes/{sid}/verdict")
def post_verdict(pid: str, sid: str, body: dict = Body(...)):
    v = body.get("verdict")
    if v not in _VERDICTS:
        raise HTTPException(400, f"verdict must be one of {sorted(_VERDICTS)}")
    return fb.append_event(pid, actor="human", type="human_verdict",
                           scene_id=sid, payload={"verdict": v})


@app.post(API + "/projects/{pid}/scenes/{sid}/note")
def post_note(pid: str, sid: str, body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "note text required")
    return fb.append_event(pid, actor="human", type="human_note",
                           scene_id=sid, payload={"text": text, "at_s": body.get("at_s")})


@app.post(API + "/projects/{pid}/scenes/{sid}/suggestion")
def post_suggestion(pid: str, sid: str, body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "suggestion text required")
    ct = body.get("change_type") or "other"
    if ct not in _CHANGE_TYPES:
        ct = "other"
    return fb.append_event(pid, actor="human", type="human_suggestion",
                           scene_id=sid, payload={"text": text, "change_type": ct})


@app.post(API + "/projects/{pid}/scenes/{sid}/regenerate")
def post_regenerate(pid: str, sid: str, body: dict = Body(...)):
    # Capture the human's regenerate INTENT (logged for the record).
    notes = body.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    return fb.append_event(pid, actor="human", type="regenerate_requested",
                           scene_id=sid,
                           payload={"notes": notes, "target": body.get("target", "scene")})


@app.post(API + "/projects/{pid}/scenes/{sid}/director-pass")
def post_director_pass(pid: str, sid: str, body: dict = Body(default={})):
    """Run the director pass: human notes (intent) -> rule-compliant revised plan.

    Notes are NOT injected raw — they go through the director (rules applied).
    Returns the drafted revision for a pre-spend approval card; also logs it.
    """
    data = scenes_mod.load_scenes(pid)
    scene = next((s for s in data["scenes"] if s["id"] == sid), None)
    if scene is None:
        raise HTTPException(404, f"scene {sid} not found")
    extra = body.get("notes") or []
    if isinstance(extra, str):
        extra = [extra]
    fb_notes = [n["text"] for n in scene["feedback"].get("notes", [])]
    notes = fb_notes + [n for n in extra if n]
    suggestions = scene["feedback"].get("suggestions", [])

    revision = director_mod.director_pass(scene, notes, suggestions)
    rid = uuid.uuid4().hex[:12]
    fb.append_event(pid, actor="director", type="revision_drafted", scene_id=sid,
                    payload={"revision_id": rid, "revision": revision, "notes": notes})
    return {"revision_id": rid, "revision": revision}


@app.post(API + "/projects/{pid}/scenes/{sid}/revision/{rid}/approve")
def post_revision_approve(pid: str, sid: str, rid: str):
    # Pre-spend approval. In a full control-plane this would dispatch generation;
    # here it records the approval so the agent/script can act on it.
    return fb.append_event(pid, actor="human", type="revision_approved",
                           scene_id=sid, payload={"revision_id": rid})


@app.post(API + "/projects/{pid}/scenes/{sid}/revision/{rid}/reject")
def post_revision_reject(pid: str, sid: str, rid: str):
    return fb.append_event(pid, actor="human", type="revision_rejected",
                           scene_id=sid, payload={"revision_id": rid})


@app.post(API + "/projects/{pid}/scenes/{sid}/revision/{rid}/approve-and-dispatch")
def post_approve_and_dispatch(pid: str, sid: str, rid: str):
    """Approve a revision AND dispatch a regeneration job for it (the spend gate).

    Logs the approval (audit), then enqueues a take job. The job fetches the prompt
    from the logged revision server-side and hard-blocks if generation is unavailable.
    """
    ev = fb.append_event(pid, actor="human", type="revision_approved",
                         scene_id=sid, payload={"revision_id": rid})
    try:
        return takes_mod.dispatch_take(pid, sid, rid, spawned_by=ev["event_id"])
    except KeyError:
        raise HTTPException(404, f"scene {sid} not found")


@app.get(API + "/projects/{pid}/jobs/{job_id}")
def get_job(pid: str, job_id: str):
    j = takes_mod.get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get(API + "/projects/{pid}/jobs")
def get_jobs(pid: str):
    return takes_mod.list_jobs(pid)


@app.get(API + "/projects/{pid}/scenes/{sid}/clip-candidates")
def get_clip_candidates(pid: str, sid: str):
    return takes_mod.clip_candidates(pid, sid)


@app.post(API + "/projects/{pid}/scenes/{sid}/use-clip")
def post_use_clip(pid: str, sid: str, body: dict = Body(...)):
    """Swap a scene to an existing clip we already have (manual take override)."""
    path = body.get("path")
    if not path:
        raise HTTPException(400, "path required")
    try:
        return takes_mod.assign_clip(pid, sid, path, body.get("label", ""))
    except FileNotFoundError:
        raise HTTPException(404, "clip not found")
    except ValueError:
        raise HTTPException(403, "path outside project")


# ---- media (range-served so video seeks) ----------------------------------

@app.get(API + "/projects/{pid}/media/{path:path}")
def media(pid: str, path: str):
    base = (scenes_mod.PROJECTS_DIR / pid).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)  # guard against path traversal
    except ValueError:
        raise HTTPException(403, "forbidden")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)  # Starlette FileResponse honors Range requests


# ---- static frontend (mounted last so /api/* wins) ------------------------
# Prefer the built React/Vite app (web/ui/dist); fall back to the vanilla app
# (web/frontend) when the UI hasn't been built yet.
_UI_DIST = REPO_ROOT / "web" / "ui" / "dist"
_FRONTEND = _UI_DIST if (_UI_DIST / "index.html").is_file() else (REPO_ROOT / "web" / "frontend")
app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
