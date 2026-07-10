"""Build a preview render of the episode's opening segments (default: ~first 3 min).

A scoped dry-run of the full pipeline before the bulk spend: generates only the
target segments' pending shots (anchor-refreshed, chain-aware, fully gated),
renders the in-range sketch diagram, conforms everything to the narration's
exact slots, burns overlays, assembles with the real narration audio, applies
the channel finishing grade, and writes renders/preview_<n>min.mp4.

Usage:
  python projects/therac-25-test/script_v5/build_preview.py            # plan + cost, no spend
  python projects/therac-25-test/script_v5/build_preview.py --yes      # generate + assemble
  python projects/therac-25-test/script_v5/build_preview.py --yes --until seg_009
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.base_tool  # noqa: F401 — loads .env

PROJECT = Path("projects/therac-25-test")
ARTIFACTS = PROJECT / "artifacts"
AUDIO_DIR = PROJECT / "assets" / "audio_v6"
SEGMENTS_DIR = PROJECT / "assets" / "ai_segments"
FLF_DIR = PROJECT / "assets" / "flf_segments"   # the FLF lane's per-segment clip cache
PREVIEW_DIR = PROJECT / "assets" / "_preview"
RENDERS = PROJECT / "renders"
MANIFEST = ARTIFACTS / "shot_manifest_v6.json"
BIBLE = ARTIFACTS / "asset_bible_v6.json"
SCRIPT = PROJECT / "script_v5" / "scored_script.yaml"

TARGET_FPS = int(os.environ.get("RENDER_FPS", "30"))
SCALE_VF = (f"scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:-1:-1:color=black,fps={TARGET_FPS},setsar=1")
_COST_PER_SECOND = 0.017


def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    subprocess.run(["ffmpeg", "-y", *args], capture_output=True, timeout=timeout, check=True)


def _refresh_anchor(job, script_seg, bible) -> str | None:
    """Fresh, ingestible anchor for a pending job (stored URLs go stale)."""
    from lib.image_host import upload_image
    kf = job.keyframe or ""
    if kf and not kf.startswith(("http://", "https://")) and Path(kf).exists():
        return upload_image(kf)
    # Local per-shot keyframes from Phase A, then the bible canonical.
    for cand in (PROJECT / "assets" / "keyframes" / f"{job.segment_id}_{job.shot_id}_key_pop.png",
                 PROJECT / "assets" / "keyframes" / f"{job.segment_id}_{job.shot_id}_key.png"):
        if cand.exists():
            return upload_image(cand)
    asset = bible.asset_for_segment(script_seg) if script_seg is not None else None
    local = getattr(asset, "canonical_reference_image", "") if asset else ""
    if local and Path(local).exists():
        return upload_image(local)
    return kf or None


def main() -> None:
    ap = argparse.ArgumentParser(description="Preview render of the opening segments")
    ap.add_argument("--until", default="seg_009", help="last segment id to include")
    ap.add_argument("--from", dest="from_seg", default=None,
                    help="first segment id to include (default: the start) — e.g. seg_010 for the tail")
    ap.add_argument("--out-name", default="preview_3min",
                    help="output basename under renders/ (e.g. tail_seg010_037)")
    ap.add_argument("--yes", action="store_true", help="actually spend on generation")
    ap.add_argument("--max-attempts", type=int, default=3)
    args = ap.parse_args()

    os.environ.setdefault("IMAGE_HOST", "tmpfiles")  # proven Grok-ingestible

    from lib.scored_script import load_scored_script
    from lib.asset_bible import AssetBible
    from lib.duration_map import build_duration_map_from_paths
    from lib.shot_manifest import ShotManifest
    from lib.quality_gate import GenerationHardStop
    from lib import visual_router as vr

    script = load_scored_script(SCRIPT)
    bible = AssetBible.load(BIBLE)
    manifest = ShotManifest.load(MANIFEST)
    audio = {x.id: str(AUDIO_DIR / f"seg_{x.index:03d}.mp3") for x in script.segments
             if (AUDIO_DIR / f"seg_{x.index:03d}.mp3").exists()}
    dm = build_duration_map_from_paths(script, audio)

    segs = []
    started = args.from_seg is None
    for seg in script.segments:
        if seg.id == args.from_seg:
            started = True
        if started:
            segs.append(seg)
        if seg.id == args.until:
            break
    seg_by_id = {s.id: s for s in segs}

    pending = [j for s in segs for j in manifest.shots_for_segment(s.id) if j.status != "done"]
    est_secs = sum(j.duration_s for j in pending)
    # FLF beats (explanatory) are off the i2v manifest — generated via the controllable lane.
    from lib.flf import flf_beats_in_scope, generate_flf_segments
    flf_segs = [s for s in flf_beats_in_scope(script, {s.id for s in segs})
                if not (FLF_DIR / f"{s.id}.mp4").exists()]
    print(f"PREVIEW SCOPE: {segs[0].id}..{segs[-1].id} ({len(segs)} segments)")
    print(f"  pending clips: {len(pending)} (~{est_secs:.0f}s video, "
          f"~{est_secs * 2.9:.0f} Kie credits / ~${est_secs * _COST_PER_SECOND:.2f}, "
          f"worst case x{args.max_attempts})")
    if flf_segs:
        print(f"  FLF beats to generate (Kling lane): {[s.id for s in flf_segs]}")
    if (pending or flf_segs) and not args.yes:
        print("  COST PREVIEW ONLY — re-run with --yes to generate.")
        return

    # --- 1. Generate pending shots (anchor-refreshed, chain-aware, gated) ----
    for j in pending:
        seg = seg_by_id.get(j.segment_id)
        anchor = j.keyframe or None
        if j.chain_from:
            prev = manifest.get_shot(j.segment_id, j.chain_from)
            chained = None
            if prev is not None and prev.status == "done" and Path(prev.output).exists():
                chained = vr.resolve_chain_anchor(prev.output, Path(j.output).parent)
            anchor = chained or _refresh_anchor(j, seg, bible)
            print(f"  {j.key}: chain anchor {'OK' if chained else 'FALLBACK'}")
        else:
            anchor = _refresh_anchor(j, seg, bible)
        print(f"  [{j.key}] generating ({j.duration_s:.1f}s) ...")
        Path(j.output).parent.mkdir(parents=True, exist_ok=True)
        try:
            clip = vr.generate_shot(j.video_prompt, anchor, j.duration_s, j.provider,
                                    j.seed, j.output, enable_gemini=True,
                                    max_attempts=args.max_attempts,
                                    description=j.description, narration=j.narration)
        except GenerationHardStop as hs:
            print(f"  HARD STOP: {hs}")
            sys.exit(1)
        j.status = "done" if clip else "failed"
        manifest.save(MANIFEST)
        if clip is None:
            print(f"  ! FAILED {j.key} — preview blocked (fix or re-roll in the dashboard)")
            sys.exit(1)

    # --- 1b. FLF beats (explanatory) — the controllable lane (Nano start + Kling FLF) ----
    if flf_segs:
        print(f"  generating {len(flf_segs)} FLF beat(s) via the controllable lane ...")
        flf_out = generate_flf_segments(script, dm, FLF_DIR, bible=bible,
                                        keyframe_dir=PROJECT / "assets" / "keyframes",
                                        seg_ids={s.id for s in flf_segs})
        for sid, clip in flf_out.items():
            if not (clip and Path(clip).exists()):
                print(f"  ! FLF beat failed: {sid}")
                sys.exit(1)
            print(f"  {sid}: FLF beat OK")

    # --- 2. Per-segment clips at exact slots --------------------------------
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    seg_clips: list[tuple[str, Path]] = []
    for seg in segs:
        ts = dm.get_segment(seg.id)
        slot = ts.total_duration_s if ts else 5.0
        out = SEGMENTS_DIR / f"{seg.id}.mp4"
        if seg.visual.type == "manim_animation":
            from lib.sketch_diagrams import render_template, SCENES
            sys.path.insert(0, str(PROJECT / "script_v5"))
            from build_v6 import SKETCH_SCENE_MAP, SKETCH_SEGMENT_OVERRIDE, MANIM_ASSETS  # type: ignore
            scene = SKETCH_SEGMENT_OVERRIDE.get(seg.id) or SKETCH_SCENE_MAP.get(
                seg.visual.template, seg.visual.template)
            if scene in SCENES:
                res = render_template(scene, slot, out, finish=False)
                if not (res and Path(res).exists()):
                    print(f"  ! sketch render failed for {seg.id}")
                    sys.exit(1)
                out = Path(res)  # the renderer's ACTUAL output path, not the requested one
            else:
                cand = MANIM_ASSETS.get(seg.visual.template)
                if not (cand and cand.exists()):
                    print(f"  ! no diagram for {seg.id}")
                    sys.exit(1)
                vr._trim_to_duration(str(cand), out, slot)
        elif getattr(seg.visual, "flf", None) is not None:
            cached = FLF_DIR / f"{seg.id}.mp4"
            if not cached.exists():
                print(f"  ! FLF clip missing for {seg.id} — re-run with --yes to generate")
                sys.exit(1)
            out = cached  # FLF lane already conformed it to the slot
        else:
            jobs = manifest.shots_for_segment(seg.id)
            clips = [j.output for j in jobs if Path(j.output).exists()]
            res = vr.concat_segment_shots(clips, out, slot)
            if res is None:
                print(f"  ! concat failed for {seg.id}")
                sys.exit(1)
        # overlays
        if seg.visual.text_overlay:
            from lib.text_overlay import apply_text_overlay
            ovl = PREVIEW_DIR / f"{seg.id}_ovl.mp4"
            apply_text_overlay(str(out), str(ovl), seg.visual.text_overlay,
                               emphasis=seg.visual.text_emphasis)
            out = ovl
        seg_clips.append((seg.id, out))
        print(f"  {seg.id}: segment clip ready ({slot:.2f}s)")

    # --- 3. Assemble: normalize -> concat -> audio -> finish ----------------
    norm_list = []
    for sid, p in seg_clips:
        n = PREVIEW_DIR / f"n_{sid}.mp4"
        _ffmpeg(["-i", str(p), "-map", "0:v:0", "-vf", SCALE_VF, "-an",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(n)])
        norm_list.append(n)
    lst = PREVIEW_DIR / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in norm_list), encoding="utf-8")
    video = PREVIEW_DIR / "preview_video.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])

    # narration audio for exactly these segments (mp3 + silence_after)
    aparts = []
    for seg in segs:
        mp3 = AUDIO_DIR / f"seg_{seg.index:03d}.mp3"
        if mp3.exists():
            aparts.append(f"file '{mp3.resolve().as_posix()}'\n")
        sil = getattr(seg, "silence_after_s", 0) or 0
        if sil > 0:
            sp = PREVIEW_DIR / f"sil_{seg.id}.mp3"
            if not sp.exists():
                _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(sil),
                         "-c:a", "libmp3lame", "-b:a", "192k", str(sp)])
            aparts.append(f"file '{sp.resolve().as_posix()}'\n")
    alst = PREVIEW_DIR / "audio_concat.txt"
    alst.write_text("".join(aparts), encoding="utf-8")
    aud = PREVIEW_DIR / "preview_audio.mp3"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(alst),
             "-c:a", "libmp3lame", "-b:a", "192k", str(aud)])

    muxed = PREVIEW_DIR / "preview_muxed.mp4"
    _ffmpeg(["-i", str(video), "-i", str(aud), "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(muxed)])

    from lib.finishing import apply_finish
    RENDERS.mkdir(parents=True, exist_ok=True)
    final = RENDERS / f"{args.out_name}.mp4"
    apply_finish(str(muxed), str(final))

    # sanity: duration vs expected
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(final)], capture_output=True, text=True)
    got = float(p.stdout.strip())
    want = sum((dm.get_segment(s.id).total_duration_s if dm.get_segment(s.id) else 0)
               for s in segs)
    print(f"\nPREVIEW: {final}  ({got:.1f}s vs expected {want:.1f}s, "
          f"delta {got - want:+.2f}s)")
    if abs(got - want) > 2.0:
        print("  WARNING: preview duration off by >2s — inspect before trusting sync")


if __name__ == "__main__":
    main()
