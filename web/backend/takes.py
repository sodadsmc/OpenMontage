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
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from web.backend import feedback as fb
from web.backend import scenes as scenes_mod
from web.backend.scenes import PROJECTS_DIR

_log = logging.getLogger(__name__)

# USD/sec by provider (from the cost map). grok = i2v; flf = Kling first-last-frame.
_RATE = {"grok-kie": 0.017, "kling-kie": 0.084}
_FLOOR_USD = 0.10
DISPATCHABLE_LANES = {"grok", "flf_state_morph", "flf_drain"}

# Spend safety for INTERACTIVE regens: ONE gate re-roll per shot (not the bulk default of 3,
# which fanned out to a 13-call / $1.33 runaway). attempts=1 was tried but is a FALSE economy —
# a single gate-failing leg then fails the WHOLE take after paying for the good legs (seg_014:
# $0.51 spent, no clip). One re-roll lets a multi-leg take actually complete; typical cost is
# still ~legs x $0.10 (the re-roll only fires on gate-failing legs). Bulk runs leave it unset (3).
os.environ.setdefault("AI_SHOT_ATTEMPTS", "2")


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
        # New: a director that emits a per-beat `beats` array generates EVERY dispatchable beat
        # and concats them (see _beat_plan / _store_mixed_take) — a complete take, not a partial.
        if any((b.get("lane") in DISPATCHABLE_LANES) for b in (revision.get("beats") or [])):
            return {"ok": True, "lane": "mixed", "prompt": "", "flf": None, "partial": True}
        ps = revision.get("primary_shot") or {}
        if ps.get("lane") in DISPATCHABLE_LANES and (ps.get("prompt") or revision.get("revised_prompt")):
            return {"ok": True, "lane": ps["lane"],
                    "prompt": ps.get("prompt") or revision.get("revised_prompt") or "",
                    "flf": ps.get("flf"), "partial": True}
    return {"ok": False, "lane": lane}


def _load_bible_asset(pid: str, sid: str):
    """Resolve (AssetBible, AssetEntry) for a segment so a regenerated take ANCHORS on the
    project's amber-graded canonical reference image — the same grounding the bulk pipeline
    uses. The old regen path passed bible=None/asset=None, so takes had no styled anchor and
    the generator freelanced the look (the off-style drift the operator flagged). Best-effort:
    returns (None, None) on any miss so generation still proceeds (just unanchored)."""
    try:
        from lib.asset_bible import AssetBible
        from lib.scored_script import load_scored_script
        bpath = PROJECTS_DIR / pid / "artifacts" / "asset_bible_v6.json"
        if not bpath.exists():
            return None, None
        bible = AssetBible.load(bpath)
        seg = None
        scripts = sorted((PROJECTS_DIR / pid).glob("**/scored_script.yaml"))
        if scripts:
            seg = next((s for s in load_scored_script(scripts[0]).segments if s.id == sid), None)
        asset = bible.asset_for_segment(seg) if seg is not None else None
        _refresh_canonical_url(asset)
        return bible, asset
    except Exception:
        return None, None


def _refresh_canonical_url(asset) -> None:
    """Re-host the asset's LOCAL canonical image to a fresh public URL.

    The bible stores a tmpfiles ``canonical_image_url`` that expires (~1h retention), so a regen
    hours/days later hits a dead link (HTTP 404) and the keyframe edit fails ('generator returned
    no clip'). Re-upload the local file (image_host prefers DURABLE premiumize when keyed) so the
    i2v/edit provider can fetch the amber canonical anchor. Best-effort: leave the stored URL
    untouched on any failure."""
    if asset is None:
        return
    try:
        local = getattr(asset, "canonical_reference_image", "") or ""
        if not (local and Path(local).exists()):
            return
        from lib.image_host import upload_image
        fresh = upload_image(local)
        if fresh:
            asset.canonical_image_url = fresh
    except Exception:
        pass


def _scene_gold_ref(pid: str, sid: str, idx: int | None = None, beat_text: str = "",
                    n_beats: int | None = None) -> str | None:
    """A LOCKED gold reference frame — an approved still that a chained keyframe / per-beat re-roll
    grounds on, so identity/design stay pinned to the approved standard (printing press, not slot
    machine). Per-BEAT plates are keyed by CONTENT via a sidecar ``_gold_refs/{sid}.beats.json``
    ([{"file": "...", "match": "<regex over the beat text>"}, ...] — first match wins), because a
    director re-plan renumbers beats: ordinal ``{sid}_b{idx}.png`` plates are honored only when the
    plate count equals the plan's chainable-beat count (a 4-plate set under a 5-beat plan would put
    the door plate under the strike beat). Falls back to the scene plate ``{sid}.png``. Returns a
    hosted URL, or None if unset."""
    root = PROJECTS_DIR / pid / "assets" / "ai_segments" / "_gold_refs"
    p = None
    side = root / f"{sid}.beats.json"
    if idx is not None and side.exists():
        try:
            for ent in json.loads(side.read_text(encoding="utf-8")):
                if ent.get("file") and ent.get("match") and re.search(ent["match"], beat_text or "", re.I):
                    c = root / ent["file"]
                    if c.exists():
                        p = c
                    break
        except Exception:
            p = None
    elif idx is not None:
        c = root / f"{sid}_b{idx}.png"
        n_plates = len(list(root.glob(f"{sid}_b*.png")))
        if c.exists() and (n_beats is None or n_plates == n_beats):
            p = c
        elif c.exists():
            _log.warning("gold plates for %s are ordinal (%d plates) but the plan has %s chainable "
                         "beats — using the scene plate; add %s.beats.json to key plates by content",
                         sid, n_plates, n_beats, sid)
    if p is None:
        c = root / f"{sid}.png"
        p = c if c.exists() else None
    if p is None:
        return None
    try:
        from lib.image_host import upload_image
        return upload_image(str(p)) or None
    except Exception:
        return None


# Per-beat LOCATION grounding for chained scenes. A scene's beats can span rooms (seg_019: the
# operator presses P at the CONSOLE in the adjacent control room; everything else happens in the
# treatment room). Grounding every beat on one segment-level asset put the terminal room's canonical
# under treatment-room beats (and vice versa), and let the machine design drift per beat. The beat's
# OWN text picks its locale; each locale grounds on its own bible asset, and the keyframe chain
# RESETS at a locale boundary so the console frame never seeds the treatment room.
_TERMINAL_RX = re.compile(
    r"\b(press(es|ing)?|key(board)?s?|terminal|console|types?|typing|screen|cursor|prompt|"
    r"vt-?100|monitor|operator station)\b", re.I)


def _machine_in_beat(beat: dict) -> bool:
    """True when the MACHINE is visibly on screen in this beat regardless of locale — a console
    beat that shows the treatment room 'through the observation window' still needs the machine
    sheet/tokens riding (a window-framed Therac drifted to a CT donut when they didn't)."""
    text = f"{beat.get('prompt') or ''} {beat.get('label') or ''} {beat.get('motion') or ''}"
    return bool(re.search(r"(therac|machine|beam head|treatment head|gantry|linear accelerator|"
                          r"through the (observation )?window)", text, re.I))


def _beat_locale(beat: dict) -> str:
    text = f"{beat.get('prompt') or ''} {beat.get('label') or ''}"
    return "terminal" if _TERMINAL_RX.search(text) else "room"


def _locale_asset(bible, scene_asset, locale: str):
    """The bible asset that carries a locale's identity. 'terminal' -> the terminal/console
    location asset; 'room' -> the SCENE's own mapped asset when it can ground (it has tokens or a
    model sheet — the scene's room is the right room), else the machine asset (the location with
    identity_tokens, whose tokens/sheet pin the Therac-25's design). Falls back to scene_asset."""
    assets = getattr(bible, "assets", None) or []
    if locale == "terminal":
        hit = next((a for a in assets
                    if "terminal" in (getattr(a, "asset_id", "") or "").lower()
                    or "console" in (getattr(a, "asset_id", "") or "").lower()), None)
    elif scene_asset is not None and ((getattr(scene_asset, "identity_tokens", None) or [])
                                      or (getattr(scene_asset, "reference_sheet", "") or "")):
        hit = scene_asset
    else:
        hit = next((a for a in assets
                    if getattr(a, "type", "") == "location"
                    and (getattr(a, "identity_tokens", None) or [])), None)
    if hit is not None:
        _refresh_canonical_url(hit)
    return hit or scene_asset


def _real_photo_local(pid: str, asset) -> Path | None:
    """The LOCAL file behind the bible's stored reference_images (those URLs live on expiring
    CDNs and are already dead) — resolved by basename under the project's assets tree."""
    for url in (getattr(asset, "reference_images", None) or []):
        name = (url or "").rstrip("/").rsplit("/", 1)[-1]
        if not name:
            continue
        local = next(iter((PROJECTS_DIR / pid).glob(f"assets/**/{name}")), None)
        if local is not None and local.is_file():
            return local
    return None


def _real_photo_ref(pid: str, asset) -> str | None:
    """A freshly-hosted REAL photograph of the machine (from the local file). The photo rides
    as an extra ref on machine-visible beats so the design is copied from reality."""
    try:
        local = _real_photo_local(pid, asset)
        if local is not None:
            from lib.image_host import upload_image
            return upload_image(str(local))
    except Exception:
        pass
    return None


