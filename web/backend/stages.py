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
        name = ent.get("name") or "?"
        needles = _tokens(name)
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

        sheet = _rel(bound.get(name) or (str(cands[0]) if cands else None))
        refs = []
        if ref_dir.is_dir():
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
        })
    return {
        "project_id": pid,
        "sheets_gate_open": sheets["sheets_gate_open"],
        "census_missing": sheets["census_missing"],
        "scenes": rows,
        "generated_ts": time.time(),
    }
