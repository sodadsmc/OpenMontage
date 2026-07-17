"""Post-render machine QC — runs the polish-pass eyeball checks mechanically.

Every polish round on the therac-25 episode surfaced the same defect classes,
each found by a human watch-through: cream panel borders on some scenes,
freeze tails where a take ran out before its slot, 1-2 frame stray shots
flashing at splice seams, and dead-air silences at scene changes. All of them
are machine-detectable. This stage runs them against the rendered master using
the assembly manifest's timeline, so future runs catch these BEFORE the
operator's first watch-through.

Checks (all advisory unless --strict):
  borders   cream comic borders (skip windows listed in the keep-list sidecar)
  freeze    static tails inside ai_video slots while the timeline continues
  flash     A-X-A stray-shot fragments (1-3 frames) anywhere in the render
  silence   audio gaps exceeding the scripted silence for that boundary
  duration  render duration vs manifest total

Keep-list sidecar (intentional borders, e.g. framed portrait shots) — windows
are SCENE-RELATIVE so they survive timeline re-timing:
  projects/{pid}/assets/ai_segments/_gold_refs/qc_keep_borders.json
  [{"scene": "seg_022", "start_local": 13.5, "end_local": 24.5,
    "why": "Katie window portrait"}]

CLI:
  python -m lib.render_qc <project_id> [--render PATH] [--strict] [--json OUT]
  python -m lib.render_qc --clip some_take.mp4 [--slot 22.1]   (pre-promotion)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from lib.frame_hygiene import (
    double_cuts, flash_frames, freeze_tail, motion_floor, probe_duration, seam_jumps,
    static_runs, video_border_hits,
)

# scenes whose visuals are deliberately static holds don't get freeze findings
_STATIC_OK_TYPES = {"manim_animation"}
_MAX_UNSCRIPTED_GAP_S = 2.4      # silence beyond scripted gets flagged past this
_SILENCE_NOISE = "-32dB"
_SILENCE_MIN_S = 0.7


def _find_manifest(proj: Path) -> Path:
    """Highest-versioned manifest that actually has the v2 timeline shape
    (older v3-v5 manifests lack total_duration_s / _timeline_end; mtime order
    is unreliable on this filesystem)."""
    def version(p: Path) -> int:
        m = re.search(r"_v(\d+)", p.stem)
        return int(m.group(1)) if m else -1
    for p in sorted(proj.glob("artifacts/assembly_manifest_v*.json"),
                    key=version, reverse=True):
        try:
            m = json.loads(p.read_text())
            am = m.get("assembly_manifest") or {}
            am = dict(am) if not isinstance(am, dict) else am
            if "total_duration_s" in am and am.get("cuts") and \
                    "_timeline_end" in am["cuts"][0]:
                return p
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    raise SystemExit(f"no usable assembly manifest under {proj / 'artifacts'}")


def _find_render(proj: Path) -> Path:
    cands = sorted(proj.glob("renders/*.mp4"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit(f"no render under {proj / 'renders'}")
    return cands[0]


def _load_keep_windows(proj: Path, cuts: list) -> list:
    """Resolve keep-list entries to absolute render times via the manifest."""
    sidecar = proj / "assets" / "ai_segments" / "_gold_refs" / "qc_keep_borders.json"
    if not sidecar.is_file():
        return []
    starts = {c["_scene_id"]: c["_timeline_start"] for c in cuts}
    out = []
    for w in json.loads(sidecar.read_text()):
        if "scene" in w:
            base = starts.get(w["scene"])
            if base is None:
                continue
            out.append((base + w["start_local"], base + w["end_local"]))
        else:                              # legacy absolute form
            out.append((w["start"], w["end"]))
    return out


def _has_audio(render: Path) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(render)],
        capture_output=True, text=True)
    return "audio" in r.stdout


def _silences(render: Path) -> list:
    """[(start, end, dur)] from ffmpeg silencedetect over the full master."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(render), "-af",
         f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_S}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    text = r.stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", text)]
    ends = re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", text)
    out = []
    for i, (e, d) in enumerate(ends):
        s = starts[i] if i < len(starts) else float(e) - float(d)
        out.append((round(s, 2), round(float(e), 2), round(float(d), 2)))
    return out