def _vet_keyframe(pid: str, asset, frame: Path) -> dict | None:
    """ADVISORY reference-judge verdict for one authored still ({ok, mismatches} or None).
    Runs gemini-2.5-pro against the asset's LOCAL sheet + real photo — empirically the only
    tier that separates the labeled good/bad seg_019 frames (`python -m lib.reference_judge
    selftest`). Never raises; a None is 'no opinion', and nothing here ever blocks a job —
    it exists to point the operator's eyeball at the mismatch, not to replace it."""
    try:
        refs = []
        sheet = getattr(asset, "reference_sheet", "") or ""
        if sheet and Path(sheet).exists():
            refs.append(Path(sheet))
        photo = _real_photo_local(pid, asset)
        if photo is not None:
            refs.append(photo)
        if not refs:
            return None
        from lib.reference_judge import judge_frame
        return judge_frame(frame, refs,
                           getattr(asset, "subject", "") or "the machine",
                           list(getattr(asset, "identity_tokens", None) or []))
    except Exception:
        return None


_POOL = ThreadPoolExecutor(max_workers=1)        # serialize spend
_JOBS: dict[str, dict] = {}                       # job_id -> record (in-memory; truth is on disk)
_ACTIVE: set[tuple[str, str]] = set()             # (pid, sid) currently generating
_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_usd(slot_s: float, lane: str = "grok") -> float:
    return round(max(_FLOOR_USD, _RATE[_lane_provider(lane)] * round(slot_s or 0)), 2)


def _worst_case_usd(slot_s: float, lane: str = "grok") -> tuple[float, int, int]:
    """Worst-case regen spend = legs (<=6s, chained) x per-shot gate re-rolls (AI_SHOT_ATTEMPTS).
    The $1.33 runaway was 3 legs x 3 attempts; dispatch_take refuses jobs whose worst case
    exceeds OPENMONTAGE_REGEN_MAX_USD."""
    n_legs = max(1, math.ceil((slot_s or 0) / 6.0))
    attempts = max(1, int(os.environ.get("AI_SHOT_ATTEMPTS", "2")))
    per_leg = _RATE[_lane_provider(lane)] * 6.0  # one ~6s leg
    return round(n_legs * attempts * per_leg, 2), n_legs, attempts


def _beat_cost(lane: str, dur: float) -> float:
    """USD for ONE beat (one pass): grok = chained <=6s legs; flf = a SINGLE Kling interpolation
    (<=15s, not legs); manim/other = a free placeholder. Mirror of api.ts beatCostUsd."""
    lane = lane or "grok"
    dur = float(dur or 0)
    if lane.startswith("flf"):
        return round(min(dur, 15.0) * _RATE["kling-kie"], 2)
    if lane == "grok":
        return round(math.ceil(dur / 6.0) * _RATE["grok-kie"] * 6.0, 2)
    return 0.0


def edit_revision(pid: str, sid: str, rid: str, beats: list) -> dict:
    """Clone a drafted revision with operator-edited beats and log it as a NEW revision (the
    append-only event log is never rewritten). The operator can rewrite a beat's prompt or change
    its lane/service (e.g. a pricey FLF text beat -> a cheap grok shot). Returns
    {revision_id, revision} so the UI can approve-and-dispatch the edited plan."""
    rev = _find_revision(pid, sid, rid)
    if rev is None:
        raise KeyError(rid)
    new_rev = dict(rev)
    new_rev["beats"] = beats
    new_rev["lane"] = "mixed"
    new_rev["_source"] = (rev.get("_source") or "director") + " +operator-edit"
    new_rid = uuid.uuid4().hex[:12]
    fb.append_event(pid, actor="human", type="revision_drafted", scene_id=sid,
                    payload={"revision_id": new_rid, "revision": new_rev, "notes": ["operator beat edit"]})
    return {"revision_id": new_rid, "revision": new_rev}


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


def _graded_preview(pid: str, sid: str, take_n: int, abs_src: str) -> str | None:
    """Render the duotone+grain GRADED sibling of a take so the dashboard reviews the
    SHIPPABLE look — the channel finishing pass (lib.finishing.apply_finish) that final
    assembly applies once at render. The RAW take is left untouched (assembly grades it, not
    this preview, so it's never double-graded). Returns the project-relative graded path,
    or None (then the dashboard serves the raw clip). Best-effort."""
    try:
        from lib.finishing import apply_finish
        graded = PROJECTS_DIR / pid / "assets" / "ai_segments" / f"{sid}__take{take_n}.graded.mp4"
        graded.parent.mkdir(parents=True, exist_ok=True)
        out = apply_finish(abs_src, str(graded))
        # apply_finish returns the OUTPUT on success, or the INPUT path if finishing is disabled
        # (passthrough) — only treat a real graded file as a preview.
        if out and graded.is_file() and Path(out).resolve() == graded.resolve():
            return f"projects/{pid}/assets/ai_segments/{sid}__take{take_n}.graded.mp4"
    except Exception:
        pass
    return None


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
        "preview": _graded_preview(pid, sid, take_n, str(PROJECTS_DIR / pid / projrel)),
        "verdict": "accepted", "passed": True, "score": None,
        "provider": "manual", "source": "existing-clip",
        "label": label or Path(projrel).name, "cost_usd": 0.0, "ts": _now(),
    }
    _append_take(pid, sid, entry)
    fb.append_event(pid, actor="human", type="take_generated", scene_id=sid, payload=entry)
    return entry


def delete_take(pid: str, sid: str, take_n: int) -> dict:
    """Remove a take from the scene's take index (declutter the review list).

    The .mp4 stays on disk (re-addable via the swap picker); only the index entry is
    dropped and a ``take_deleted`` event is logged (the append-only record is never
    rewritten). If the deleted take was active, the scene loader falls back to the next
    accepted take — or the baseline — on its own."""
    with _INDEX_LOCK:
        idx = read_index(pid)
        lst = idx.get(sid, [])
        removed = next((t for t in lst if t.get("take") == take_n), None)
        if removed is None:
            raise KeyError(f"take {take_n} not found for {sid}")
        idx[sid] = [t for t in lst if t.get("take") != take_n]
        _index_path(pid).write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    fb.append_event(pid, actor="human", type="take_deleted", scene_id=sid,
                    payload={"take": take_n, "path": removed.get("path"),
                             "revision_id": removed.get("revision_id")})
    return {"deleted": take_n, "remaining": len(idx.get(sid, []))}


def reset_scene(pid: str, sid: str) -> dict:
    """Start a scene FRESH: drop all its takes from the index and append a scene_reset event so the
    folded feedback (notes/verdict/revisions) is cleared and the next director pass isn't polluted
    by stale notes. The .mp4 files and the append-only event log are preserved (recoverable)."""
    with _INDEX_LOCK:
        idx = read_index(pid)
        cleared = len(idx.get(sid, []))
        idx.pop(sid, None)
        _index_path(pid).write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    fb.append_event(pid, actor="human", type="scene_reset", scene_id=sid, payload={"cleared_takes": cleared})
    return {"reset": sid, "cleared_takes": cleared}


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

    # Spend ceiling: block on the EXPECTED cost (legs x one pass). For a 'mixed' scene that's the
    # sum of its DISPATCHABLE beats' legs (placeholders are free). The gate re-roll only spends
    # extra on gate-failing legs (bounded 2x), so block on expected, not worst — blocking on worst
    # wrongly refused ordinary ~30s scenes. Operator-tunable via OPENMONTAGE_REGEN_MAX_USD.
    plan = _beat_plan(revision, slot, scene.get("narration", ""))
    attempts = max(1, int(os.environ.get("AI_SHOT_ATTEMPTS", "2")))
    if plan:
        disp = [b for b in plan if b["dispatchable"]]
        n_legs = sum(math.ceil(b["dur"] / 6.0) for b in disp)
        expected = round(sum(_beat_cost(b["lane"], b["dur"]) for b in disp), 2)
    else:
        worst0, n_legs, _a = _worst_case_usd(slot, tgt.get("lane") or "grok")
        expected = round(worst0 / max(1, _a), 2)
    worst = round(expected * attempts, 2)
    ceiling = float(os.environ.get("OPENMONTAGE_REGEN_MAX_USD", "1.00"))
    if expected > ceiling:
        return {"status": "blocked", "est_usd": expected, "worst_usd": worst,
                "error": (f"~${expected:.2f} for {n_legs} legs (~{slot:.0f}s, up to ${worst:.2f} if legs "
                          f"retry) exceeds the ${ceiling:.2f} regen ceiling — raise OPENMONTAGE_REGEN_MAX_USD")}
    est = expected  # record the realistic (beat-aware) cost on the job/take

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
            "partial": (any(not b["dispatchable"] for b in plan) if plan else tgt["partial"]),
            "created_ts": _now(), "started_ts": None, "ended_ts": None,
            "take": None, "error": None,
        }

    _POOL.submit(_run_take_job, pid, sid, rid, job_id, spawned_by, est)
    return {"job_id": job_id, "status": "queued", "est_usd": est, "worst_usd": worst,
            "lane": revision.get("lane")}


def _vet_take(pid: str, sid: str, take_target, narration: str) -> dict | None:
    """Gemini WATCHES the finished take against the narration and reports specific sync mismatches
    + polish/motion fixes (lib.animation_vet) — the per-moment feedback the coarse pass/fail score
    gate misses, and the 'eyes' the (text-only) director lacks. Best-effort: None on any failure.
    One cheap Gemini call."""
    try:
        from lib.animation_vet import vet_animation
        v = vet_animation(str(take_target), narration or "")
        if not isinstance(v, dict) or v.get("skipped") or v.get("error"):
            return None
        return {
            "sync_score": v.get("sync_score"), "polish_score": v.get("polish_score"),
            "overall": v.get("overall"),
            "mismatches": (v.get("mismatches") or [])[:5],
            "suggestions": (v.get("suggestions") or [])[:4],
        }
    except Exception:
        return None


# ---- mixed multi-beat generation (generate every beat + concat into one complete take) ----

