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
import math
import os
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
            "flf": b.get("flf"),
            "dur": round(max(2.0, slot_s * w / tot), 2),
            "label": (b.get("desc") or b.get("beat") or b.get("prompt") or bl)[:60],
            "dispatchable": bl in DISPATCHABLE_LANES,
        })
    return plan


def _gen_beat(beat_id: str, beat: dict, out_path: str, scratch: Path, narration: str,
              bible, anchor_asset, mood) -> str | None:
    """Generate ONE dispatchable beat (grok or flf) at ``beat['dur']`` -> out_path. Anchors on the
    asset bible (house look). Returns the conformed clip path or None on failure."""
    from lib import visual_router as vr
    lane = beat["lane"]; prompt = beat["prompt"] or narration; dur = float(beat["dur"])
    if lane.startswith("flf"):
        from lib import flf as flf_mod
        from lib.scored_script import FLFSpec
        f = beat.get("flf") or {}
        spec = FLFSpec(start_prompt=f.get("start_prompt") or prompt,
                       transition=f.get("transition") or prompt,
                       drain=float(f.get("drain", 0.85)),
                       band=tuple(f["band"]) if f.get("band") else None,
                       anchor=f.get("anchor") or "fresh", morph=f.get("morph"))
        return flf_mod.flf_segment(spec, dur, str(out_path), keyframe_dir=str(scratch), bible=bible) or None
    spec = SimpleNamespace(
        description=prompt, effective_prompt=prompt, ai_prompt=prompt, ai_motion=None,
        type="ai_video", ai_style=mood, ai_reference_image=None, asset_ref=None, location_id=None,
        editorial_intent="", directors_move="", pacing="", shots=[], support_asset_refs=[], text_overlay=[])
    asset = vr.generate_ai_video(beat_id, spec, scratch, dur, bible=bible, asset=anchor_asset,
                                 enable_gemini=True, narration=narration)
    if asset is None or not getattr(asset, "path", None):
        return None
    return (vr._trim_to_duration(asset.path, str(out_path), dur)
            if hasattr(vr, "_trim_to_duration") else asset.path) or None


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
                      take_target: Path, bible, anchor_asset, plan: list[dict]) -> None:
    """Generate EVERY beat of a 'mixed' scene and concat into ONE complete take. Dispatchable beats
    (grok/flf) are generated; a manim/other beat — or one whose generation fails — becomes a labeled
    placeholder so the timing holds and the take still completes. Each beat's status is recorded so
    the operator can see exactly which beat needs fixing."""
    from lib.quality_gate import QualityGate
    beat_clips: list[str] = []
    beat_meta: list[dict] = []
    for b in plan:
        i = b["idx"]
        bdir = scratch / f"b{i}"; bdir.mkdir(parents=True, exist_ok=True)
        bout = scratch / f"beat{i}.mp4"
        clip = None
        status = "generated"
        if b["dispatchable"]:
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
            "prompt": b["prompt"], "flf": b.get("flf"), "dur": b["dur"],
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
        "provider": "mixed", "lane": "mixed", "partial": partial,
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
            ai_motion=(described.get("manner") or None),
            type="ai_video", ai_style=mood, ai_reference_image=None, asset_ref=None,
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

        # Anchor the take on the segment's amber-graded canonical reference (asset bible) so it
        # inherits the house look + locked subject design — the grounding the bulk pipeline uses
        # and the regen path previously skipped (bible=None -> off-style drift).
        bible, anchor_asset = _load_bible_asset(pid, sid)

        # MIXED multi-beat: generate every dispatchable beat + concat into one COMPLETE take
        # (each beat's status recorded). Non-mixed scenes fall through to the single-shot path.
        plan = _beat_plan(revision, slot_s, narration)
        if plan and any(b["dispatchable"] for b in plan):
            _store_mixed_take(job, pid, sid, rid, spawned_by, est, slot_s, narration, mood,
                              take_n, scratch, take_target, bible, anchor_asset, plan)
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
                drain=float(f.get("drain", 0.85)),
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

        beat_clips = []
        beat_meta = []
        for sb in src_beats:
            i = sb["idx"]
            bout = scratch / f"beat{i}.mp4"
            clip_rel = f"projects/{pid}/assets/ai_segments/_takes_scratch/{sid}__take{take_n}/beat{i}.mp4"
            if i in emap:
                e = emap[i]
                lane = e.get("lane") or sb.get("lane") or "grok"
                prompt = (e.get("prompt") or sb.get("prompt") or narration or "").strip()
                dur = float(sb.get("dur") or 0) or max(2.0, slot_s / max(1, len(src_beats)))
                beat = {"lane": lane, "prompt": prompt, "flf": e.get("flf") or sb.get("flf"), "dur": dur,
                        "label": (e.get("desc") or prompt[:60] or lane)}
                bdir = scratch / f"b{i}"; bdir.mkdir(parents=True, exist_ok=True)
                clip = None
                status = "generated"
                if lane in DISPATCHABLE_LANES:
                    clip = _gen_beat(f"{sid}_b{i}", beat, str(bout), bdir, narration, bible, anchor_asset, mood)
                    if clip is None:
                        status = "failed"
                else:
                    status = "placeholder"
                if clip is None:
                    clip = _placeholder_card(f"BEAT {i}", beat["label"], lane, dur, str(bout))
                beat_meta.append({"idx": i, "lane": lane, "status": status, "label": beat["label"],
                                  "prompt": prompt, "flf": beat["flf"], "dur": dur, "clip": clip_rel})
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
                beat_meta.append({**{k: sb.get(k) for k in ("idx", "lane", "status", "label", "prompt", "flf", "dur")},
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
            "provider": "mixed", "lane": "mixed", "partial": partial, "beats": beat_meta,
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
