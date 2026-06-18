"""Read-only scene model for the scene-review dashboard.

A "scene" is a scored-script SEGMENT (one narration beat). This module reads the
existing project artifacts — duration map (timing + narration), ai_visual_assets
(seg -> assembled clip), shot_manifest (the sub-shot legs for drill-down),
narration_alignment_report (the AUTO gate verdict/score), and the scored script
(narration_mode / lane) — and joins them into one displayable record per scene.

Nothing here mutates project state; human feedback lives in web.backend.feedback.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO_ROOT / "projects"


def _read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def media_url(project_id: str, rel: str) -> str:
    return f"/api/projects/{project_id}/media/{rel}"


def _norm_rel(path_str: str | None, project_dir: Path) -> str | None:
    """Normalize a stored media path (backslashes, repo-relative or absolute)
    into a path relative to the project dir, or None if it escapes it."""
    if not path_str:
        return None
    s = str(path_str).replace("\\", "/")
    p = Path(s)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        return str(p.resolve().relative_to(project_dir.resolve())).replace("\\", "/")
    except Exception:
        return None


def _load_scored(project_dir: Path) -> tuple[str, dict[str, dict]]:
    """Return (title, {seg_id: {type, narration_mode, flf}}) from the scored script.

    Uses lib.scored_script when importable; degrades to {} so the dashboard still
    renders timing/clips from the duration map alone if the script can't be parsed.
    """
    matches = sorted(project_dir.glob("**/scored_script.yaml"))
    if not matches:
        return (project_dir.name, {})
    try:
        from lib.scored_script import load_scored_script

        script = load_scored_script(matches[0])
        by_id = {
            seg.id: {
                "type": seg.visual.type,
                "narration_mode": seg.narration_mode,
                "flf": seg.visual.flf is not None,
            }
            for seg in script.segments
        }
        return (script.title or project_dir.name, by_id)
    except Exception:
        return (project_dir.name, {})


def list_projects() -> list[dict]:
    out: list[dict] = []
    if not PROJECTS_DIR.is_dir():
        return out
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        dm = _read_json(d / "artifacts" / "duration_map_v6.json", {})
        title, _ = _load_scored(d)
        out.append({
            "id": d.name,
            "title": title,
            "scene_count": dm.get("segment_count") if isinstance(dm, dict) else None,
        })
    return out


def load_scenes(project_id: str) -> dict:
    project_dir = PROJECTS_DIR / project_id
    art = project_dir / "artifacts"

    dm = _read_json(art / "duration_map_v6.json", {}) or {}
    # ai_visual_assets_v6.json holds the current/approved AI-segment clips and is the
    # primary source. visual_assets_v6.json (the older conformed map) is ONLY a fallback
    # for scenes it doesn't cover (manim / text-card), so those still get a clip to show.
    assets = _read_json(art / "ai_visual_assets_v6.json", {}) or {}
    final_assets = _read_json(art / "visual_assets_v6.json", {}) or {}
    # Regenerated takes live alongside the baseline (written by web.backend.takes);
    # an accepted take is preferred over the original without rewriting the manifest.
    takes_idx = _read_json(art / "ai_segments_takes.json", {}) or {}
    align = _read_json(art / "narration_alignment_report.json", []) or []
    sm = _read_json(art / "shot_manifest_v6.json", {}) or {}
    title, scored = _load_scored(project_dir)

    align_by = {a.get("segment_id"): a for a in align if isinstance(a, dict)}
    shots_by_seg: dict[str, list] = {}
    for sh in (sm.get("shots") or []):
        shots_by_seg.setdefault(sh.get("segment_id"), []).append(sh)

    # Lazy import to avoid an import cycle (feedback imports PROJECTS_DIR).
    from web.backend import feedback as fb
    fb_state = fb.scene_states(project_id)

    scenes: list[dict] = []
    for i, seg in enumerate(dm.get("segments", []), start=1):
        sid = seg.get("id")
        seg_takes = takes_idx.get(sid, [])
        accepted = [t for t in seg_takes if t.get("verdict") == "accepted"]
        latest_accepted = max(accepted, key=lambda t: t.get("take", 0)) if accepted else None
        # The good AI-segment clips (ai_visual_assets) win; visual_assets_v6 is ONLY a
        # fallback for scenes absent from it (manim / text-card), so AI scenes keep the
        # exact clips that were reviewed and manim scenes still get a clip to show.
        baseline = assets.get(sid) or final_assets.get(sid)
        preferred = latest_accepted["path"] if latest_accepted else baseline
        clip_rel = _norm_rel(preferred, project_dir)
        active_take = latest_accepted["take"] if latest_accepted else 0  # 0 = baseline
        # Visuals are silent (TTS-first): the narration lives in a separate mp3
        # and is only muxed at final render. Surface it so the UI can sync it.
        audio_rel = _norm_rel(seg.get("audio_path"), project_dir)
        sc = scored.get(sid, {})
        a = align_by.get(sid, {})
        shots = shots_by_seg.get(sid, [])
        mode = sc.get("narration_mode")
        scenes.append({
            "number": i,
            "id": sid,
            "narration": seg.get("narration", ""),
            "slot_s": round(seg.get("total_duration_s") or seg.get("audio_duration_s") or 0, 2),
            "timeline_start_s": round(seg.get("timeline_start_s") or 0, 2),
            "lane": seg.get("visual_type") or sc.get("type"),
            "flf": sc.get("flf", False),
            # Depiction is the default standard; unset narration_mode -> "literal".
            "narration_mode": mode or "literal",
            "narration_mode_default": mode is None,
            "clip_url": media_url(project_id, clip_rel) if clip_rel else None,
            "audio_url": media_url(project_id, audio_rel) if audio_rel else None,
            "active_take": active_take,
            "take_count": len(seg_takes),
            "takes": [
                {
                    "take": t.get("take"),
                    "verdict": t.get("verdict"),
                    "score": t.get("score"),
                    "cost_usd": t.get("cost_usd"),
                    "revision_id": t.get("revision_id"),
                    "ts": t.get("ts"),
                    "url": (media_url(project_id, _norm_rel(t.get("path"), project_dir))
                            if _norm_rel(t.get("path"), project_dir) else None),
                }
                for t in seg_takes
            ],
            "shots": [
                {
                    "shot_id": s.get("shot_id"),
                    "provider": s.get("provider"),
                    "prompt": (s.get("video_prompt") or "")[:400],
                }
                for s in shots
            ],
            "auto_gate": {
                "verdict": a.get("verdict"),
                "score": a.get("score"),
                "missing": a.get("missing_elements") or [],
                "suggested_prompt": a.get("suggested_prompt") or "",
            } if a else None,
            "feedback": fb_state.get(sid) or fb.empty_state(),
        })

    return {"project_id": project_id, "title": title, "scenes": scenes}


def cost_summary(project_id: str) -> dict:
    """Totals from the append-only cost ledger (cost_ledger.jsonl)."""
    p = PROJECTS_DIR / project_id / "artifacts" / "cost_ledger.jsonl"
    total = 0.0
    by_provider: dict[str, float] = {}
    n = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            c = float(rec.get("cost_usd") or 0)
            total += c
            by_provider[rec.get("provider", "?")] = round(
                by_provider.get(rec.get("provider", "?"), 0.0) + c, 4
            )
            n += 1
    return {"total_usd": round(total, 2), "calls": n, "by_provider": by_provider}