def _beat_plan(revision: dict, slot_s: float, narration: str = "") -> list[dict]:
    """Director-authored beats for a 'mixed' scene -> an ordered render plan. Each beat gets its
    share of the slot (by ``weight``); grok/flf beats are generated, a manim/other beat becomes a
    labeled placeholder. Returns [] for a non-mixed revision or a mixed one with no ``beats``."""
    if revision.get("lane") != "mixed":
        return []
    beats = revision.get("beats") or []
    if not beats:
        return []
    ws = [max(0.05, float(b.get("weight") or 0) or 1.0) for b in beats]
    tot = sum(ws) or 1.0
    plan = []
    for i, (b, w) in enumerate(zip(beats, ws), start=1):
        bl = (b.get("lane") or "grok").strip()
        plan.append({
            "idx": i, "lane": bl,
            "prompt": (b.get("prompt") or revision.get("revised_prompt") or narration or "").strip(),
            "motion": b.get("motion"),
            "flf": b.get("flf"),
            "dur": round(max(2.0, slot_s * w / tot), 2),
            "label": (b.get("desc") or b.get("beat") or b.get("prompt") or bl)[:60],
            "dispatchable": bl in DISPATCHABLE_LANES,
            # Route THIS beat to the Veo reference lane (~2x Grok's rate): ONE generation
            # renders a complex multi-phase arc without leg-chaining artifacts (repeated
            # beam fires, seam hitches). Set by the operator/director on the beat.
            "hard_shot": bool(b.get("hard_shot")),
        })
    return plan


def _gen_beat(beat_id: str, beat: dict, out_path: str, scratch: Path, narration: str,
              bible, anchor_asset, mood) -> str | None:
    """Generate ONE dispatchable beat (grok or flf) at ``beat['dur']`` -> out_path. Anchors on the
    asset bible (house look). Returns the conformed clip path or None on failure."""
    from lib import visual_router as vr
    try:
        lane = beat["lane"]; prompt = beat["prompt"] or narration; dur = float(beat["dur"])
        if lane.startswith("flf"):
            from lib import flf as flf_mod
            from lib.scored_script import FLFSpec
            f = beat.get("flf") or {}
            spec = FLFSpec(start_prompt=f.get("start_prompt") or prompt,
                           transition=f.get("transition") or prompt,
                           drain=float(f.get("drain") if f.get("drain") is not None else 0.85),
                           band=tuple(f["band"]) if f.get("band") else None,
                           anchor=f.get("anchor") or "fresh", morph=f.get("morph"))
            return flf_mod.flf_segment(spec, dur, str(out_path), keyframe_dir=str(scratch), bible=bible) or None
        spec = SimpleNamespace(
            description=prompt, effective_prompt=prompt, ai_prompt=prompt,
            ai_motion=beat.get("motion") or None, hard_shot=bool(beat.get("hard_shot")),
            type="ai_video", ai_style=mood, ai_reference_image=None, asset_ref=None, location_id=None,
            editorial_intent="", directors_move="", pacing="", shots=[], support_asset_refs=[], text_overlay=[])
        asset = vr.generate_ai_video(beat_id, spec, scratch, dur, bible=bible, asset=anchor_asset,
                                     enable_gemini=True, narration=narration)
        if asset is None or not getattr(asset, "path", None):
            return None
        return (vr._trim_to_duration(asset.path, str(out_path), dur)
                if hasattr(vr, "_trim_to_duration") else asset.path) or None
    except Exception:
        # One beat's error (bad spec, provider hiccup) must NOT crash the whole take — the caller
        # turns a None into a labeled placeholder so the other beats still render.
        return None


def _gen_chained_beat(beat_id: str, beat: dict, out_path: str, scratch: Path, narration: str,
                      bible, anchor_asset, mood, prev_keyframe, gold_ref=None, keyframe=None, video=True,
                      real_photo=None, fig_from_narration=True, machine_grounding=True,
                      enable_gemini=False, console_clause=False):
    """Chained-keyframe Grok beat (the cohesion path for a multi-beat ACTION sequence).

    ``keyframe`` reuses an operator-approved pre-authored still (skips authoring). ``video=False``
    authors the keyframe and returns it WITHOUT the paid video — the keyframe-preview step.

    Authors THIS beat's keyframe as a Nano edit grounded on the PRIOR beat's keyframe (+ the segment
    canonical + the machine reference sheet) so the figure + room identity carry across the whole
    sequence — instead of each beat re-imagining the figure from the empty canonical. Then animates
    it with Grok via generate_ai_video (ground_keyframe=) so the leg-chain/concat/conform machinery is
    reused. FLF is NOT used here: it needs pixel-matched frames and morphs the background when you
    re-pose a figure (proven on seg_019 — see memory flf-cohesion-action-sequences). Returns
    (clip_or_None, keyframe_or_None); the caller threads the returned keyframe into the next beat."""
    from lib import visual_router as vr
    from lib import channel_style
    from lib.image_host import upload_image
    prompt = (beat.get("prompt") or narration or "").strip()
    try:
        dur = float(beat["dur"])
        kf_path = Path(scratch) / "keyframe.png"
        kf = keyframe or None    # reuse an operator-approved pre-authored keyframe (skip authoring)
        canon = (getattr(anchor_asset, "canonical_image_url", "") or "") if anchor_asset else ""
        sheet_local = ""
        if kf is not None:
            pass                 # reuse path: no grounding assembly, no ref hosting — go animate
        else:
            # machine_grounding=False (a console/terminal beat): the machine is NOT on screen — the
            # model sheet + 'MUST match this design' clause would misground the console close-up
            # (the terminal asset's reference_sheet points at the MACHINE sheet, so gate it here).
            sheet_local = ((getattr(anchor_asset, "reference_sheet", "") or "")
                           if (anchor_asset is not None and machine_grounding) else "")
            sheet_url = ""
            if sheet_local and Path(sheet_local).exists():
                # Re-host from disk; fall back to the stored URL rather than silently dropping the binding.
                sheet_url = (upload_image(sheet_local)
                             or getattr(anchor_asset, "reference_sheet_url", "") or "")
            elif anchor_asset is not None and machine_grounding:
                sheet_url = getattr(anchor_asset, "reference_sheet_url", "") or ""
            # Author the chained keyframe: anchor on the prior beat's keyframe (carries figure+room),
            # falling back to the canonical for the first beat; canonical+sheet ride as extra refs.
            anchor = prev_keyframe or canon or None
            if not anchor:
                _log.warning("chained beat %s: no anchor (bible/canonical missing) — this beat degrades "
                             "to non-chained and the action sequence loses cohesion here", beat_id)
            # Figure identity: inject any bible SUBJECT named in THIS BEAT's text (e.g. Cox) as an
            # extra reference sheet + name it in the prompt, so the recurring character stays
            # on-model. Matching the whole narration put the Cox sheet (and a "person MUST match
            # Cox" clause) under the OPERATOR-at-the-console beat; the beat's own text is the truth
            # of who is on screen. When the beat text names nobody, fall back to the narration only
            # if the caller says the figure can appear here (fig_from_narration — off for
            # terminal/console beats).
            fig_urls, fig_tokens = [], []
            _bt = f"{beat.get('prompt') or ''} {beat.get('label') or ''}".lower()
            _nl = (narration or "").lower()
            for _a in (getattr(bible, "assets", None) or []):
                _subj = getattr(_a, "subject", "") or ""
                _nm = _subj.split()[-1].lower() if _subj else ""
                if getattr(_a, "type", "") != "subject" or not _nm:
                    continue
                _hit = (re.search(rf"\b{re.escape(_nm)}\b", _bt)
                        or (fig_from_narration and re.search(rf"\b{re.escape(_nm)}\b", _nl)))
                if _hit:
                    _u = getattr(_a, "reference_sheet_url", "") or ""
                    _loc = getattr(_a, "reference_sheet", "") or ""
                    if _loc and Path(_loc).exists():
                        _u = upload_image(_loc) or _u   # stored sheet URLs expire — re-host from disk
                    if _u:
                        fig_urls.append(_u)
                    fig_tokens += [t for t in (getattr(_a, "identity_tokens", None) or []) if t]
            # Grounding refs, priority: LOCKED GOLD frame (identity 'plate'); character sheet(s);
            # the machine model sheet; a REAL photo of the machine; the room canonical. Bindings are
            # built IN THE SAME ORDER the edit model receives the images ([anchor] + extras): a
            # positional mislabel teaches the model to copy identity from the wrong image.
            extra, bindings = [], []
            if gold_ref:
                extra.append(gold_ref)
                bindings.append("this beat's locked GOLD frame — render the SAME person (same face, "
                                "build, gown) and the SAME machine, door, and room design as it, exactly")
            for _u in fig_urls:
                extra.append(_u)
                bindings.append("a character REFERENCE SHEET — every depiction of that person must "
                                "match it exactly (same face, build, hair, gown)")
            if sheet_url:
                extra.append(sheet_url)
                bindings.append("the machine's MODEL REFERENCE SHEET — every depiction of the machine "
                                "must copy that sheet exactly, never a different design")
            if real_photo:
                extra.append(real_photo)
                bindings.append("a REAL PHOTOGRAPH of the actual machine — copy its exact housing "
                                "design and proportions precisely, rendered in the illustration style")
            if prev_keyframe and canon:
                extra.append(canon)
                bindings.append("the room's canonical scene image (room layout/design reference)")
            # Keep the edit-model image list bounded: anchor + at most 4 refs, dropped from the tail —
            # gold/figure/sheet/photo outrank the canonical, which mostly repeats what gold carries.
            extra, bindings = extra[:4], bindings[:4]
            # NAME the Therac-25 + the character in the prompt so they can't drift, regardless of the
            # (advisory) fidelity gate; and bind each reference by its actual position in the image
            # list. Machine tokens only when the machine is actually on screen (machine_grounding).
            tokens = "; ".join([t for t in ((getattr(anchor_asset, "identity_tokens", None) or [])
                                             + (getattr(anchor_asset, "locked_attributes", None) or []))
                                if t]) if (anchor_asset is not None and machine_grounding) else ""
            ref_clause = ((" || REFERENCE IMAGES: after the first image (the scene anchor you are "
                           "editing), the additional reference images are, in order: "
                           + "; ".join(f"({n}) {b}" for n, b in enumerate(bindings, start=1)) + ".")
                          if bindings else "")
            figure_clause = (f" || The person MUST match the character reference sheet exactly (same face, "
                             f"build, hair, gown): {'; '.join(fig_tokens)}." if fig_tokens else "")
            if console_clause and not fig_tokens:
                # Console/terminal beat: the figure is the hospital OPERATOR, not a patient — without
                # this Nano dressed the operator in a patient's gown and staged a bunker-hatch wall.
                # (Gated on the LOCALE, not on machine_grounding: a montage room beat without machine
                # words must never be told to draw a console operator — it nearly hit Katie's beat.)
                figure_clause = (" || The figure at the console is the hospital radiation OPERATOR — "
                                 "a technician in plain 1980s work clothes (shirt/scrubs), NEVER a "
                                 "patient, NO hospital gown. Plain clinical control-room wall behind "
                                 "them — no hatches, no wheels, no vault doors.")
            machine_clause = (f" || The machine is the AECL Therac-25 and MUST match this design: {tokens}."
                              if tokens else "")
            # Guard the recurring Nano failure modes: the "comic-page" diptych and ghosted/fading figures.
            kf_prompt = channel_style.apply_to_prompt(prompt) + ref_clause + figure_clause + machine_clause + (
                " || COMPOSITION: ONE single continuous full-bleed illustration that fills the whole frame "
                "— NOT a multi-panel comic page, no split panels, no panel borders, gutters, or side-by-side "
                "frames; exactly ONE moment in time, never a before/after or comparison layout (proven "
                "failure: transition wording splits the frame into two panels). Every figure is solid "
                "and fully rendered, never faint, ghosted, or dissolving.")
            if anchor and os.environ.get("AI_POPULATED_KEYFRAMES", "1") != "0":
                kf = vr._populated_keyframe(
                    kf_prompt, anchor, kf_path,
                    description=(beat.get("label") or prompt), narration=narration,
                    extra_ref_urls=extra or None,
                    fidelity_ref=(sheet_local if (sheet_local and Path(sheet_local).exists()) else (sheet_url or "")),
                )
                # The keyframe gate over-rejects here (unreliable Therac fidelity check). If it exhausted,
                # USE the authored chained keyframe anyway (the last attempt is on disk) instead of dropping
                # to an ungrounded fallback that breaks cohesion — the operator eyeballs the take.
                if kf is None and kf_path.exists() and kf_path.stat().st_size > 1024:
                    _log.warning("chained beat %s: keyframe gate exhausted — using the authored chained "
                                 "keyframe anyway (operator eyeballs)", beat_id)
                    kf = str(kf_path)
        if not video:   # keyframe-preview mode: return the authored still, no paid video
            # Authoring failed -> None, NOT prev_keyframe: the preview must record a FAILED beat,
            # never silently pass off the previous beat's still as this beat's "authored" frame
            # (the operator would approve a duplicate and pay to animate the wrong pose).
            return None, (str(kf) if kf else None)
        spec = SimpleNamespace(
            description=prompt, effective_prompt=prompt, ai_prompt=prompt,
            ai_motion=beat.get("motion") or None, hard_shot=bool(beat.get("hard_shot")),
            type="ai_video", ai_style=mood, ai_reference_image=None, asset_ref=None, location_id=None,
            editorial_intent="", directors_move="", pacing="", shots=[], support_asset_refs=[], text_overlay=[])
        # enable_gemini defaults False on the CHAINED path: the semantic clip gate FALSE-rejected
        # good chained clips (beat3) into placeholders, and the operator eyeballs those takes.
        # Montage beats keep it on (parity with the old non-chained path). Layer-1
        # (file/static/dur) always runs inside generate_shot.
        asset = vr.generate_ai_video(beat_id, spec, scratch, dur, bible=bible, asset=anchor_asset,
                                     enable_gemini=enable_gemini, narration=narration,
                                     ground_keyframe=(str(kf) if kf else None))
        if asset is None or not getattr(asset, "path", None):
            return None, (str(kf) if kf else (str(prev_keyframe) if prev_keyframe else None))
        clip = (vr._trim_to_duration(asset.path, str(out_path), dur)
                if hasattr(vr, "_trim_to_duration") else asset.path)
        return (clip or None), (str(kf) if kf else (str(prev_keyframe) if prev_keyframe else None))
    except Exception:
        if not video:
            return None, None   # preview mode: report the failure honestly (see above)
        # Keep the chain alive on failure: thread the last good keyframe forward.
        return None, (str(prev_keyframe) if prev_keyframe else None)


