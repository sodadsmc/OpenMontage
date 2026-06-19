"""Approve-and-dispatch job runner — regenerate ONE scene as a new TAKE.

When a human approves a director's revised plan, this runs the revised prompt
through the real generation pipeline (lib.visual_router.generate_ai_video), conforms
the clip to the scene's slot, re-scores it with the quality gate, and stores it as a
NEW take (never clobbering the original). Cost is logged; spend is gated.

Safety (all enforced here):
- spend only via approve-and-dispatch (a human action); the director draft never spends.
- the prompt is fetched from the logged revision (server-side), never the request body.
- hard-blocks with no KIE_API_KEY / GOOGLE_API_KEY / network, or OPENMONTAGE_DISABLE_DISPATCH=1.
- one job per scene; ThreadPoolExecutor(max_workers=1) serializes all spend.
- idempotent: a second approve of the same revision returns the existing take.
- v1 dispatches the Grok i2v lane only; other lanes are left to the manual pipeline.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from web.backend import feedback as fb
from web.backend import scenes as scenes_mod
from web.backend.scenes import PROJECTS_DIR

# USD/sec by provider (from the cost map). grok = i2v; flf = Kling first-last-frame.
_RATE = {"grok-kie": 0.017, "kling-kie": 0.084}
_FLOOR_USD = 0.10
DISPATCHABLE_LANES = {"grok", "flf_state_morph", "flf_drain"}


def _lane_provider(lane: str) -> str:
    return "kling-kie" if (lane or "").startswith("flf") else "grok-kie"


def _gen_target(revision: dict) -> dict:
    """What to actually GENERATE for a revision.

    A single auto-dispatchable lane (grok / flf_*) generates the revision itself. A 'mixed' lane
    generates the director's ``primary_shot`` — the dispatchable live-action/flf sub-shot — and the
    result is a PARTIAL take (the other beats, e.g. a labeled diagram, still need a manual/manim
    pass). Returns ok=False when nothing can be auto-generated (e.g. a pure manim beat).
    """
    lane = revision.get("lane")
    if lane in DISPATCHABLE_LANES:
        return {"ok": True, "lane": lane, "prompt": revision.get("revised_prompt") or "",
                "flf": revision.get("flf"), "partial": False}
    if lane == "mixed":
        ps = revision.get("primary_shot") or {}
        if ps.get("lane") in DISPATCHABLE_LANES and (ps.get("prompt") or revision.get("revised_prompt")):
            return {"ok": True, "lane": ps["lane"],
                    "prompt": ps.get("prompt") or revision.get("revised_prompt") or "",
                    "flf": ps.get("flf"), "partial": True}
    return {"ok": False, "lane": lane}

_POOL = ThreadPoolExecutor(max_workers=1)        # serialize spend
_JOBS: dict[str, dict] = {}                       # job_id -> record (in-memory; truth is on disk)
_ACTIVE: set[tuple[str, str]] = set()             # (pid, sid) currently generating
_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_usd(slot_s: float, lane: str = "grok") -> float:
    return round(max(_FLOOR_USD, _RATE[_lane_provider(lane)] * round(slot_s or 0)), 2)


# ---- take index (ai_segments_takes.json) ---------------------------------

def _index_path(pid: str):
    return PROJECTS_DIR / pid / "artifacts" / "ai_segments_takes.json"


def read_index(pid: str) -> dict:
    p = _index_path(pid)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _append_take(pid: str, sid: str, entry: dict) -> None:
    with _INDEX_LOCK:
        idx = read_index(pid)
        idx.setdefault(sid, []).append(entry)
        p = _index_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_revision(pid: str, sid: str, rid: str) -> dict | None:
    st = fb.scene_states(pid).get(sid) or {}
    for r in st.get("revisions", []):
        if r.get("id") == rid:
            return r.get("revision")
    return None


def _existing_take(pid: str, sid: str, rid: str) -> dict | None:
    for t in read_index(pid).get(sid, []):
        if t.get("revision_id") == rid:
            return t
    return None


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def get_job(job_id: str) -> dict | None:
    j = _JOBS.get(job_id)
    return _job_view(j) if j else None


def list_jobs(pid: str) -> list[dict]:
    return [_job_view(j) for j in _JOBS.values() if j.get("project_id") == pid]


# ---- existing-clip picker (swap a scene to a better clip we already have) ----

_CLIP_DIRS = [
    "assets/ai_segments", "assets/conformed_v6", "assets/visuals_synced",
    "assets/visuals_v6", "assets/video/generated", "assets/video/clips", "renders",
]


def clip_candidates(pid: str, sid: str, limit: int = 120) -> list[dict]:
    """List existing .mp4 clips in the project a scene could be swapped to.

    Scene-matching clips are surfaced first, then everything else newest-first, so
    'the clip we made for this' is easy to find (and previewable) in the UI.
    """
    base = PROJECTS_DIR / pid
    seg_num = sid.split("_")[-1] if "_" in sid else sid
    seen: set[str] = set()
    out: list[dict] = []
    for d in _CLIP_DIRS:
        dd = base / d
        if not dd.is_dir():
            continue
        for f in sorted(dd.glob("*.mp4")):
            rel = str(f.relative_to(base)).replace("\\", "/")
            if rel in seen:
                continue
            seen.add(rel)
            try:
                st = f.stat()
            except OSError:
                continue
            name = f.name
            seg_match = (sid in name) or (f"seg_{seg_num}" in name) or (f"_{seg_num}_" in name)
            out.append({
                "name": name, "dir": d, "rel": rel,
                "path": f"projects/{pid}/{rel}",
                "url": f"/api/projects/{pid}/media/{rel}",
                "size_mb": round(st.st_size / 1_000_000, 1),
                "mtime": st.st_mtime, "seg_match": seg_match,
            })
    out.sort(key=lambda x: (not x["seg_match"], -x["mtime"]))
    return out[:limit]


def assign_clip(pid: str, sid: str, path: str, label: str = "") -> dict:
    """Set an EXISTING clip as the active take for a scene (manual override).

    Reuses the take store: the assigned clip is recorded as an accepted take, so the
    scene loader prefers it (the original baseline is never touched).
    """
    base = (PROJECTS_DIR / pid).resolve()
    s = str(path).replace("\\", "/")
    prefix = f"projects/{pid}/"
    projrel = s[len(prefix):] if s.startswith(prefix) else s
    target = (PROJECTS_DIR / pid / projrel).resolve()
    target.relative_to(base)  # raises ValueError if the path escapes the project
    if not target.is_file():
        raise FileNotFoundError(projrel)
    take_n = len(read_index(pid).get(sid, [])) + 1
    entry = {
        "take": take_n, "path": f"projects/{pid}/{projrel}",
        "verdict": "accepted", "passed": True, "score": None,
        "provider": "manual", "source": "existing-clip",
        "label": label or Path(projrel).name, "cost_usd": 0.0, "ts": _now(),
    }
    _append_take(pid, sid, entry)
    fb.append_event(pid, actor="human", type="take_generated", scene_id=sid, payload=entry)
    return entry


# ---- dispatch ------------------------------------------------------------

def dispatch_take(pid: str, sid: str, rid: str, spawned_by: str = "") -> dict:
    """Validate + enqueue a regeneration job. Returns a status record immediately."""
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:
        pass

    data = scenes_mod.load_scenes(pid)
    scene = next((s for s in data["scenes"] if s["id"] == sid), None)
    if scene is None:
        raise KeyError(sid)

    slot = scene.get("slot_s") or 0
    revision = _find_revision(pid, sid, rid)
    if revision is None:
        return {"status": "blocked", "error": "revision not found in log", "est_usd": estimate_usd(slot)}
    tgt = _gen_target(revision)
    est = estimate_usd(slot, tgt.get("lane") or "grok")

    existing = _existing_take(pid, sid, rid)
    if existing:
        return {"status": "succeeded", "idempotent": True, "take": existing, "est_usd": est}

    if not tgt["ok"]:
        return {"status": "not_yet_auto_dispatched", "lane": revision.get("lane"), "est_usd": est,
                "reason": f"'{revision.get('lane')}' isn't auto-dispatched yet — it stays on the manual pipeline"}

    if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
        return {"status": "blocked", "error": "dispatch disabled (OPENMONTAGE_DISABLE_DISPATCH=1)", "est_usd": est}
    if not os.environ.get("KIE_API_KEY"):
        return {"status": "blocked", "error": "KIE_API_KEY not set — generation disabled here", "est_usd": est}
    if not os.environ.get("GOOGLE_API_KEY"):
        return {"status": "blocked", "error": "GOOGLE_API_KEY not set — quality gate unavailable", "est_usd": est}

    with _LOCK:
        if (pid, sid) in _ACTIVE:
            busy = next((jid for jid, j in _JOBS.items()
                         if j.get("project_id") == pid and j.get("scene_id") == sid
                         and j.get("status") in ("queued", "running")), None)
            return {"status": "busy", "job_id": busy, "error": "a job is already running for this scene"}
        _ACTIVE.add((pid, sid))
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id, "project_id": pid, "scene_id": sid, "revision_id": rid,
            "status": "queued", "est_usd": est, "lane": revision.get("lane"),
            "partial": tgt["partial"],
            "created_ts": _now(), "started_ts": None, "ended_ts": None,
            "take": None, "error": None,
        }

    _POOL.submit(_run_take_job, pid, sid, rid, job_id, spawned_by, est)
    return {"job_id": job_id, "status": "queued", "est_usd": est, "lane": lane}


def _run_take_job(pid: str, sid: str, rid: str, job_id: str, spawned_by: str, est: float) -> None:
    job = _JOBS[job_id]
    try:
        try:
            from lib.env_loader import load_env
            load_env()
        except Exception:
            pass
        # Re-check the spend guards inside the worker (defense in depth).
        if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
            job.update(status="blocked", error="dispatch disabled (OPENMONTAGE_DISABLE_DISPATCH=1)", ended_ts=_now()); return
        if not os.environ.get("KIE_API_KEY"):
            job.update(status="blocked", error="KIE_API_KEY not set", ended_ts=_now()); return

        job.update(status="running", started_ts=_now())

        data = scenes_mod.load_scenes(pid)
        scene = next((s for s in data["scenes"] if s["id"] == sid), None)
        revision = _find_revision(pid, sid, rid)
        if scene is None or revision is None:
            job.update(status="failed", error="scene or revision vanished", ended_ts=_now()); return

        slot_s = float(scene.get("slot_s") or 0)
        narration = scene.get("narration", "")
        lane = revision.get("lane") or "grok"
        described = revision.get("described_action", {}) or {}
        # Resolve what to generate: the revision itself, or (for 'mixed') its primary_shot.
        tgt = _gen_target(revision)
        if not tgt["ok"]:
            job.update(status="failed", error=f"lane '{lane}' is not auto-dispatchable", ended_ts=_now()); return
        gen_lane = tgt["lane"]
        gen_flf = tgt["flf"]
        partial = tgt["partial"]
        prompt = tgt["prompt"] or revision.get("revised_prompt") or scene.get("narration", "")

        spec = SimpleNamespace(
            description=(described.get("action_sequence") or [narration])[0] or narration,
            effective_prompt=prompt, ai_prompt=prompt,
            ai_motion=(described.get("manner") or None),
            type="ai_video", ai_style=None, ai_reference_image=None, asset_ref=None,
            location_id=None, editorial_intent="", directors_move="", pacing="",
            shots=[], support_asset_refs=[], text_overlay=[],
        )

        take_n = len(read_index(pid).get(sid, [])) + 1
        seg_dir = PROJECTS_DIR / pid / "assets" / "ai_segments"
        scratch = seg_dir / "_takes_scratch" / f"{sid}__take{take_n}"
        scratch.mkdir(parents=True, exist_ok=True)
        take_target = seg_dir / f"{sid}__take{take_n}.mp4"

        from lib import visual_router as vr
        from lib.quality_gate import GenerationHardStop, QualityGate

        if gen_lane.startswith("flf"):
            # FLF lane (Kling, transition images): author start keyframe -> derive matched
            # end -> Kling interpolate -> conform to slot. Same path the fresh pipeline uses.
            from lib import flf as flf_mod
            from lib.scored_script import FLFSpec
            f = gen_flf or {}
            flf_spec = FLFSpec(
                start_prompt=f.get("start_prompt") or prompt,
                transition=f.get("transition") or prompt,
                drain=float(f.get("drain", 0.85)),
                band=tuple(f["band"]) if f.get("band") else None,
                anchor=f.get("anchor") or "fresh",
                morph=f.get("morph"),
            )
            conformed = flf_mod.flf_segment(flf_spec, slot_s, str(take_target),
                                            keyframe_dir=str(scratch), bible=None)
            if not conformed:
                job.update(status="failed", error="FLF generation failed (Nano/Kling/host)", ended_ts=_now()); return
        else:
            asset = vr.generate_ai_video(sid, spec, scratch, slot_s,
                                         bible=None, asset=None, enable_gemini=True, narration=narration)
            if asset is None or not getattr(asset, "path", None):
                job.update(status="failed", error="generator returned no clip", ended_ts=_now()); return
            conformed = vr._trim_to_duration(asset.path, str(take_target), slot_s) \
                if hasattr(vr, "_trim_to_duration") else asset.path
            if not conformed:
                job.update(status="failed", error="conform to slot failed", ended_ts=_now()); return

        score = None
        passed = False
        issues: list[str] = []
        try:
            report = QualityGate(enable_gemini=True).evaluate(
                str(take_target), spec, target_duration_s=slot_s, segment_id=sid)
            score, passed, issues = round(report.overall_score, 3), report.passed, report.issues[:4]
        except Exception as e:  # gate unavailable — keep the take for human review
            issues = [f"score unavailable: {type(e).__name__}: {e}"]

        rel = f"projects/{pid}/assets/ai_segments/{sid}__take{take_n}.mp4"
        entry = {
            "take": take_n, "path": rel, "score": score, "passed": passed,
            # a partial (mixed) take never auto-accepts — the operator eyeballs it.
            "verdict": "accepted" if (passed and not partial) else "needs_review",
            "revision_id": rid, "spawned_by": spawned_by, "cost_usd": est,
            "provider": _lane_provider(gen_lane), "lane": lane, "partial": partial,
            "note": ("live-action portion only — the diagram/other beats need a manual pass" if partial else ""),
            "issues": issues, "ts": _now(),
        }
        _append_take(pid, sid, entry)
        fb.append_event(pid, actor="system", type="take_generated", scene_id=sid, payload=entry)

        try:
            from lib import cost_ledger
            cost_ledger.log(provider=_lane_provider(gen_lane),
                            operation=("flf" if gen_lane.startswith("flf") else "image_to_video"),
                            cost_usd=est, duration_s=slot_s,
                            ledger=str(PROJECTS_DIR / pid / "artifacts" / "cost_ledger.jsonl"),
                            scene_id=sid, take=take_n, revision_id=rid, kind="take_regen")
        except Exception:
            pass

        job.update(status="succeeded", take=entry, ended_ts=_now())

    except Exception as e:
        # GenerationHardStop is non-retryable -> "blocked"; anything else -> "failed".
        name = type(e).__name__
        if name == "GenerationHardStop":
            job.update(status="blocked", error=f"hard_stop: {e}", ended_ts=_now())
            try:
                from lib import cost_ledger
                cost_ledger.log(provider="grok-kie", operation="image_to_video", cost_usd=0.0,
                                ledger=str(PROJECTS_DIR / pid / "artifacts" / "cost_ledger.jsonl"),
                                scene_id=sid, kind="hard_stop", error=str(e)[:200])
            except Exception:
                pass
        else:
            job.update(status="failed", error=f"{name}: {e}", ended_ts=_now())
    finally:
        with _LOCK:
            _ACTIVE.discard((pid, sid))
