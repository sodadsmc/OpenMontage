"""Phase A — build the render package (prep, cheap).

Produces everything needed for the bulk video job EXCEPT the bulk i2v clips:
  - per-shot keyframes (Nano Banana, image-to-video anchors)
  - the Shot Manifest (every i2v job fully specified)
  - HERO shot clips generated up front (Grok i2v via Kie.ai) so you can REVIEW the
    look and approve before the bulk spend.

Run order:
  build_asset_bible.py  ->  generate_voice_v6.py  ->  build_render_package.py
  ->  run_bulk_generation.py  ->  build_v6.py  ->  render_v6.py

Usage:
  python projects/therac-25-test/script_v5/build_render_package.py
  python projects/therac-25-test/script_v5/build_render_package.py --dry-run   # plan only, no image/clip gen
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

PROJECT = Path("projects/therac-25-test")
SCRIPT_PATH = PROJECT / "script_v5" / "scored_script.yaml"
ARTIFACTS_DIR = PROJECT / "artifacts"
AUDIO_DIR = PROJECT / "assets" / "audio_v6"
AUDIO_V5_DIR = PROJECT / "assets" / "audio_v5"
KEYFRAME_DIR = PROJECT / "assets" / "keyframes"
SHOTS_DIR = PROJECT / "assets" / "shots"
SEGMENTS_DIR = PROJECT / "assets" / "ai_segments"
BIBLE_PATH = ARTIFACTS_DIR / "asset_bible_v6.json"
MANIFEST_PATH = ARTIFACTS_DIR / "shot_manifest_v6.json"

AI_PRIMARY = os.environ.get("AI_VIDEO_PRIMARY", "0") == "1"
FOOTAGE_TYPES = {"atmospheric_footage", "generated_footage", "stock_footage", "archival_footage"}


def _is_ai_segment(seg) -> bool:
    # FLF beats (visual.flf set) are explanatory state-changes routed through the
    # controllable FLF lane (lib.flf), NOT the paid open-i2v manifest — exclude them here.
    if getattr(seg.visual, "flf", None) is not None:
        return False
    if seg.visual.type == "ai_video":
        return True
    return AI_PRIMARY and seg.visual.type in ("atmospheric_footage", "generated_footage")


def _build_duration_map(script):
    """Build the duration map from TTS audio (v6, falling back to v5)."""
    from lib.duration_map import build_duration_map_from_paths
    audio_paths = {}
    for seg in script.segments:
        v6 = AUDIO_DIR / f"seg_{seg.index:03d}.mp3"
        v5 = AUDIO_V5_DIR / f"seg_{seg.index - 1:03d}.mp3"
        if v6.exists():
            audio_paths[seg.id] = str(v6)
        elif v5.exists():
            audio_paths[seg.id] = str(v5)
    return build_duration_map_from_paths(script, audio_paths)


def _segment_boundaries(seg):
    """Sentence-end times (seconds) from the segment's ElevenLabs word-level
    alignment, or None when the audio predates timestamp support. Used to snap
    chained-clip seams to natural pauses instead of equal time slices."""
    al = AUDIO_DIR / f"seg_{seg.index:03d}.alignment.json"
    if not al.exists():
        return None
    try:
        from lib.word_timing import load_alignment, sentence_ends
        return sentence_ends(load_alignment(al))
    except Exception as exc:  # noqa: BLE001
        print(f"    (alignment unreadable for {seg.id}: {exc} — equal-slice legs)")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Therac-25 render package (Phase A)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan shots only — no keyframe/hero-clip generation")
    parser.add_argument("--skip-narration-gate", action="store_true",
                        help="Skip the narration<->visual alignment gate (NOT recommended — "
                             "misaligned prompts are cheap to fix here and expensive after "
                             "the bulk spend)")
    args = parser.parse_args()

    import tools.base_tool  # noqa: F401 — side effect: loads .env (GOOGLE_API_KEY,
    # KIE_API_KEY) BEFORE the narration gate / beat splitter / keyframe calls run
    from lib.scored_script import load_scored_script
    from lib.asset_bible import AssetBible
    from lib.shot_manifest import ShotManifest, ShotJob, SegmentPlan
    from lib import visual_router as vr

    print("=" * 60)
    print(f"PHASE A: RENDER PACKAGE  (AI_PRIMARY={AI_PRIMARY}, dry_run={args.dry_run})")
    print("=" * 60)

    script = load_scored_script(SCRIPT_PATH)

    # --- Narration<->visual alignment gate (pre-spend; the cheapest place to catch
    # "the clip won't depict what's narrated"). Hard gate: fix the scored script or
    # consciously override with --skip-narration-gate / SKIP_NARRATION_GATE=1.
    if not (args.skip_narration_gate or os.environ.get("SKIP_NARRATION_GATE") == "1"):
        try:
            from lib.narration_gate import validate_script_alignment, gate
            print("  Narration alignment gate (Gemini) ...")
            results = validate_script_alignment(script)
            report_path = ARTIFACTS_DIR / "narration_alignment_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            report_path.write_text(_json.dumps(results, indent=2), encoding="utf-8")
            bad = [r for r in results if r.get("verdict") == "mismatch"]
            print(f"    {len(results)} segments checked -> "
                  f"{sum(1 for r in results if r.get('verdict') == 'match')} match, "
                  f"{sum(1 for r in results if r.get('verdict') == 'partial')} partial, "
                  f"{len(bad)} mismatch  (report: {report_path})")
            if not gate(results):
                for r in bad[:5]:
                    print(f"    MISMATCH {r.get('segment_id')}: {r.get('missing_elements')}")
                print("  GATE FAILED: visuals won't depict what's narrated. Fix the scored "
                      "script's ai_prompt/description (suggested_prompt is in the report), "
                      "or re-run with --skip-narration-gate to proceed anyway.")
                sys.exit(1)
        except ImportError:
            print("  (narration gate unavailable — lib/narration_gate.py not present; skipping)")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: narration gate errored: {exc}")
            print("  Refusing to proceed un-gated. Re-run with --skip-narration-gate to override.")
            sys.exit(1)

    if not BIBLE_PATH.exists():
        print(f"  ERROR: {BIBLE_PATH} not found. Run build_asset_bible.py first.")
        sys.exit(1)
    bible = AssetBible.load(BIBLE_PATH)
    print(f"  Asset Bible: {len(bible.assets)} assets")

    # --- Subject identity gate (deterministic, free). A prompt that calls a
    # grounded named subject by a generic term ("a large beige linear
    # accelerator" for the Therac-25) licenses the model to redesign it.
    from lib.asset_bible import validate_subject_identity
    identity_errors = validate_subject_identity(script, bible)
    if identity_errors:
        print(f"  SUBJECT IDENTITY GATE FAILED ({len(identity_errors)}):")
        for e in identity_errors:
            print(f"    {e}")
        print("  Fix the scored script's prompts to NAME the subject, or set "
              "SKIP_IDENTITY_GATE=1 to proceed anyway.")
        if os.environ.get("SKIP_IDENTITY_GATE") != "1":
            sys.exit(1)

    dm = _build_duration_map(script)

    manifest = ShotManifest(project="therac-25")
    for seg in script.segments:
        if not _is_ai_segment(seg):
            continue
        ts = dm.get_segment(seg.id)
        target = ts.total_duration_s if ts else 5.0
        asset = bible.asset_for_segment(seg)

        # plan_ai_video is free in the default anchor mode — each shot's anchor
        # resolves to its existing canonical URL and NO keyframe image is generated
        # (unless AI_PER_SHOT_KEYFRAMES=1). So ALWAYS build real prompts + anchors:
        # the saved manifest must be generation-ready. --dry-run ONLY skips the paid
        # HERO-clip generation below — never the prompt/anchor planning. (A previous
        # placeholder dry-run wrote "(planned)" prompts that then generated garbage.)
        jobs = vr.plan_ai_video(seg.id, seg.visual, KEYFRAME_DIR, target, bible=bible,
                                asset=asset, narration=seg.narration,
                                allow_paid_keyframes=not args.dry_run,
                                leg_boundaries=_segment_boundaries(seg))

        shot_ids = []
        for job in jobs:
            out = SHOTS_DIR / f"{seg.id}_{job['shot_id']}.mp4"
            manifest.shots.append(ShotJob(
                segment_id=seg.id, shot_id=job["shot_id"], provider=job["provider"],
                operation=job["operation"], video_prompt=job["video_prompt"],
                duration_s=job["duration_s"], seed=job["seed"], output=str(out),
                keyframe=job["keyframe"], aspect_ratio=job["aspect_ratio"], hero=job["hero"],
                chain_from=job.get("chain_from", ""),
                description=job.get("description", ""),
                narration=job.get("narration", ""),
                veo_refs=job.get("veo_refs") or [],
            ))
            shot_ids.append(job["shot_id"])
        manifest.segments.append(SegmentPlan(
            segment_id=seg.id, target_duration_s=target, shot_ids=shot_ids,
            output=str(SEGMENTS_DIR / f"{seg.id}.mp4"),
        ))
        print(f"  {seg.id}: {len(shot_ids)} shots  (asset={asset.asset_id if asset else 'none'})")

    # Generate HERO shots now (Kie.ai, API) so they can be reviewed before the batch
    hero_done = 0
    if not args.dry_run:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        for job in manifest.shots:
            if not job.hero:
                continue
            print(f"  + HERO {job.key}: generating now ({job.provider}) ...")
            clip = vr.generate_shot(
                job.video_prompt, job.keyframe or None, job.duration_s,
                job.provider, job.seed, job.output, enable_gemini=False,
                ref_urls=getattr(job, "veo_refs", None) or None,
            )
            if clip is not None:
                job.status = "done"
                hero_done += 1
            else:
                job.status = "failed"
                print(f"    ! hero generation failed for {job.key}")

    # Never clobber a run's forensic record: if the existing manifest has DONE
    # shots (a completed/partial production run), keep a timestamped backup
    # before overwriting — re-planning resets every shot to pending and would
    # otherwise erase the only record of prompts/seeds/keyframes that shipped.
    if MANIFEST_PATH.exists():
        try:
            import json as _json
            from datetime import datetime
            old = _json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if any(s.get("status") == "done" for s in old.get("shots", [])):
                bak = MANIFEST_PATH.with_name(
                    f"{MANIFEST_PATH.stem}_{datetime.now():%Y%m%d_%H%M%S}.bak.json")
                bak.write_text(_json.dumps(old, indent=2), encoding="utf-8")
                print(f"  (backed up previous manifest with done shots -> {bak.name})")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not back up existing manifest: {exc}")
    manifest.save(MANIFEST_PATH)
    c = manifest.counts()
    print(f"\n  Saved manifest: {MANIFEST_PATH}")
    print(f"  Shots: {c['total']} total | hero generated now: {hero_done} | "
          f"bulk pending: {len(manifest.pending_jobs())}")
    if not args.dry_run:
        print(f"\n  REVIEW the hero clips in {SHOTS_DIR} before running the bulk batch.")
    print("  Next: run_bulk_generation.py (cloud — Grok i2v via Kie.ai).")


if __name__ == "__main__":
    main()