def _placeholder_card(tag: str, label: str, lane: str, dur: float, out_path: str) -> str:
    """A deterministic channel-style placeholder clip for a beat that needs a manual pass (a manim
    diagram) or whose generation failed — holds the timing and marks the gap so it's reviewable."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1280, 720), (10, 20, 40))
    d = ImageDraw.Draw(img)
    def fnt(sz):
        try: return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", sz)
        except Exception: return ImageFont.load_default()
    d.text((90, 250), f"{tag}  -  needs a pass", font=fnt(40), fill=(232, 164, 76))
    d.text((90, 322), f"({lane})  {label}", font=fnt(26), fill=(190, 178, 150))
    d.text((90, 400), "placeholder - produce this beat on the manual pipeline", font=fnt(22), fill=(120, 110, 90))
    png = Path(str(out_path)).with_suffix(".png")
    img.save(png)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png),
                    "-t", str(round(dur, 2)), "-vf", "scale=1280:720,setsar=1", "-r", "24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)], check=True)
    return str(out_path)


def _concat(clips: list[str], out_path: str) -> str | None:
    """Concat beat clips (normalizing res/fps/sar) into one mp4. Returns the path or None."""
    clips = [c for c in clips if c]
    if not clips:
        return None
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    n = len(clips)
    fc = "".join(f"[{i}:v]scale=1280:720,fps=24,setsar=1[v{i}];" for i in range(n))
    fc += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1[out]"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc,
                        "-map", "[out]", "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(out_path)], check=True)
        return str(out_path)
    except Exception:
        return None


def _store_mixed_take(job: dict, pid: str, sid: str, rid: str, spawned_by: str, est: float,
                      slot_s: float, narration: str, mood, take_n: int, scratch: Path,
                      take_target: Path, bible, anchor_asset, plan: list[dict],
                      chained: bool = False) -> None:
    """Generate EVERY beat of a 'mixed' scene and concat into ONE complete take. Dispatchable beats
    (grok/flf) are generated; a manim/other beat — or one whose generation fails — becomes a labeled
    placeholder so the timing holds and the take still completes. Each beat's status is recorded so
    the operator can see exactly which beat needs fixing."""
    from lib.quality_gate import QualityGate
    beat_clips: list[str] = []
    beat_meta: list[dict] = []
    # GROUNDING is for every non-FLF beat (locale asset, content-keyed gold plate, machine
    # sheet/tokens/photo, preauthored-still reuse); CHAIN-CARRY (beat N's frame seeding N+1) is
    # only for chained action sequences with >1 chainable beat. A montage beat must never
    # inherit its neighbor's frame — but it deserves the same identity grounding (seg_022's
    # ungrounded montage drifted the machine and the door designs).
    chainable = [b for b in plan if b["dispatchable"] and not (b.get("lane") or "").startswith("flf")]
    use_chain = chained and len(chainable) > 1
    grounded = len(chainable) >= 1
    preauth = _load_preauthored_keyframes(pid, sid, rid) if grounded else {}  # operator-previewed stills
    prev_kf = None  # chained-keyframe carry: beat N's keyframe grounds beat N+1's
    prev_locale = None
    room_asset = _locale_asset(bible, anchor_asset, "room") if grounded else None
    term_asset = _locale_asset(bible, anchor_asset, "terminal") if grounded else None
    # Grounding refs are only consumed when a beat actually AUTHORS a keyframe — skip the hosting
    # spend/latency when every chainable beat reuses an operator-approved still.
    need_author = grounded and any(b["idx"] not in preauth for b in chainable)
    real_photo = _real_photo_ref(pid, room_asset) if need_author else None
    for b in plan:
        i = b["idx"]
        bdir = scratch / f"b{i}"; bdir.mkdir(parents=True, exist_ok=True)
        bout = scratch / f"beat{i}.mp4"
        clip = None
        status = "generated"
        if b["dispatchable"]:
            if not (b.get("lane") or "").startswith("flf"):
                # Grounded beat. Chained: ground on the prior beat's keyframe and thread it
                # forward, resetting at a locale boundary (the console frame must never seed the
                # treatment room). Montage: prev anchor stays None — grounding only, no carry.
                # Machine identity rides room beats when chained (continuity), and any beat whose
                # own text puts the machine on screen. (FLF beats keep the state-morph path.)
                locale = _beat_locale(b)
                if use_chain:
                    if prev_locale is not None and locale != prev_locale:
                        prev_kf = None
                    prev_locale = locale
                pk = preauth.get(i)
                machine = _machine_in_beat(b) or (use_chain and locale == "room")
                gold = None if pk else _scene_gold_ref(
                    pid, sid, i, beat_text=f"{b.get('prompt') or ''} {b.get('label') or ''}",
                    n_beats=len(chainable))
                clip, kf = _gen_chained_beat(f"{sid}_b{i}", b, str(bout), bdir, narration,
                                             bible, (term_asset if locale == "terminal" else room_asset),
                                             mood, (prev_kf if use_chain else None),
                                             gold_ref=gold, keyframe=pk,
                                             real_photo=(real_photo if (machine and not pk) else None),
                                             fig_from_narration=(use_chain and locale == "room"),
                                             machine_grounding=machine,
                                             console_clause=(locale == "terminal"),
                                             enable_gemini=(not use_chain))
                if use_chain and kf:
                    prev_kf = kf
            else:
                clip = _gen_beat(f"{sid}_b{i}", b, str(bout), bdir, narration, bible, anchor_asset, mood)
            if clip is None:
                status = "failed"
        else:
            status = "placeholder"
        if clip is None:  # non-dispatchable beat, OR a generation that failed -> labeled placeholder
            clip = _placeholder_card(f"BEAT {i}", b["label"], b["lane"], float(b["dur"]), str(bout))
        beat_clips.append(clip)
        # Record enough to REGENERATE just this beat later (prompt/lane/dur) and to REUSE its clip.
        beat_meta.append({
            "idx": i, "lane": b["lane"], "status": status, "label": b["label"],
            "prompt": b["prompt"], "flf": b.get("flf"), "dur": b["dur"], "motion": b.get("motion"),
            "hard_shot": bool(b.get("hard_shot")),
            "clip": f"projects/{pid}/assets/ai_segments/_takes_scratch/{sid}__take{take_n}/beat{i}.mp4",
        })

    conformed = _concat(beat_clips, str(take_target))
    if not conformed:
        job.update(status="failed", error="beat concat failed", ended_ts=_now()); return

    score = None; passed = False; issues: list[str] = []
    gspec = SimpleNamespace(description=narration, narration=narration, ai_style=mood)
    try:
        report = QualityGate(enable_gemini=True).evaluate(
            str(take_target), gspec, target_duration_s=slot_s, segment_id=sid)
        score, passed, issues = round(report.overall_score, 3), report.passed, report.issues[:4]
    except Exception as e:
        issues = [f"score unavailable: {type(e).__name__}: {e}"]

    needs = [m for m in beat_meta if m["status"] != "generated"]
    partial = bool(needs)
    rel = f"projects/{pid}/assets/ai_segments/{sid}__take{take_n}.mp4"
    preview = _graded_preview(pid, sid, take_n, str(take_target))
    note = ("; ".join(f"beat {m['idx']} ({m['lane']}) {m['status']}" for m in needs)
            or f"all {len(beat_meta)} beats generated")
    entry = {
        "take": take_n, "path": rel, "preview": preview, "score": score, "passed": bool(passed),
        "verdict": "accepted" if (passed and not partial) else "needs_review",
        "revision_id": rid, "spawned_by": spawned_by, "cost_usd": est,
        "provider": "mixed", "lane": "mixed", "partial": partial, "chained": use_chain,
        "beats": beat_meta, "note": note, "issues": issues,
        "vet": _vet_take(pid, sid, take_target, narration), "ts": _now(),
    }
    _append_take(pid, sid, entry)
    fb.append_event(pid, actor="system", type="take_generated", scene_id=sid, payload=entry)
    try:
        from lib import cost_ledger
        cost_ledger.log(provider="mixed", operation="mixed_beats", cost_usd=est, duration_s=slot_s,
                        ledger=str(PROJECTS_DIR / pid / "artifacts" / "cost_ledger.jsonl"),
                        scene_id=sid, take=take_n, revision_id=rid, kind="take_regen", beats=len(beat_meta))
    except Exception:
        pass
    job.update(status="succeeded", take=entry, ended_ts=_now())


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
        # House art-direction: the per-segment MOOD (channel ai_style) must ride into the
        # prompt builder, or the regen drifts off-style — the channel MEDIUM appended
        # downstream (duotone/ink/halftone) gets overpowered by vivid content. Prefer the
        # director's style-aware mood, else the canonical scored-script mood for the segment.
        mood = revision.get("ai_style") or scene.get("ai_style")

        spec = SimpleNamespace(
            description=(described.get("action_sequence") or [narration])[0] or narration,
            effective_prompt=prompt, ai_prompt=prompt,
            ai_motion=(revision.get("motion") or None),
            type="ai_video", ai_style=mood, ai_reference_image=None, asset_ref=None,
            location_id=None,
            editorial_intent=(revision.get("editorial_intent") or scene.get("editorial_intent") or ""),
            directors_move="",
            pacing=(revision.get("pacing") or scene.get("pacing") or ""),
            shots=[], support_asset_refs=[], text_overlay=[],
        )

        take_n = len(read_index(pid).get(sid, [])) + 1
        seg_dir = PROJECTS_DIR / pid / "assets" / "ai_segments"
        scratch = seg_dir / "_takes_scratch" / f"{sid}__take{take_n}"
        scratch.mkdir(parents=True, exist_ok=True)
        take_target = seg_dir / f"{sid}__take{take_n}.mp4"

        from lib import visual_router as vr
        from lib.quality_gate import GenerationHardStop, QualityGate

        # Anchor the take on the segment's amber-graded canonical reference (asset bible) so it
        # inherits the house look + locked subject design — the grounding the bulk pipeline uses
        # and the regen path previously skipped (bible=None -> off-style drift).
        bible, anchor_asset = _load_bible_asset(pid, sid)

        # MIXED multi-beat: generate every dispatchable beat + concat into one COMPLETE take
        # (each beat's status recorded). Non-mixed scenes fall through to the single-shot path.
        plan = _beat_plan(revision, slot_s, narration)
        if plan and any(b["dispatchable"] for b in plan):
            _store_mixed_take(job, pid, sid, rid, spawned_by, est, slot_s, narration, mood,
                              take_n, scratch, take_target, bible, anchor_asset, plan,
                              chained=bool(revision.get("chained")))
            return

        if gen_lane.startswith("flf"):
            # FLF lane (Kling, transition images): author start keyframe -> derive matched
            # end -> Kling interpolate -> conform to slot. Same path the fresh pipeline uses.
            from lib import flf as flf_mod
            from lib.scored_script import FLFSpec
            f = gen_flf or {}
            flf_spec = FLFSpec(
                start_prompt=f.get("start_prompt") or prompt,
                transition=f.get("transition") or prompt,
                drain=float(f.get("drain") if f.get("drain") is not None else 0.85),
                band=tuple(f["band"]) if f.get("band") else None,
                anchor=f.get("anchor") or "fresh",
                morph=f.get("morph"),
            )
            conformed = flf_mod.flf_segment(flf_spec, slot_s, str(take_target),
                                            keyframe_dir=str(scratch), bible=bible)
            if not conformed:
                job.update(status="failed", error="FLF generation failed (Nano/Kling/host)", ended_ts=_now()); return
        else:
            asset = vr.generate_ai_video(sid, spec, scratch, slot_s,
                                         bible=bible, asset=anchor_asset, enable_gemini=True, narration=narration)
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
        preview = _graded_preview(pid, sid, take_n, str(take_target))
        entry = {
            "take": take_n, "path": rel, "preview": preview, "score": score, "passed": passed,
            # a partial (mixed) take never auto-accepts — the operator eyeballs it.
            "verdict": "accepted" if (passed and not partial) else "needs_review",
            "revision_id": rid, "spawned_by": spawned_by, "cost_usd": est,
            "provider": _lane_provider(gen_lane), "lane": lane, "partial": partial,
            "note": ("live-action portion only — the diagram/other beats need a manual pass" if partial else ""),
            "issues": issues, "vet": _vet_take(pid, sid, take_target, narration), "ts": _now(),
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


# ---- per-beat regeneration (redo only the selected beats; reuse the rest) -------------------

def regen_beats(pid: str, sid: str, src_take_n: int, edits: list, spawned_by: str = "") -> dict:
    """Regenerate ONLY the selected beats of a mixed take, reuse the others' clips, re-concat into
    a NEW take. ``edits`` = [{idx, lane?, prompt?, desc?, flf?}]. Cost = only the edited dispatchable
    beats (reused beats are free). Returns a queued-job status record."""
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:
        pass
    src = next((t for t in read_index(pid).get(sid, []) if t.get("take") == src_take_n), None)
    if src is None:
        return {"status": "blocked", "error": f"take {src_take_n} not found"}
    src_beats = src.get("beats") or []
    if not src_beats:
        return {"status": "blocked", "error": "that take has no beats to regenerate"}
    emap = {int(e["idx"]): e for e in (edits or []) if e.get("idx") is not None}
    if not emap:
        return {"status": "blocked", "error": "select at least one beat to regenerate"}

    def _lane(i):
        return (emap.get(i, {}).get("lane")) or next((b["lane"] for b in src_beats if b["idx"] == i), "grok")

    def _dur(i):
        return next((float(b.get("dur") or 0) for b in src_beats if b["idx"] == i), 0.0) or max(2.0, (src_beats and 5.0) or 5.0)

    expected = round(sum(_beat_cost(_lane(i), _dur(i)) for i in emap if _lane(i) in DISPATCHABLE_LANES), 2)
    ceiling = float(os.environ.get("OPENMONTAGE_REGEN_MAX_USD", "1.00"))
    if expected > ceiling:
        return {"status": "blocked", "est_usd": expected,
                "error": f"~${expected:.2f} for the selected beats exceeds the ${ceiling:.2f} ceiling — raise OPENMONTAGE_REGEN_MAX_USD"}
    if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
        return {"status": "blocked", "error": "dispatch disabled (OPENMONTAGE_DISABLE_DISPATCH=1)", "est_usd": expected}
    if not os.environ.get("KIE_API_KEY"):
        return {"status": "blocked", "error": "KIE_API_KEY not set", "est_usd": expected}
    if not os.environ.get("GOOGLE_API_KEY"):
        return {"status": "blocked", "error": "GOOGLE_API_KEY not set", "est_usd": expected}

    with _LOCK:
        if (pid, sid) in _ACTIVE:
            busy = next((jid for jid, j in _JOBS.items()
                         if j.get("project_id") == pid and j.get("scene_id") == sid
                         and j.get("status") in ("queued", "running")), None)
            return {"status": "busy", "job_id": busy, "error": "a job is already running for this scene"}
        _ACTIVE.add((pid, sid))
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id, "project_id": pid, "scene_id": sid, "revision_id": src.get("revision_id"),
            "status": "queued", "est_usd": expected, "lane": "mixed", "partial": False,
            "created_ts": _now(), "started_ts": None, "ended_ts": None, "take": None, "error": None,
        }
    _POOL.submit(_run_regen_beats_job, pid, sid, src_take_n, edits, job_id, spawned_by, expected)
    return {"job_id": job_id, "status": "queued", "est_usd": expected, "lane": "mixed"}


def _run_regen_beats_job(pid, sid, src_take_n, edits, job_id, spawned_by, est) -> None:
    job = _JOBS[job_id]
    try:
        try:
            from lib.env_loader import load_env
            load_env()
        except Exception:
            pass
        if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
            job.update(status="blocked", error="dispatch disabled", ended_ts=_now()); return
        if not os.environ.get("KIE_API_KEY"):
            job.update(status="blocked", error="KIE_API_KEY not set", ended_ts=_now()); return
        job.update(status="running", started_ts=_now())

        data = scenes_mod.load_scenes(pid)
        scene = next((s for s in data["scenes"] if s["id"] == sid), None)
        src = next((t for t in read_index(pid).get(sid, []) if t.get("take") == src_take_n), None)
        if scene is None or src is None:
            job.update(status="failed", error="scene or source take vanished", ended_ts=_now()); return
        src_beats = sorted(src.get("beats") or [], key=lambda b: b.get("idx", 0))
        slot_s = float(scene.get("slot_s") or 0)
        narration = scene.get("narration", "")
        mood = scene.get("ai_style")
        emap = {int(e["idx"]): e for e in (edits or []) if e.get("idx") is not None}

        take_n = len(read_index(pid).get(sid, [])) + 1
        seg_dir = PROJECTS_DIR / pid / "assets" / "ai_segments"
        scratch = seg_dir / "_takes_scratch" / f"{sid}__take{take_n}"
        scratch.mkdir(parents=True, exist_ok=True)
        take_target = seg_dir / f"{sid}__take{take_n}.mp4"
        bible, anchor_asset = _load_bible_asset(pid, sid)
        # Printing-press re-roll: re-generated beats ground on their locked GOLD plate + the
        # neighbouring kept beats, so a touch-up matches the approved standard instead of dice-rolling.
        chained = bool(src.get("chained"))
        prev_kf = None
        prev_locale = None
        # GROUNDING for every non-FLF re-roll (chained or montage); CHAIN-CARRY only when chained.
        # Hoist the (network-hosting) grounding refs only when an edited beat will consume them.
        _regen_grounds = any(
            not ((emap[x["idx"]].get("lane") or x.get("lane") or "grok").startswith("flf"))
            for x in src_beats if x["idx"] in emap)
        room_asset = _locale_asset(bible, anchor_asset, "room") if _regen_grounds else None
        term_asset = _locale_asset(bible, anchor_asset, "terminal") if _regen_grounds else None
        real_photo = _real_photo_ref(pid, room_asset) if _regen_grounds else None
        # Reuse the OPERATOR-APPROVED preview stills of the take's revision: a motion-only
        # re-roll must not dice-roll the approved keyframe away with it.
        preauth = (_load_preauthored_keyframes(pid, sid, src.get("revision_id") or "")
                   if _regen_grounds else {})

        beat_clips = []
        beat_meta = []
        for sb in src_beats:
            i = sb["idx"]
            bout = scratch / f"beat{i}.mp4"
            clip_rel = f"projects/{pid}/assets/ai_segments/_takes_scratch/{sid}__take{take_n}/beat{i}.mp4"
            if i in emap:
                e = emap[i]
                lane = e.get("lane") or sb.get("lane") or "grok"
                # Fall back to the beat's own (concrete) prompt/label before the whole narration —
                # an empty redo box that fell through to the generic narration is what failed beat 2.
                prompt = (e.get("prompt") or sb.get("prompt") or sb.get("label") or narration or "").strip()
                dur = float(sb.get("dur") or 0) or max(2.0, slot_s / max(1, len(src_beats)))
                beat = {"lane": lane, "prompt": prompt, "flf": e.get("flf") or sb.get("flf"), "dur": dur,
                        "label": (e.get("desc") or prompt[:60] or lane),
                        # Motion rides too (an edit can pin e.g. a locked camera); without this a
                        # re-rolled beat silently lost its camera move to the synthetic default.
                        "motion": e.get("motion") or sb.get("motion"),
                        "hard_shot": bool(e.get("hard_shot") if e.get("hard_shot") is not None
                                          else sb.get("hard_shot"))}
                bdir = scratch / f"b{i}"; bdir.mkdir(parents=True, exist_ok=True)
                clip = None
                status = "generated"
                if lane in DISPATCHABLE_LANES:
                    if not lane.startswith("flf"):
                        locale = _beat_locale(beat)
                        if chained:
                            if prev_locale is not None and locale != prev_locale:
                                prev_kf = None   # locale boundary: never seed the room with the console
                            prev_locale = locale
                        _n_chain = sum(1 for x in src_beats
                                       if (x.get("lane") or "grok") in DISPATCHABLE_LANES
                                       and not (x.get("lane") or "").startswith("flf"))
                        pk = None if e.get("prompt") else preauth.get(i)  # a rewritten beat re-authors; a
                        # motion/lane-only edit KEEPS the approved still
                        machine = _machine_in_beat(beat) or (chained and locale == "room")
                        clip, kf = _gen_chained_beat(f"{sid}_b{i}", beat, str(bout), bdir, narration,
                                                     bible, (term_asset if locale == "terminal" else room_asset),
                                                     mood, (prev_kf if chained else None), keyframe=pk,
                                                     gold_ref=(None if pk else _scene_gold_ref(
                                                         pid, sid, i, beat_text=f"{prompt} {beat.get('label') or ''}",
                                                         n_beats=_n_chain)),
                                                     real_photo=(real_photo if (machine and not pk) else None),
                                                     fig_from_narration=(chained and locale == "room"),
                                                     machine_grounding=machine,
                                                     console_clause=(locale == "terminal"),
                                                     enable_gemini=(not chained))
                        if chained and kf:
                            prev_kf = kf
                    else:
                        clip = _gen_beat(f"{sid}_b{i}", beat, str(bout), bdir, narration, bible, anchor_asset, mood)
                    if clip is None:
                        status = "failed"
                else:
                    status = "placeholder"
                if clip is None:
                    clip = _placeholder_card(f"BEAT {i}", beat["label"], lane, dur, str(bout))
                beat_meta.append({"idx": i, "lane": lane, "status": status, "label": beat["label"],
                                  "prompt": prompt, "flf": beat["flf"], "dur": dur, "motion": beat.get("motion"),
                                  "hard_shot": bool(beat.get("hard_shot")), "clip": clip_rel})
            else:
                # reuse the source beat's clip (recorded path, else the take-scratch convention)
                rel = (sb.get("clip") or "").split(f"projects/{pid}/")[-1]
                srcp = (PROJECTS_DIR / pid / rel) if rel else \
                    (seg_dir / "_takes_scratch" / f"{sid}__take{src_take_n}" / f"beat{i}.mp4")
                if srcp.exists():
                    shutil.copy(str(srcp), str(bout)); clip = str(bout)
                else:
                    clip = _placeholder_card(f"BEAT {i}", sb.get("label", ""), sb.get("lane", "grok"),
                                             float(sb.get("dur") or 5.0), str(bout))
                # Thread the kept beat's authored keyframe forward so a later re-rolled beat grounds on it.
                _kept_kf = seg_dir / "_takes_scratch" / f"{sid}__take{src_take_n}" / f"b{i}" / "keyframe.png"
                if _kept_kf.exists():
                    prev_kf = str(_kept_kf)
                # Track the kept beat's locale too — otherwise the boundary reset misfires around it
                # (a kept console beat would seed a re-rolled room beat, or trigger a bogus reset).
                if chained and not (sb.get("lane") or "").startswith("flf"):
                    prev_locale = _beat_locale(sb)
                beat_meta.append({**{k: sb.get(k) for k in ("idx", "lane", "status", "label", "prompt", "flf", "dur", "motion", "hard_shot")},
                                  "clip": clip_rel})
            beat_clips.append(clip)

        conformed = _concat(beat_clips, str(take_target))
        if not conformed:
            job.update(status="failed", error="beat concat failed", ended_ts=_now()); return

        from lib.quality_gate import QualityGate
        score = None; passed = False; issues = []
        gspec = SimpleNamespace(description=narration, narration=narration, ai_style=mood)
        try:
            report = QualityGate(enable_gemini=True).evaluate(str(take_target), gspec, target_duration_s=slot_s, segment_id=sid)
            score, passed, issues = round(report.overall_score, 3), report.passed, report.issues[:4]
        except Exception as e:
            issues = [f"score unavailable: {type(e).__name__}: {e}"]

        needs = [m for m in beat_meta if m["status"] != "generated"]
        partial = bool(needs)
        rel = f"projects/{pid}/assets/ai_segments/{sid}__take{take_n}.mp4"
        preview = _graded_preview(pid, sid, take_n, str(take_target))
        note = (f"regen of take {src_take_n} beats {sorted(emap)}; "
                + ("; ".join(f"beat {m['idx']} {m['status']}" for m in needs) or "all beats present"))
        entry = {
            "take": take_n, "path": rel, "preview": preview, "score": score, "passed": bool(passed),
            "verdict": "accepted" if (passed and not partial) else "needs_review",
            "revision_id": src.get("revision_id"), "spawned_by": spawned_by, "cost_usd": est,
            "provider": "mixed", "lane": "mixed", "partial": partial, "chained": chained, "beats": beat_meta,
            "note": note, "issues": issues,
            "vet": _vet_take(pid, sid, take_target, narration), "ts": _now(),
        }
        _append_take(pid, sid, entry)
        fb.append_event(pid, actor="system", type="take_generated", scene_id=sid, payload=entry)
        try:
            from lib import cost_ledger
            cost_ledger.log(provider="mixed", operation="regen_beats", cost_usd=est, duration_s=slot_s,
                            ledger=str(PROJECTS_DIR / pid / "artifacts" / "cost_ledger.jsonl"),
                            scene_id=sid, take=take_n, kind="take_regen", beats=len(emap))
        except Exception:
            pass
        job.update(status="succeeded", take=entry, ended_ts=_now())
    except Exception as e:
        name = type(e).__name__
        job.update(status=("blocked" if name == "GenerationHardStop" else "failed"),
                   error=f"{name}: {e}", ended_ts=_now())
    finally:
        with _LOCK:
            _ACTIVE.discard((pid, sid))


# ---- keyframe preview (gate on the cheap $0.04 still before paying for the $0.10+ video) ------

def _keyframe_review_dir(pid: str, sid: str, rid: str) -> Path:
    return PROJECTS_DIR / pid / "assets" / "ai_segments" / "_keyframe_review" / f"{sid}__{rid}"


def _load_preauthored_keyframes(pid: str, sid: str, rid: str) -> dict:
    """{beat_idx: keyframe_path_or_url} of operator-previewed keyframes for this revision, if any.

    Prefers the LOCAL still on disk (the dispatch pipeline re-hosts a local path fresh via the
    selector) — the stored url lives on an expiring temp host (tmpfiles/provider CDN) and can be
    dead within hours of the preview. Entries that failed to author are skipped, so a dead ref can
    never be handed to a paid beat as its ground keyframe."""
    f = _keyframe_review_dir(pid, sid, rid) / "keyframes.json"
    if not f.exists():
        return {}
    try:
        out = {}
        for k in json.loads(f.read_text(encoding="utf-8")):
            if k.get("idx") is None or k.get("status") != "authored":
                continue
            local = (PROJECTS_DIR / pid / k["media"]) if k.get("media") else None
            if local is not None and local.is_file() and local.stat().st_size > 1024:
                out[int(k["idx"])] = str(local)
            elif k.get("url"):
                out[int(k["idx"])] = k["url"]
        return out
    except Exception:
        return {}


def author_scene_keyframes(pid: str, sid: str, rid: str) -> dict:
    """Enqueue KEYFRAME-PREVIEW: author the chained keyframe SET (cheap Nano, NO video) as a background
    job so the operator can eyeball/approve the $0.04 stills before paying for the $0.10+ clips; the
    dispatch then REUSES the approved keyframes. Returns a queued job (poll GET /jobs/{id} for the
    stills). Runs as a job — a synchronous authoring call blocks the request for minutes."""
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:
        pass
    data = scenes_mod.load_scenes(pid)
    scene = next((s for s in data["scenes"] if s["id"] == sid), None)
    if scene is None:
        raise KeyError(sid)
    revision = _find_revision(pid, sid, rid)
    if revision is None:
        return {"status": "blocked", "error": "revision not found in log"}
    plan = _beat_plan(revision, float(scene.get("slot_s") or 0), scene.get("narration", ""))
    if not plan:
        return {"status": "not_applicable", "error": "keyframe preview applies to a mixed multi-beat scene"}
    # Guard PARITY with dispatch: _store_mixed_take grounds (and reuses stills for) every non-FLF
    # dispatchable beat — chained AND montage alike — so preview whenever at least one exists.
    n = sum(1 for b in plan if b["dispatchable"] and not (b.get("lane") or "").startswith("flf"))
    if n < 1:
        return {"status": "not_applicable",
                "error": "this plan has no groundable (non-FLF dispatchable) beats to preview"}
    if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
        return {"status": "blocked", "error": "dispatch disabled (OPENMONTAGE_DISABLE_DISPATCH=1)"}
    if not os.environ.get("KIE_API_KEY"):
        return {"status": "blocked", "error": "KIE_API_KEY not set — keyframe authoring disabled"}
    est = round(n * 0.04, 2)  # keyframes only, no video (per-beat Nano; gate retries can cost more)
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
            "status": "queued", "est_usd": est, "kind": "keyframes", "keyframes": None,
            "created_ts": _now(), "started_ts": None, "ended_ts": None, "error": None,
        }
    _POOL.submit(_run_author_keyframes_job, pid, sid, rid, job_id)
    return {"job_id": job_id, "status": "queued", "est_usd": est, "kind": "keyframes"}


def _run_author_keyframes_job(pid: str, sid: str, rid: str, job_id: str) -> None:
    """Worker: author the chained keyframe SET (no video) and store the stills on the job for review."""
    job = _JOBS[job_id]
    try:
        try:
            from lib.env_loader import load_env
            load_env()
        except Exception:
            pass
        # Re-check the spend guards inside the worker (defense in depth, mirrors _run_take_job).
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
        narration = scene.get("narration", "")
        plan = _beat_plan(revision, float(scene.get("slot_s") or 0), narration)
        review = _keyframe_review_dir(pid, sid, rid)
        # Idempotent per revision (printing press, not slot machine): if this rid's stills already
        # exist and every one authored, return them instead of paying to re-author the same plan
        # (covers a re-click and crash-after-author recovery — this job once failed AFTER the spend).
        prior = review / "keyframes.json"
        if prior.exists():
            try:
                frames = json.loads(prior.read_text(encoding="utf-8"))
                if frames and all(k.get("status") == "authored" for k in frames):
                    job.update(status="succeeded", keyframes=frames, ended_ts=_now(),
                               error=None, est_usd=0.0)  # nothing spent on this run
                    return
            except Exception:
                pass
        review.mkdir(parents=True, exist_ok=True)
        bible, anchor_asset = _load_bible_asset(pid, sid)
        from lib.image_host import upload_image
        _n_chainable = sum(1 for x in plan
                           if x["dispatchable"] and not (x.get("lane") or "").startswith("flf"))
        # Match DISPATCH semantics exactly: carry/chained grounding only when >1 chainable beat
        # (a 1-chainable chained revision dispatches as montage — preview must ground the same way).
        chained = bool(revision.get("chained")) and _n_chainable > 1
        prev_kf = None
        prev_locale = None
        room_asset = _locale_asset(bible, anchor_asset, "room")
        term_asset = _locale_asset(bible, anchor_asset, "terminal")
        real_photo = _real_photo_ref(pid, room_asset)
        frames = []
        for b in plan:
            if not (b["dispatchable"] and not (b.get("lane") or "").startswith("flf")):
                continue
            i = b["idx"]
            bdir = review / f"b{i}"; bdir.mkdir(parents=True, exist_ok=True)
            locale = _beat_locale(b)
            if chained:
                if prev_locale is not None and locale != prev_locale:
                    prev_kf = None   # locale boundary: don't seed the room with the console frame
                prev_locale = locale
            _n_chain = sum(1 for x in plan
                           if x["dispatchable"] and not (x.get("lane") or "").startswith("flf"))
            machine = _machine_in_beat(b) or (chained and locale == "room")
            _clip, kf = _gen_chained_beat(f"{sid}_b{i}", b, str(bdir / "out.mp4"), bdir, narration,
                                          bible, (term_asset if locale == "terminal" else room_asset),
                                          None, (prev_kf if chained else None),
                                          gold_ref=_scene_gold_ref(
                                              pid, sid, i,
                                              beat_text=f"{b.get('prompt') or ''} {b.get('label') or ''}",
                                              n_beats=_n_chain),
                                          video=False,
                                          real_photo=(real_photo if machine else None),
                                          fig_from_narration=(chained and locale == "room"),
                                          machine_grounding=machine,
                                          console_clause=(locale == "terminal"))
            media = f"assets/ai_segments/_keyframe_review/{sid}__{rid}/b{i}/keyframe.png"
            rel = f"projects/{pid}/{media}"
            if not kf:
                frames.append({"idx": i, "label": b["label"], "path": rel, "media": None, "url": None, "status": "failed"})
                continue
            prev_kf = kf
            local = bdir / "keyframe.png"
            url = None
            if str(kf).startswith(("http://", "https://")):
                url = str(kf)
                try:
                    import requests
                    r = requests.get(str(kf), timeout=90)
                    r.raise_for_status()                # never write an HTTP error body as a PNG
                    if len(r.content) > 1024:
                        local.write_bytes(r.content)
                except Exception:
                    _log.warning("keyframe preview %s b%s: could not mirror %s to disk — the grid "
                                 "will render the (expiring) hosted copy", sid, i, kf)
            elif Path(str(kf)).exists():
                if Path(str(kf)) != local:
                    local.write_bytes(Path(str(kf)).read_bytes())
                url = upload_image(str(local))
            # "authored" only when the operator can actually SEE it (local mirror or live URL) —
            # media only when the durable local copy really exists (the reuse loader prefers it).
            ok = local.is_file() and local.stat().st_size > 1024
            # Advisory design-fidelity badge (room beats only — the machine refs are the rubric).
            vet = _vet_keyframe(pid, room_asset, local) if (ok and machine) else None
            frames.append({"idx": i, "label": b["label"], "path": rel, "media": (media if ok else None),
                           "url": url, "status": "authored" if (ok or url) else "failed", "vet": vet})
        (review / "keyframes.json").write_text(json.dumps(frames, indent=2), encoding="utf-8")
        try:
            fb.append_event(pid, actor="system", type="keyframes_authored", scene_id=sid,
                            payload={"revision_id": rid, "keyframes": frames})
        except Exception:
            # The event log is history, not truth — a logging hiccup must never mark a job whose
            # PAID stills are already safely on disk as failed (it did once: unregistered type).
            _log.exception("keyframes_authored event append failed for %s/%s", sid, rid)
        job.update(status="succeeded", keyframes=frames, ended_ts=_now())
    except Exception as e:
        name = type(e).__name__
        job.update(status=("blocked" if name == "GenerationHardStop" else "failed"),
                   error=f"{name}: {e}", ended_ts=_now())
    finally:
        with _LOCK:
            _ACTIVE.discard((pid, sid))


def reroll_scene_keyframe(pid: str, sid: str, rid: str, idx: int, hint: str = "") -> dict:
    """Enqueue a RE-ROLL of ONE previewed keyframe (~$0.04): re-author just that beat's still —
    grounded on its per-beat gold plate, its locale asset, and the nearest APPROVED prior still —
    keeping every other beat untouched. ``hint`` (optional) REPLACES the beat's prompt for this
    roll, so the operator can pin the exact pose ("torso propped on his elbows, legs flat on the
    table"). This is the printing-press loop for stills: eyeball -> fix one plate -> eyeball."""
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:
        pass
    data = scenes_mod.load_scenes(pid)
    scene = next((s for s in data["scenes"] if s["id"] == sid), None)
    if scene is None:
        raise KeyError(sid)
    revision = _find_revision(pid, sid, rid)
    if revision is None:
        return {"status": "blocked", "error": "revision not found in log"}
    plan = _beat_plan(revision, float(scene.get("slot_s") or 0), scene.get("narration", ""))
    target = next((b for b in plan if b["idx"] == idx and b["dispatchable"]
                   and not (b.get("lane") or "").startswith("flf")), None)
    if target is None:
        return {"status": "not_applicable", "error": f"beat {idx} is not a chainable beat of this plan"}
    if not (_keyframe_review_dir(pid, sid, rid) / "keyframes.json").exists():
        return {"status": "not_applicable", "error": "no previewed keyframe set for this revision — "
                                                     "run Preview keyframes first"}
    if os.environ.get("OPENMONTAGE_DISABLE_DISPATCH") == "1":
        return {"status": "blocked", "error": "dispatch disabled (OPENMONTAGE_DISABLE_DISPATCH=1)"}
    if not os.environ.get("KIE_API_KEY"):
        return {"status": "blocked", "error": "KIE_API_KEY not set — keyframe authoring disabled"}
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
            "status": "queued", "est_usd": 0.04, "kind": "keyframes", "keyframes": None,
            "created_ts": _now(), "started_ts": None, "ended_ts": None, "error": None,
        }
    _POOL.submit(_run_reroll_keyframe_job, pid, sid, rid, idx, (hint or "").strip(), job_id)
    return {"job_id": job_id, "status": "queued", "est_usd": 0.04, "kind": "keyframes"}


