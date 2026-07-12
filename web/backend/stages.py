"""Stills-first workflow stages (dashboard v2).

The production order per scene is: narration -> entity sheets (project-wide
gate) -> stills + storyboard animatic (per-scene gate) -> video. Stage state
is derived from the append-only feedback event log — nothing here mutates
takes or media except building animatics via lib.animatic.

Gates are HARD by design ("once these are all vetted we move onto video"):
approve-and-dispatch checks `stills_gate_open` before spending.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from web.backend import feedback as fb
from web.backend import scenes as scenes_mod

PROJECTS_DIR = Path("projects")


def _events(pid: str) -> list[dict]:
    try:
        return fb.read_events(pid)
    except Exception:
        p = PROJECTS_DIR / pid / "feedback" / "events.jsonl"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# ---------------------------------------------------------------------------
# entity sheets
# ---------------------------------------------------------------------------

def _census_path(pid: str) -> Path:
    return PROJECTS_DIR / pid / "artifacts" / "entity_census.json"


def list_entities(pid: str) -> dict:
    """Census entities + their sheet/approval state for the Sheets page."""
    cpath = _census_path(pid)
    census = []
    if cpath.is_file():
        census = json.loads(cpath.read_text(encoding="utf-8"))
        if isinstance(census, dict):
            census = census.get("entities", [])
    # newest human verdict per entity name wins; an approval may carry an
    # explicit sheet_path — that binding beats fuzzy filename matching forever
    verdicts: dict[str, dict] = {}
    bound: dict[str, str] = {}
    for e in _events(pid):
        if e.get("type") in ("sheet_approved", "sheet_rejected"):
            p = e.get("payload") or {}
            name = p.get("entity")
            if name:
                verdicts[name] = e
                if p.get("sheet_path"):
                    bound[name] = p["sheet_path"]
    sheet_dir = PROJECTS_DIR / pid / "assets" / "asset_bible"
    ref_dir = PROJECTS_DIR / pid / "assets" / "_reference"
    # Explicit binding sidecar (beats fuzzy filename matching, which mis-assigns
    # when entities share a token — every "Toops X" grabbed the same sheet).
    #   artifacts/sheet_map.json: {"<entity name>": {"sheet": "loc_x.png",
    #     "ref_dirs": ["reservoir", ...], "role": "sheet"|"prop"|"covered"}}
    # role prop/covered => the entity is a document/diagram prop or is covered
    # by another sheet, so it needs NO sheet and drops out of the gate.
    smap_path = PROJECTS_DIR / pid / "artifacts" / "sheet_map.json"
    sheet_map = {}
    if smap_path.is_file():
        try:
            sheet_map = json.loads(smap_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sheet_map = {}
    out = []
    def _norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())

    def _tokens(name: str) -> list[str]:
        """Match needles: full normalized name + 5-char prefixes of each word
        ("Katherine Yarbrough" -> katherineyarbrough, kathe, yarbr — so the
        subj_katie sheet still matches by prefix)."""
        base = name.split("(")[0]
        toks = [_norm(base)]
        for w in base.replace("-", " ").split():
            n = _norm(w)
            if len(n) >= 4:
                toks.append(n[:5])
        return [t for t in toks if t]

    def _matches(cand_norm: str, needles: list[str]) -> bool:
        return any(n in cand_norm for n in needles)

    for ent in census:
        ent = dict(ent)
        name = ent.get("name") or "?"
        needles = _tokens(name)
        mapping = sheet_map.get(name) or {}
        role = mapping.get("role", "sheet")
        if role in ("prop", "covered"):
            ent["needs_sheet"] = False
            ent["sheet_role"] = role
        # sheets: prefer explicit *reference_sheet* files over locale canonicals
        cands = [c for c in sorted(sheet_dir.glob("*.png"))
                 if _matches(_norm(c.stem), needles)]
        cands.sort(key=lambda c: (0 if "reference_sheet" in c.stem.lower()
                                  else 1 if c.stem.lower().startswith("subj_")
                                  else 2, len(c.stem)))
        def _rel(p) -> str | None:
            """Project-relative forward-slash path for the /media endpoint."""
            if not p:
                return None
            try:
                return Path(p).resolve().relative_to(
                    (PROJECTS_DIR / pid).resolve()).as_posix()
            except ValueError:
                # normalize the fallback too — a Windows-style or repo-relative
                # path 404s against /media; strip up to the project dir if present
                s = str(p).replace("\\", "/")
                marker = f"projects/{pid}/"
                return s.split(marker, 1)[1] if marker in s else s

        # sheet: operator binding > sidecar mapping > fuzzy match
        mapped_sheet = None
        if mapping.get("sheet"):
            cand = sheet_dir / mapping["sheet"]
            if cand.is_file():
                mapped_sheet = str(cand)
        sheet = _rel(bound.get(name) or mapped_sheet
                     or (str(cands[0]) if cands else None))
        # reference photos: sidecar ref_dirs > fuzzy token match
        refs = []
        map_dirs = mapping.get("ref_dirs") or []
        if map_dirs:
            for rd in map_dirs:
                sub_dir = ref_dir / rd
                if sub_dir.is_dir():
                    for sub in sorted(sub_dir.glob("*")):
                        if sub.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                            refs.append(_rel(str(sub)))
                        if len(refs) >= 8:
                            break
        elif ref_dir.is_dir():
            for sub in ref_dir.rglob("*"):
                if sub.is_file() and sub.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
                        and _matches(_norm(str(sub)), needles):
                    refs.append(_rel(str(sub)))
                if len(refs) >= 8:
                    break
        v = verdicts.get(name)
        out.append({
            **ent,
            "sheet_path": sheet,
            "reference_photos": refs,
            "verdict": (v.get("type") if v else None),
            "verdict_ts": (v.get("ts") if v else None),
            "verdict_hint": ((v.get("payload") or {}).get("hint") if v else None),
        })
    needs = [e for e in out if e.get("needs_sheet")]
    return {
        "project_id": pid,
        "entities": out,
        "sheets_gate_open": bool(needs) and all(
            e.get("verdict") == "sheet_approved" for e in needs),
        "census_missing": not cpath.is_file(),
    }


def run_census(pid: str) -> dict:
    from lib.entity_census import run_census as _run
    _run(pid)
    return list_entities(pid)


# ---------------------------------------------------------------------------
# stills gate + per-still notes
# ---------------------------------------------------------------------------

def stills_state(pid: str, sid: str) -> dict:
    """Latest stills-gate state + per-still notes for a scene."""
    approved_ts, notes = None, []
    for e in _events(pid):
        if e.get("scene_id") != sid:
            continue
        t = e.get("type")
        if t == "stills_approved":
            approved_ts = e.get("ts")
        elif t == "stills_unapproved":
            approved_ts = None
        elif t == "still_note":
            p = e.get("payload") or {}
            notes.append({"idx": p.get("idx"), "note": p.get("note"),
                          "ts": e.get("ts")})
    return {"scene_id": sid, "stills_approved": approved_ts is not None,
            "approved_ts": approved_ts, "notes": notes}


def stills_gate_open(pid: str, sid: str) -> bool:
    return stills_state(pid, sid)["stills_approved"]


def check_still_hygiene(path: str | Path) -> dict:
    """Border intake check for ONE authored still (the border saga started in
    a single keyframe). Advisory: {clean: bool, box: [..] | None}."""
    try:
        from PIL import Image
        from lib.frame_hygiene import find_border_box
        box = find_border_box(Image.open(path).convert("RGB"))
        return {"clean": box is None, "box": list(box) if box else None}
    except Exception as exc:  # noqa: BLE001
        return {"clean": True, "box": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# animatics
# ---------------------------------------------------------------------------

def build_scene_animatic(pid: str, sid: str,
                         stills: list[str] | None = None) -> dict:
    from lib.animatic import scene_animatic
    out = scene_animatic(pid, sid, stills=stills)
    fb.append_event(pid, actor="system", type="animatic_built", scene_id=sid,
                    payload={"path": str(out), "stills": len(stills or [])})
    rel = Path(out).resolve().relative_to(Path("projects").resolve() / pid)
    return {"scene_id": sid, "path": str(out), "media": str(rel)}


def build_episode_animatic(pid: str) -> dict:
    from lib.animatic import episode_animatic
    out = episode_animatic(pid)
    fb.append_event(pid, actor="system", type="animatic_built",
                    payload={"path": str(out), "episode": True})
    rel = Path(out).resolve().relative_to(Path("projects").resolve() / pid)
    return {"project_id": pid, "path": str(out), "media": str(rel)}


def scene_animatic_media(pid: str, sid: str) -> str | None:
    p = PROJECTS_DIR / pid / "assets" / "animatics" / f"{sid}.mp4"
    return f"assets/animatics/{sid}.mp4" if p.is_file() else None


# ---------------------------------------------------------------------------
# omni edit — surgical revision of a take ($0.10/s, explicit only)
# ---------------------------------------------------------------------------

def omni_edit_take(pid: str, sid: str, take: int, hint: str) -> dict:
    """Revise ONE take with Gemini Omni Flash ('change X, keep everything
    else') and register the result as a NEW take in the normal review flow.

    Synchronous (~1-2 min for short clips). Only for takes <=10.5s (the model
    outputs <=10s). Cost = clip seconds x $0.10, ledger-logged by the adapter.
    Timing drift is possible — the new take goes through the usual verdict/QC.
    """
    import subprocess
    if not hint.strip():
        raise ValueError("an edit note is required")
    proj = PROJECTS_DIR / pid
    idx_path = proj / "artifacts" / "ai_segments_takes.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    entry = next((t for t in idx.get(sid, []) if int(t.get("take", -1)) == take), None)
    if entry is None:
        raise KeyError(f"take {take} not found for {sid}")
    src = Path(entry["path"])
    if not src.is_file():
        raise FileNotFoundError(f"take file missing: {src}")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True)
    dur = float(r.stdout.strip() or 0)
    if dur > 10.5:
        raise ValueError(f"take is {dur:.1f}s — omni edits clips of 10s or less")

    from tools.video.omni_flash_video import OmniFlashVideo
    out = proj / "assets" / "ai_segments" / f"{sid}__omni_edit_t{take}_{int(time.time())}.mp4"
    prompt = (f"Keep this exact clip - same shot, same composition, same timing, "
              f"same sound - but change ONE thing only: {hint.strip()} "
              f"Everything else identical. {dur:.0f} seconds.")
    res = OmniFlashVideo().execute({"prompt": prompt, "video_path": str(src),
                                    "output_path": str(out)})
    if not res.success:
        raise RuntimeError(res.error)
    # register through the normal take flow so verdict/promotion apply
    from web.backend import takes as takes_mod
    rel = out.as_posix()
    try:
        rel = out.resolve().relative_to(Path(".").resolve()).as_posix()
    except ValueError:
        pass
    reg = takes_mod.assign_clip(pid, sid, rel,
                                f"omni edit of take {take}: {hint.strip()[:80]}")
    return {"ok": True, "scene_id": sid, "source_take": take,
            "new_take": reg.get("take"), "cost_usd": res.cost_usd,
            "seconds": (res.data or {}).get("seconds"),
            "interaction_id": (res.data or {}).get("interaction_id")}


# ---------------------------------------------------------------------------
# human-keyed take promotion
# ---------------------------------------------------------------------------

def promote_take(pid: str, sid: str, take: int) -> dict:
    """Copy ONE explicitly chosen take to the canonical assets/ai_segments/
    {sid}.mp4 that build_v6 conforms from, with a dated backup.

    HUMAN-keyed by design: the takes index's "accepted" flag is the auto-gate's
    verdict — promoting latest-accepted shipped wrong takes (019 t10 vs the
    approved t12). This endpoint promotes exactly the take the operator names,
    and logs it, so the board can always show which take is canonical.
    """
    import shutil
    import datetime as _dt
    proj = PROJECTS_DIR / pid
    idx_path = proj / "artifacts" / "ai_segments_takes.json"
    if not idx_path.is_file():
        raise FileNotFoundError("takes index missing")
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    entry = next((t for t in idx.get(sid, []) if int(t.get("take", -1)) == take), None)
    if entry is None:
        raise KeyError(f"take {take} not found for {sid}")
    src = Path(entry["path"])
    if not src.is_absolute():
        src = Path(".") / src
    if not src.is_file():
        raise FileNotFoundError(f"take file missing: {src}")
    dst = proj / "assets" / "ai_segments" / f"{sid}.mp4"
    bak_dir = proj / "assets" / "ai_segments" / \
        f"_canonical_backup_{_dt.date.today():%Y%m%d}"
    bak_dir.mkdir(exist_ok=True)
    if dst.is_file() and not (bak_dir / f"{sid}.mp4").exists():
        shutil.copy2(dst, bak_dir / f"{sid}.mp4")
    shutil.copy2(src, dst)
    fb.append_event(pid, actor="human", type="take_promoted", scene_id=sid,
                    payload={"take": take, "path": entry["path"]})
    return {"ok": True, "scene_id": sid, "take": take,
            "canonical": str(dst), "backup_dir": str(bak_dir)}


def reuse_candidates(pid: str, sid: str, limit: int = 12) -> dict:
    """Approved library assets matching this scene's narration — surface
    already-paid pixels BEFORE the operator generates new stills
    (reuse-before-generate as a UI affordance). Advisory: reuse AMPLIFIES a
    wrong identity seed, so the UI reminds the operator to verify against the
    entity sheet."""
    try:
        from lib.asset_library import _load, _projects_dir
        records = _load(pid, _projects_dir())
    except Exception:
        return {"scene_id": sid, "candidates": [], "note": "library index missing"}
    data = scenes_mod.load_scenes(pid)
    scene = next((s for s in data["scenes"] if s["id"] == sid), None)
    words = {w.strip(".,!?;:'\"").lower()
             for w in (scene.get("narration", "") if scene else "").split()
             if len(w) > 4}
    scored = []
    for r in records:
        hay = " ".join(str(r.get(k, "")) for k in
                       ("label", "prompt", "scene", "asset_id", "path")).lower()
        score = sum(1 for w in words if w in hay)
        if r.get("scene") == sid:
            score += 3
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    cands = []
    for score, r in scored[:limit]:
        p = r.get("path")
        rel = None
        if p:
            try:
                rel = Path(p).resolve().relative_to(
                    (PROJECTS_DIR / pid).resolve()).as_posix()
            except (ValueError, OSError):
                rel = str(p).replace("\\", "/")
        cands.append({**{k: r.get(k) for k in
                         ("kind", "scene", "label", "asset_id")},
                      "media": rel, "score": score})
    return {"scene_id": sid, "candidates": cands,
            "note": "verify any reuse against the entity sheet first — "
                    "reuse amplifies a wrong identity seed"}


def promoted_takes(pid: str) -> dict:
    """scene_id -> take number of the newest promotion event."""
    out: dict[str, int] = {}
    for e in _events(pid):
        if e.get("type") == "take_promoted" and e.get("scene_id"):
            t = (e.get("payload") or {}).get("take")
            if t is not None:
                out[e["scene_id"]] = int(t)
    return out


# ---------------------------------------------------------------------------
# the stage board
# ---------------------------------------------------------------------------

def stage_board(pid: str) -> dict:
    """Per-scene stage strip: narration / stills / video, plus the
    project-wide sheets gate."""
    data = scenes_mod.load_scenes(pid)
    sheets = list_entities(pid)
    audio_dir = PROJECTS_DIR / pid / "assets" / "audio_v6"
    # newest human verdict per scene (video stage); a scene "enters the stills
    # flow" only via EXPLICIT events (a real stills animatic, a note, a gate
    # action) — a placeholder animatic file on disk is NOT a signal (the
    # episode animatic builds placeholder cards for every scene)
    video_verdicts: dict[str, str] = {}
    stills_ok: dict[str, bool] = {}
    in_flow: set[str] = set()
    for e in _events(pid):
        sid = e.get("scene_id")
        if not sid:
            continue
        t = e.get("type")
        if t == "human_verdict":
            video_verdicts[sid] = (e.get("payload") or {}).get("verdict") or ""
        elif t == "stills_approved":
            stills_ok[sid] = True
            in_flow.add(sid)
        elif t == "stills_unapproved":
            stills_ok[sid] = False
            in_flow.add(sid)
        elif t == "still_note":
            in_flow.add(sid)
        elif t == "animatic_built" and (e.get("payload") or {}).get("stills"):
            in_flow.add(sid)
    promoted = promoted_takes(pid)
    rows = []
    for s in data["scenes"]:
        sid = s["id"]
        rows.append({
            "scene_id": sid,
            "narration_ready": (audio_dir / f"{sid}.mp3").is_file(),
            # tri-state: True approved / False explicitly reopened / None never
            # entered the stills flow (legacy scenes pass the dispatch gate)
            "stills_approved": stills_ok.get(sid),
            "stills_flow": sid in in_flow,
            "animatic": scene_animatic_media(pid, sid),
            "video_verdict": video_verdicts.get(sid),
            "promoted_take": promoted.get(sid),
        })
    return {
        "project_id": pid,
        "sheets_gate_open": sheets["sheets_gate_open"],
        "census_missing": sheets["census_missing"],
        "scenes": rows,
        "generated_ts": time.time(),
    }