def qc_render(project_id: str, render: str | None = None,
              root: str | Path = ".") -> dict:
    proj = Path(root) / "projects" / project_id
    manifest_path = _find_manifest(proj)
    m = json.loads(manifest_path.read_text())
    am = dict(m["assembly_manifest"]) if not isinstance(
        m.get("assembly_manifest"), dict) else m["assembly_manifest"]
    cuts = am["cuts"]
    render_path = Path(render) if render else _find_render(proj)
    keep = _load_keep_windows(proj, cuts)
    findings = []

    def kept(t):
        return any(a - 0.2 <= t <= b + 0.2 for a, b in keep)

    print(f"render:   {render_path}")
    print(f"manifest: {manifest_path.name} ({len(cuts)} cuts, "
          f"{am['total_duration_s']:.1f}s)")

    # -- duration -----------------------------------------------------------
    rdur = probe_duration(render_path)
    want = float(am["total_duration_s"])
    if rdur + 0.05 < want:
        findings.append({"check": "duration", "at": 0.0,
                         "detail": f"render {rdur:.2f}s < manifest {want:.2f}s"})
    print(f"[duration] render {rdur:.2f}s vs manifest {want:.2f}s")

    # -- borders (3 samples per scene) + freeze tails ------------------------
    for c in cuts:
        sid = c["_scene_id"]
        t0, t1 = c["_timeline_start"], c["_timeline_end"]
        hits = [h for h in video_border_hits(render_path, t0, t1 - t0)
                if not kept(h[0])]
        for t, box in hits:
            findings.append({"check": "border", "scene": sid, "at": t,
                             "detail": f"cream border {box}"})
        if c.get("_visual_type") not in _STATIC_OK_TYPES:
            fz = freeze_tail(render_path, slot_end=t1,
                             window_s=min(8.0, t1 - t0))
            # a static LAST slot ending into credits/black is fine
            if fz and t1 < want - 1.0:
                findings.append({"check": "freeze", "scene": sid, "at": fz[0],
                                 "detail": f"static for {fz[1]}s before slot end"})
            # advisory: long mid-scene holds (card holds show up here too)
            for ht, hd in static_runs(render_path, t0, t1 - t0):
                if fz and abs(ht - fz[0]) < 0.5:
                    continue                      # already reported as freeze
                findings.append({"check": "hold", "scene": sid, "at": ht,
                                 "detail": f"static hold {hd}s (advisory)"})
        print(f"  {sid}: {'BORDER ' + str(len(hits)) if hits else 'ok'}",
              flush=True)

    # -- flash frames + double-cuts (full-render single passes) --------------
    print("[flash] scanning full render at 30fps (low-res)...")
    for t, n in flash_frames(render_path):
        # cuts between scenes are legit; a flash INSIDE a scene body is not.
        near_cut = any(abs(t - c["_timeline_start"]) < 0.25 for c in cuts)
        if not near_cut:
            findings.append({"check": "flash", "at": t,
                             "detail": f"{n}-frame stray shot (A-X-A)"})
    for t, gap in double_cuts(render_path):
        near_cut = any(abs(t - c["_timeline_start"]) < 0.25 or
                       abs(t + gap / 30.0 - c["_timeline_start"]) < 0.25
                       for c in cuts)
        if not near_cut:
            findings.append({"check": "double-cut", "at": t,
                             "detail": f"two cuts {gap} frames apart (advisory; "
                                       "montage cuts are legit)"})
    print("[seam] scanning for chain-seam / skip-jump cuts...")
    for t, c_ in seam_jumps(render_path):
        near_cut = any(abs(t - c["_timeline_start"]) < 0.25 for c in cuts)
        if not near_cut:
            findings.append({"check": "seam-jump", "at": t,
                             "detail": f"cut between two renderings of the same "
                                       f"staging (corr {c_}) - chain seam or "
                                       "mis-anchored splice"})

    # -- silences -------------------------------------------------------------
    print("[silence] scanning master audio...")
    if not _has_audio(render_path):
        findings.append({"check": "silence", "at": 0.0,
                         "detail": "render has NO audio stream"})
    for s, e, d in _silences(render_path):
        # exempt only a SHORT tail-out; a long dead tail means the audio died
        if e >= want - 0.5 and (want - s) <= 4.0:
            continue
        # allowance = scripted silence at the nearest boundary + narration tail
        allowance = _MAX_UNSCRIPTED_GAP_S
        for c in cuts:
            if abs(c["_timeline_end"] - e) < 2.5 or \
               (c["_timeline_end"] >= s and c["_timeline_end"] <= e + 0.5):
                allowance = max(allowance,
                                float(c.get("_silence_after") or 0) + 1.6)
        if d > allowance:
            findings.append({"check": "silence", "at": s,
                             "detail": f"{d:.2f}s gap (allowance {allowance:.1f}s)"})

    report = {"render": str(render_path), "manifest": str(manifest_path),
              "duration_s": rdur, "findings": findings}
    print(f"\n{'!' if findings else 'OK'} {len(findings)} finding(s)")
    for f in findings:
        mins, secs = divmod(f["at"], 60)
        print(f"  [{f['check']}] {int(mins)}:{secs:05.2f} "
              f"{f.get('scene', '')} - {f['detail']}")
    return report