def _run_reroll_keyframe_job(pid: str, sid: str, rid: str, idx: int, hint: str, job_id: str) -> None:
    """Worker: re-author ONE beat's preview still and refresh keyframes.json + the event log."""
    job = _JOBS[job_id]
    try:
        try:
            from lib.env_loader import load_env
            load_env()
        except Exception:
            pass
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
        narration = scene.get("narration", "")
        plan = _beat_plan(revision, float(scene.get("slot_s") or 0), narration)
        chain = [b for b in plan if b["dispatchable"] and not (b.get("lane") or "").startswith("flf")]
        b = next((x for x in chain if x["idx"] == idx), None)
        if b is None:
            job.update(status="failed", error=f"beat {idx} vanished from the plan", ended_ts=_now()); return
        review = _keyframe_review_dir(pid, sid, rid)
        bdir = review / f"b{idx}"; bdir.mkdir(parents=True, exist_ok=True)
        bible, scene_asset = _load_bible_asset(pid, sid)
        chained = bool(revision.get("chained")) and len(chain) > 1  # dispatch-effective semantics
        locale = _beat_locale(b)
        beat_asset = _locale_asset(bible, scene_asset, locale)
        machine = _machine_in_beat(b) or (chained and locale == "room")
        real_photo = _real_photo_ref(pid, beat_asset) if machine else None
        # CHAINED: ground on the nearest APPROVED prior still in the SAME locale (never across
        # the boundary). MONTAGE: no neighbor carry — the beat stands alone on its own grounding.
        prev_kf = None
        if chained:
            for x in reversed([x for x in chain if x["idx"] < idx]):
                if _beat_locale(x) != locale:
                    break
                cand = review / f"b{x['idx']}" / "keyframe.png"
                if cand.is_file() and cand.stat().st_size > 1024:
                    prev_kf = str(cand)
                    break
        if hint:
            b = dict(b)
            b["prompt"] = hint  # the operator's pose language REPLACES the beat prompt for this roll
        gold = _scene_gold_ref(pid, sid, idx,
                               beat_text=f"{b.get('prompt') or ''} {b.get('label') or ''}",
                               n_beats=len(chain))
        # Back up the current still so a worse roll never destroys an approved frame.
        local = bdir / "keyframe.png"
        if local.is_file():
            n = 1
            while (bdir / f"keyframe_r{n}.png").exists():
                n += 1
            shutil.copy(str(local), str(bdir / f"keyframe_r{n}.png"))
        _clip, kf = _gen_chained_beat(f"{sid}_b{idx}", b, str(bdir / "out.mp4"), bdir, narration,
                                      bible, beat_asset, None, prev_kf, gold_ref=gold, video=False,
                                      real_photo=real_photo,
                                      fig_from_narration=(chained and locale == "room"),
                                      machine_grounding=machine,
                                      console_clause=(locale == "terminal"))
        kjson = review / "keyframes.json"
        frames = json.loads(kjson.read_text(encoding="utf-8")) if kjson.exists() else []
        entry = next((f for f in frames if f.get("idx") == idx), None)
        if not kf:
            job.update(status="failed", error=f"beat {idx} re-roll failed to author (prior still kept)",
                       keyframes=frames, ended_ts=_now())
            return
        from lib.image_host import upload_image
        url = None
        if str(kf).startswith(("http://", "https://")):
            url = str(kf)
            try:
                import requests
                r = requests.get(str(kf), timeout=90)
                r.raise_for_status()
                if len(r.content) <= 1024:
                    raise RuntimeError(f"mirrored body too small ({len(r.content)} bytes)")
                local.write_bytes(r.content)
            except Exception as e:
                # Disk is truth: dispatch reuses the LOCAL still (hosted URLs expire), so a failed
                # mirror must FAIL the roll — succeeding here leaves the OLD pose on disk and the
                # operator pays to animate the wrong frame (caught live on seg_024 b2).
                _log.warning("keyframe reroll %s b%s: could not mirror %s to disk", sid, idx, kf)
                job.update(status="failed", keyframes=frames, ended_ts=_now(),
                           error=f"beat {idx} re-rolled but the new frame could not be mirrored to "
                                 f"disk ({type(e).__name__}) — prior still kept; re-roll again")
                return
        else:
            if Path(str(kf)) != local:
                local.write_bytes(Path(str(kf)).read_bytes())
            url = upload_image(str(local))
        ok = local.is_file() and local.stat().st_size > 1024
        media = f"assets/ai_segments/_keyframe_review/{sid}__{rid}/b{idx}/keyframe.png"
        if entry is None:
            entry = {"idx": idx, "label": b.get("label") or "", "path": f"projects/{pid}/{media}"}
            frames.append(entry)
        vet = _vet_keyframe(pid, beat_asset, local) if (ok and machine) else None
        entry.update(media=(media if ok else None), url=url,
                     status="authored" if (ok or url) else "failed", vet=vet)
        kjson.write_text(json.dumps(frames, indent=2), encoding="utf-8")
        try:
            # The hint is a DRIFT SIGNAL, not just a prompt: when the same fix keeps being
            # typed ("rounded head", "hospital gown"), that attribute is missing from the
            # asset's identity tokens — promote it via lib/identity_tokens (see its docstring).
            fb.append_event(pid, actor="system", type="keyframes_authored", scene_id=sid,
                            payload={"revision_id": rid, "keyframes": frames,
                                     "reroll": {"idx": idx, "hint": hint}})
        except Exception:
            _log.exception("keyframes_authored event append failed for %s/%s", sid, rid)
        job.update(status="succeeded", keyframes=frames, ended_ts=_now())
    except Exception as e:
        name = type(e).__name__
        job.update(status=("blocked" if name == "GenerationHardStop" else "failed"),
                   error=f"{name}: {e}", ended_ts=_now())
    finally:
        with _LOCK:
            _ACTIVE.discard((pid, sid))