def qc_clip(clip: str, slot: float | None = None) -> dict:
    """Pre-promotion hygiene on a single take/assembly (no manifest needed)."""
    dur = probe_duration(clip)
    findings = []
    for t, box in video_border_hits(clip):
        findings.append({"check": "border", "at": t, "detail": f"cream border {box}"})
    fz = freeze_tail(clip, slot_end=slot or dur)
    if fz:
        findings.append({"check": "freeze", "at": fz[0],
                         "detail": f"static for {fz[1]}s at tail"})
    frame_mean, span_delta, reads_dead = motion_floor(clip)
    if reads_dead:
        findings.append({"check": "dead-still", "at": 0.0,
                         "detail": f"frame Δ {frame_mean:.2f}, span Δ {span_delta:.2f} - "
                                   "essentially a STILL; upgrade if this is a footage/"
                                   "establishing/hero beat, OK only for a card/diagram/"
                                   "quiet insert"})
    for t, n in flash_frames(clip):
        findings.append({"check": "flash", "at": t,
                         "detail": f"{n}-frame stray shot (A-X-A)"})
    for t, gap in double_cuts(clip):
        findings.append({"check": "double-cut", "at": t,
                         "detail": f"two cuts {gap} frames apart (advisory)"})
    for t, c_ in seam_jumps(clip):
        findings.append({"check": "seam-jump", "at": t,
                         "detail": f"same-staging cut (corr {c_}) - chain seam "
                                   "or mis-anchored splice"})
    if slot and dur < slot - 0.05:
        findings.append({"check": "duration", "at": dur,
                         "detail": f"short of {slot:.2f}s slot by {slot - dur:.2f}s"})
    print(f"{clip}: {len(findings)} finding(s)")
    for f in findings:
        print(f"  [{f['check']}] @{f['at']} - {f['detail']}")
    return {"clip": clip, "duration_s": dur, "findings": findings}


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Post-render machine QC")
    ap.add_argument("project_id", nargs="?")
    ap.add_argument("--render", default=None)
    ap.add_argument("--clip", default=None)
    ap.add_argument("--slot", type=float, default=None)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when findings exist")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    if args.clip:
        report = qc_clip(args.clip, slot=args.slot)
    elif args.project_id:
        report = qc_render(args.project_id, render=args.render)
    else:
        ap.error("give a project_id or --clip")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1))
    if args.strict and report["findings"]:
        sys.exit(1)


if __name__ == "__main__":
    _main()
