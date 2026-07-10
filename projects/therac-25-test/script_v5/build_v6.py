"""OpenMontage v6 Integrated Pipeline — Therac-25 Documentary

Chains all v6 architectural components into a single pipeline:

    STAGE 0: Parse scored script (YAML → dataclasses)
    STAGE 1: Structural validation (binding, card types, uniqueness)
    STAGE 2: TTS generation → Duration Map (timing contract)
    STAGE 3: Visual generation (duration-constrained, styled cards, asset registry)
    STAGE 4: Pre-assembly sync validation (hard gate)
    STAGE 5: Assembly manifest generation
    STAGE 6: Post-render sync validation (hard gate)

Usage:
    python projects/therac-25-test/script_v5/build_v6.py
    python projects/therac-25-test/script_v5/build_v6.py --dry-run
    python projects/therac-25-test/script_v5/build_v6.py --stage 3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Project paths
PROJECT = Path("projects/therac-25-test")
SCRIPT_PATH = PROJECT / "script_v5" / "scored_script.yaml"
AUDIO_DIR = PROJECT / "assets" / "audio_v6"
VISUALS_DIR = PROJECT / "assets" / "visuals_v6"
ARTIFACTS_DIR = PROJECT / "artifacts"
GEN_DIR = PROJECT / "assets" / "video" / "generated"
STOCK_DIR = PROJECT / "assets" / "video" / "clips"
AI_SEGMENTS_DIR = PROJECT / "assets" / "ai_segments"  # pre-generated AI segment clips
CONFORMED_DIR = PROJECT / "assets" / "conformed_v6"   # every asset trimmed/padded to its exact slot
MANIM_DIR = PROJECT / "assets" / "visuals_v3" / "media" / "videos" / "1080p30"

# Generation-first (the default): footage-typed segments generate AI video to
# DEPICT the narration, and fall back to the stock map only when generation is
# disabled or explicitly permitted. Set AI_VIDEO_PRIMARY=0 to force the legacy
# stock-first behavior. (Stock fallback for ai_video itself stays gated behind
# ALLOW_AI_STOCK_FALLBACK so depiction failures surface loudly, not silently.)
AI_PRIMARY = os.environ.get("AI_VIDEO_PRIMARY", "1") != "0"

# Legacy Manim template → file mapping (FALLBACK ONLY — superseded by the hand-drawn
# sketch diagrams below; used only if a sketch scene isn't built yet).
MANIM_ASSETS = {
    "radiation_therapy": MANIM_DIR / "RadiationTherapy.mp4",
    "safety_interlocks": MANIM_DIR / "SafetyInterlocks.mp4",
    "race_condition": MANIM_DIR / "RaceCondition.mp4",
    "byte_overflow": MANIM_DIR / "ByteOverflow.mp4",
    "dose_chart": MANIM_DIR / "DoseChart.mp4",
    "incident_timeline": MANIM_DIR / "IncidentTimeline.mp4",
    "process_flow": MANIM_DIR / "ProcessFlow.mp4",
    "state_machine": MANIM_DIR / "StateMachine.mp4",
}

# Script manim template -> hand-drawn sketch scene (lib.sketch_diagrams.SCENES).
# These replace the clean Manim clips with the graphic-novel inked diagrams that
# match the footage. Rendered raw (no per-clip finishing); render_v6 grades the
# whole timeline once at the end.
SKETCH_SCENE_MAP = {
    "radiation_therapy": "linac",
    "race_condition": "race_condition",
    "dose_chart": "beam_fires",
    "byte_overflow": "byte_overflow",
}
# Per-segment override where one template serves two different beats (seg_028 is the
# rollover; seg_029 is the false-SAFE / beam-fires consequence).
SKETCH_SEGMENT_OVERRIDE = {
    "seg_029": "false_safe",
}

# Generated footage keyword → file mapping
GENERATED_MAP = {
    "corridor": GEN_DIR / "scene_corridor.mp4",
    "treatment room": GEN_DIR / "scene_treatment_room.mp4",
    "patient": GEN_DIR / "scene_patient_pain.mp4",
    "flinch": GEN_DIR / "scene_patient_pain.mp4",
    "pain": GEN_DIR / "scene_patient_pain.mp4",
    "hospital exterior": GEN_DIR / "scene_hospital_exterior.mp4",
    "kennestone": GEN_DIR / "scene_hospital_exterior.mp4",
    "ontario": GEN_DIR / "scene_hospital_exterior_2.mp4",
    "hamilton": GEN_DIR / "scene_hospital_exterior_2.mp4",
    "canadian": GEN_DIR / "scene_hospital_exterior_2.mp4",
    "yakima": GEN_DIR / "scene_corridor.mp4",
    "tyler": GEN_DIR / "scene_tyler_interior.mp4",
    "east texas": GEN_DIR / "scene_tyler_interior.mp4",
    "interior": GEN_DIR / "scene_tyler_interior.mp4",
    "fda": GEN_DIR / "scene_fda_documents.mp4",
    "letterhead": GEN_DIR / "scene_fda_documents.mp4",
    "correspondence": GEN_DIR / "scene_fda_documents.mp4",
    "empty room": GEN_DIR / "scene_empty_room_end.mp4",
    "lights off": GEN_DIR / "scene_empty_room_end.mp4",
    "powered down": GEN_DIR / "scene_empty_room_end.mp4",
    "paper": GEN_DIR / "scene_fda_documents.mp4",
    "academic": GEN_DIR / "scene_fda_documents.mp4",
}

stock_pool = sorted(STOCK_DIR.glob("pexels_*.mp4"))[:50]
stock_idx = 0


def pick_stock():
    global stock_idx
    if stock_pool:
        clip = stock_pool[stock_idx % len(stock_pool)]
        stock_idx += 1
        return clip
    return None


# ======================================================================
# STAGE 0: Parse
# ======================================================================

def stage_0_parse():
    """Parse the scored script."""
    from lib.scored_script import load_scored_script

    print("=" * 60)
    print("STAGE 0: PARSE SCORED SCRIPT")
    print("=" * 60)

    script = load_scored_script(SCRIPT_PATH)
    print(f"  Title: {script.title}")
    print(f"  Segments: {len(script.segments)}")
    print(f"  Words: {script.total_words}")
    print(f"  Acts: {[a.title for a in script.acts]}")
    print()
    return script


# ======================================================================
# STAGE 1: Validate
# ======================================================================

def stage_1_validate(script):
    """Structural validation — blocks pipeline on errors."""
    from lib.script_validator import validate_structure
    from lib.scored_script import validate_scored_script_file

    print("=" * 60)
    print("STAGE 1: STRUCTURAL VALIDATION")
    print("=" * 60)

    # JSON Schema validation
    schema_errors = validate_scored_script_file(SCRIPT_PATH)
    if schema_errors:
        print("  SCHEMA ERRORS:")
        for e in schema_errors:
            print(f"    {e}")
        sys.exit(1)
    print("  Schema validation: PASSED")

    # Structural validation
    report = validate_structure(script)
    print(report.summary())

    if report.blocking:
        print("\n  PIPELINE BLOCKED — fix errors before proceeding")
        sys.exit(1)

    print()
    return report


# ======================================================================
# STAGE 2: TTS + Duration Map
# ======================================================================

def stage_2_duration_map(script, dry_run=False):
    """Generate TTS audio and build the duration map."""
    from lib.duration_map import (
        build_duration_map_from_paths,
        save_duration_map,
        get_audio_duration,
    )

    print("=" * 60)
    print("STAGE 2: TTS + DURATION MAP")
    print("=" * 60)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Check for existing audio from v5 (old segment split)
    old_audio = Path("projects/therac-25-test/assets/audio_v5")
    old_files = sorted(old_audio.glob("seg_*.mp3"))

    if old_files:
        print(f"  Found {len(old_files)} audio files from v5")
        print("  NOTE: v5 has different segment splits than v6 scored script")
        print("  In production: regenerate TTS for scored script segments")
        print()

    # Build audio path mapping
    # For dry-run/testing: map v6 segments to closest v5 audio
    audio_paths = {}
    for seg in script.segments:
        # Try exact match first (new audio)
        v6_path = AUDIO_DIR / f"seg_{seg.index:03d}.mp3"
        if v6_path.exists():
            audio_paths[seg.id] = str(v6_path)
            continue

        # Fall back to v5 audio with index offset (seg_001 → seg_000)
        v5_idx = seg.index - 1
        v5_path = old_audio / f"seg_{v5_idx:03d}.mp3"
        if v5_path.exists():
            audio_paths[seg.id] = str(v5_path)
            continue

        if dry_run:
            # Estimate duration from word count (160 WPM)
            est_dur = seg.word_count / 160 * 60
            print(f"  {seg.id}: no audio, estimated {est_dur:.1f}s from {seg.word_count} words")
            continue
        else:
            print(f"  ERROR: No audio for {seg.id}")
            print(f"  Run TTS generation first or use --dry-run")
            sys.exit(1)

    if dry_run and len(audio_paths) < len(script.segments):
        # Build estimated duration map for dry run
        print(f"  Dry run: {len(audio_paths)} real + "
              f"{len(script.segments) - len(audio_paths)} estimated")
        print("  Using estimated durations (160 WPM) for missing audio")
        print()

        # Create a synthetic duration map
        from lib.duration_map import DurationMap, TimedSegment
        timed = []
        cursor = 0.0
        total_audio = 0.0
        total_silence = 0.0

        for seg in script.segments:
            if seg.id in audio_paths:
                dur = get_audio_duration(audio_paths[seg.id])
            else:
                dur = seg.word_count / 160 * 60  # estimate

            silence = seg.silence_after_s
            total_dur = dur + silence

            timed.append(TimedSegment(
                id=seg.id, act=seg.act, narration=seg.narration,
                audio_path=audio_paths.get(seg.id, ""),
                audio_duration_s=round(dur, 3),
                silence_after_s=silence,
                total_duration_s=round(total_dur, 3),
                timeline_start_s=round(cursor, 3),
                timeline_end_s=round(cursor + total_dur, 3),
                visual_type=seg.visual.type,
                visual_description=seg.visual.description,
                word_count=seg.word_count,
            ))
            total_audio += dur
            total_silence += silence
            cursor += total_dur

        dm = DurationMap(
            segments=timed,
            total_duration_s=round(cursor, 3),
            total_audio_s=round(total_audio, 3),
            total_silence_s=round(total_silence, 3),
            segment_count=len(timed),
        )
    else:
        dm = build_duration_map_from_paths(script, audio_paths)

    # Save
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    save_duration_map(dm, ARTIFACTS_DIR / "duration_map_v6.json")

    print(dm.summary())
    print()
    return dm


# ======================================================================
# STAGE 3: Visual Generation
# ======================================================================

def stage_3_visuals(script, duration_map, dry_run=False):
    """Generate visual assets — duration-constrained, with registry."""
    from lib.asset_registry import AssetRegistry
    from lib.quality_gate import QualityGate

    print("=" * 60)
    print("STAGE 3: VISUAL GENERATION")
    print("=" * 60)

    VISUALS_DIR.mkdir(parents=True, exist_ok=True)

    registry = AssetRegistry()
    gate = QualityGate(enable_gemini=False)  # Gemini optional
    visual_assets = {}  # seg_id → path

    # Preflight: inline AI generation (_try_ai_video) runs with enable_gemini=True so
    # the narration-match gate vets each clip before it lands in the cut. Without a
    # GOOGLE_API_KEY the gate can't fail-closed (it warns + degrades to Layer-1 only),
    # so an UNATTENDED bulk run could splice un-vetted clips into the assembly — the
    # exact "clips don't match narration" failure this project exists to prevent.
    # Refuse rather than ship blind; ALLOW_UNVETTED=1 is the deliberate keyless escape.
    if AI_PRIMARY and not dry_run and not os.environ.get("GOOGLE_API_KEY") \
            and os.environ.get("ALLOW_UNVETTED") != "1":
        raise SystemExit(
            "STAGE 3 blocked: GOOGLE_API_KEY is not set, so the narration-match "
            "quality gate cannot run on inline AI generation. Set GOOGLE_API_KEY to "
            "vet clips, or pass ALLOW_UNVETTED=1 for a deliberate keyless/offline run."
        )

    # Load the Asset Bible if present (canonical refs + continuity groups)
    bible = None
    bible_path = ARTIFACTS_DIR / "asset_bible_v6.json"
    if bible_path.exists():
        from lib.asset_bible import AssetBible
        bible = AssetBible.load(bible_path)
        print(f"  Asset Bible loaded: {len(bible.assets)} assets")
    if AI_PRIMARY:
        print("  AI-primary mode: footage segments generate AI video first")

    def _try_ai_video(seg, target):
        """Use a pre-generated AI segment clip from the box batch (Phase B) if
        present; otherwise generate inline. Returns a Path or None."""
        pre = AI_SEGMENTS_DIR / f"{seg.id}.mp4"
        if pre.exists():
            return pre
        if dry_run:
            return None
        from lib.visual_router import generate_ai_video
        asset = bible.asset_for_segment(seg) if bible is not None else None
        # Gemini gate ON: the inline path generates un-reviewed clips straight into
        # the assembly, so it needs the artifact/narration check MORE than the bulk
        # path (which at least has the hero review upstream).
        res = generate_ai_video(
            seg.id, seg.visual, VISUALS_DIR, target,
            bible=bible, asset=asset, enable_gemini=True, narration=seg.narration,
        )
        return Path(res.path) if res else None

    for seg in script.segments:
        ts = duration_map.get_segment(seg.id)
        target_dur = ts.total_duration_s if ts else 5.0

        vtype = seg.visual.type
        desc_lower = seg.visual.description.lower()

        asset_path = None

        # Route by visual type
        if vtype == "text_card":
            if not dry_run:
                from lib.visual_router import generate_styled_card
                result = generate_styled_card(
                    seg.id, seg.visual, VISUALS_DIR, target_dur
                )
                if result:
                    asset_path = Path(result.path)
            label = f"text_card:{seg.visual.card_type}"

        elif vtype == "manim_animation":
            template = seg.visual.template
            scene = SKETCH_SEGMENT_OVERRIDE.get(seg.id) or \
                SKETCH_SCENE_MAP.get(template, template)
            if not dry_run:
                try:
                    from lib.sketch_diagrams import render_template, SCENES
                except Exception as exc:  # noqa: BLE001
                    print(f"    sketch import failed: {exc}")
                    render_template, SCENES = None, {}
                # Prefer the narration-SYNCED variant when the segment has a word
                # alignment: reveals land on spoken words, not hardcoded phases.
                synced_draw = None
                if render_template:
                    try:
                        from lib.sketch_diagrams import SYNCED_SCENES, resolve_cues
                        if scene in SYNCED_SCENES:
                            align_p = AUDIO_DIR / f"{seg.id}.alignment.json"
                            if align_p.exists():
                                factory, cues = SYNCED_SCENES[scene]
                                align = json.loads(align_p.read_text(encoding="utf-8"))
                                synced_draw = factory(resolve_cues(align, cues))
                                print(f"    sketch: narration-synced '{scene}'")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    synced sketch unavailable ({exc}); using static timing")
                if render_template and (synced_draw is not None or scene in SCENES):
                    out = VISUALS_DIR / f"{seg.id}_diagram.mp4"
                    res = render_template(synced_draw or scene, target_dur, out,
                                          finish=False)
                    if res and Path(res).exists():
                        asset_path = Path(res)
                if asset_path is None:
                    # No hand-built sketch scene for this template — mint one with
                    # the Coder->Critic codegen loop (same renderer/palette, so it
                    # matches the hand-coded scenes by construction).
                    try:
                        from lib.diagram_codegen import generate_diagram_scene
                        out = VISUALS_DIR / f"{seg.id}_diagram.mp4"
                        brief = seg.visual.description or template
                        res = generate_diagram_scene(brief, target_dur, out,
                                                     narration=seg.narration)
                        if res and Path(res).exists():
                            asset_path = Path(res)
                            print(f"    diagram codegen minted scene for {seg.id} ({template})")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    diagram codegen failed: {exc}")
                if asset_path is None:
                    # Last resort: legacy Manim clip (clean-vector look; the duotone
                    # finishing pass will crush its colors — known mismatch).
                    cand = MANIM_ASSETS.get(template)
                    if cand and cand.exists():
                        asset_path = cand
            label = f"sketch:{scene}"

        elif getattr(seg.visual, "flf", None) is not None:
            # Explanatory state-change -> the controllable FLF lane (lib.flf), not open i2v.
            # (build_render_package already excludes these from the paid manifest.)
            out = VISUALS_DIR / f"{seg.id}_flf.mp4"
            if out.exists():
                asset_path = out
            elif not dry_run:
                from lib.flf import flf_segment
                res = flf_segment(seg.visual.flf, target_dur, out,
                                  keyframe_dir=PROJECT / "assets" / "keyframes", bible=bible)
                if res and Path(res).exists():
                    asset_path = Path(res)
            label = "flf"

        elif vtype == "ai_video":
            asset_path = _try_ai_video(seg, target_dur)
            if not (asset_path and asset_path.exists()):
                # AI generation failed/unavailable. Do NOT silently substitute photoreal
                # stock: a Pexels clip passes the duration-only gate and ships a hard
                # style break into the inked graphic-novel cut. Leave the segment MISSING
                # so the stage_4 sync gate surfaces it and the build stops loudly.
                # (Set ALLOW_AI_STOCK_FALLBACK=1 for a quick draft that tolerates stock.)
                asset_path = None
                if os.environ.get("ALLOW_AI_STOCK_FALLBACK") == "1":
                    stock = pick_stock()
                    if stock:
                        asset_path = stock
            label = "ai_video"

        elif vtype in ("atmospheric_footage", "generated_footage"):
            # Generation-first: these legacy "footage" types are treated like
            # ai_video — generate AI to depict the narration. Fall back to the
            # stock map ONLY when generation is disabled (AI_VIDEO_PRIMARY=0) or
            # explicitly permitted (ALLOW_AI_STOCK_FALLBACK=1); otherwise leave the
            # segment MISSING so the stage_4 sync gate surfaces it loudly, instead
            # of silently shipping a photoreal stock clip into the inked cut.
            if AI_PRIMARY:
                ai = _try_ai_video(seg, target_dur)
                if ai and ai.exists():
                    asset_path = ai
            allow_stock = (not AI_PRIMARY) or os.environ.get("ALLOW_AI_STOCK_FALLBACK") == "1"
            if asset_path is None and allow_stock:
                # Match generated footage by keywords
                for keyword, path in GENERATED_MAP.items():
                    if keyword in desc_lower and path.exists():
                        asset_path = path
                        break
                if not asset_path:
                    stock = pick_stock()
                    if stock:
                        asset_path = stock
            label = "footage"

        elif vtype in ("archival_footage", "stock_footage"):
            stock = pick_stock()
            if stock:
                asset_path = stock
            label = "stock"

        else:
            stock = pick_stock()
            if stock:
                asset_path = stock
            label = vtype

        if asset_path and asset_path.exists():
            # Conform EVERY real asset to its exact slot duration (+ 30fps CFR, no
            # audio) so the pre-assembly sync gate is meaningful and the renderer
            # receives slot-exact clips. AI clips arrive already exact (idempotent);
            # raw stock / generated / Manim clips get trimmed or freeze-padded here.
            if not dry_run:
                from lib.visual_router import _trim_to_duration
                CONFORMED_DIR.mkdir(parents=True, exist_ok=True)
                ct = _trim_to_duration(str(asset_path), CONFORMED_DIR / f"{seg.id}.mp4", target_dur)
                if ct is not None:
                    asset_path = Path(ct)
                # "Text over footage": burn the styled caption over the conformed clip.
                # BURN_OVERLAYS=0 skips ALL caption burns (operator call 2026-07-08:
                # scenes carry their own composited text now; static burned captions
                # stack on top and read as noise).
                overlay = list(getattr(seg.visual, "text_overlay", []) or [])
                if os.environ.get("BURN_OVERLAYS", "1") == "0":
                    overlay = []
                if overlay:
                    from lib.text_overlay import apply_text_overlay
                    res = apply_text_overlay(
                        asset_path, CONFORMED_DIR / f"{seg.id}_ovl.mp4", overlay,
                        emphasis=getattr(seg.visual, "text_emphasis", -1),
                    )
                    if res is not None:
                        asset_path = Path(res)
            visual_assets[seg.id] = str(asset_path)
            # Pass the bible's continuity group so same-asset segments are
            # exempt from the perceptual-duplicate check (intended consistency).
            cg = None
            if bible is not None:
                a = bible.asset_for_segment(seg)
                if a is not None:
                    cg = a.continuity_group or a.asset_id
            aid = registry.register(seg.id, asset_path, seg.visual, continuity_group=cg)
            status = "OK"
        elif dry_run:
            visual_assets[seg.id] = f"<{label}:{target_dur:.1f}s>"
            status = "DRY"
        else:
            status = "MISSING"

        print(f"  {seg.id} [{label:30s}] {target_dur:6.1f}s  {status}")

    # Uniqueness check
    print()
    violations = registry.check_all()
    if violations:
        print("  UNIQUENESS VIOLATIONS:")
        for v in violations:
            print(f"    {v}")
    else:
        print(f"  Asset Registry: {registry.count} assets, no uniqueness violations")

    print(f"\n{registry.summary()}")
    registry.save(ARTIFACTS_DIR / "asset_registry_v6.json")
    print()

    return visual_assets


# ======================================================================
# STAGE 4: Pre-Assembly Sync Validation
# ======================================================================

def stage_4_sync_check(duration_map, visual_assets, dry_run=False):
    """Pre-assembly sync validation — hard gate."""
    from lib.sync_validator import validate_pre_assembly

    print("=" * 60)
    print("STAGE 4: PRE-ASSEMBLY SYNC VALIDATION")
    print("=" * 60)

    if dry_run:
        # Filter to only real file paths
        real_assets = {
            k: v for k, v in visual_assets.items()
            if not v.startswith("<") and Path(v).exists()
        }
        print(f"  Dry run: checking {len(real_assets)}/{len(visual_assets)} "
              f"real assets (rest are placeholders)")
        if real_assets:
            report = validate_pre_assembly(duration_map, real_assets, tolerance_s=60.0)
            print(report.summary())
        else:
            print("  No real assets to validate in dry-run mode")
    else:
        report = validate_pre_assembly(duration_map, visual_assets)
        print(report.summary())
        if not report.passed:
            print("\n  PIPELINE BLOCKED — fix sync errors before assembly")
            sys.exit(1)
        # Persist the GATED asset map as the single source of truth the renderer
        # reads (render_v6 consumes visual_assets_v6.json). Writing it ONLY here —
        # after the pre-assembly sync gate passes — removes the split-brain where
        # render_v6 used an un-gated file from the retired generate_visuals_v6.py.
        real = {k: v for k, v in visual_assets.items()
                if isinstance(v, str) and not v.startswith("<") and Path(v).exists()}
        assets_out = ARTIFACTS_DIR / "visual_assets_v6.json"
        with open(assets_out, "w", encoding="utf-8") as f:
            json.dump(real, f, indent=2)
        print(f"  Wrote gated {assets_out} ({len(real)} segments)")

    print()


# ======================================================================
# STAGE 5: Assembly Manifest
# ======================================================================

def stage_5_assembly(script, duration_map, visual_assets, dry_run=False):
    """Build assembly manifest from duration map + visual assets."""

    print("=" * 60)
    print("STAGE 5: ASSEMBLY MANIFEST")
    print("=" * 60)

    cuts = []
    for ts in duration_map.segments:
        source = visual_assets.get(ts.id, "")

        cuts.append({
            "id": f"cut_{ts.id}",
            "source": source,
            "in_seconds": 0,
            "out_seconds": ts.total_duration_s,
            "speed": 1.0,
            "_scene_id": ts.id,
            "_act": ts.act,
            "_timeline_start": ts.timeline_start_s,
            "_timeline_end": ts.timeline_end_s,
            "_visual_type": ts.visual_type,
            "_audio_path": ts.audio_path,
            "_audio_duration": ts.audio_duration_s,
            "_silence_after": ts.silence_after_s,
        })

    manifest = {
        "version": "2.0",
        "pipeline": "v6-scored-script",
        "assembly_manifest": {
            "version": "2.0",
            "total_duration_s": duration_map.total_duration_s,
            "segment_count": duration_map.segment_count,
            "cuts": cuts,
        },
    }

    out_path = ARTIFACTS_DIR / "assembly_manifest_v6.json"
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Summary
    type_counts = {}
    for ts in duration_map.segments:
        type_counts[ts.visual_type] = type_counts.get(ts.visual_type, 0) + 1

    print(f"  Assembly: {len(cuts)} cuts, "
          f"{duration_map.total_duration_s:.1f}s ({duration_map.total_duration_s/60:.1f} min)")
    for vtype, count in sorted(type_counts.items()):
        print(f"    {vtype}: {count}")
    print(f"  Saved: {out_path}")
    print()

    return manifest


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="OpenMontage v6 Pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without generating assets or requiring audio")
    parser.add_argument("--stage", type=int, default=0,
                        help="Start from a specific stage (0-5)")
    args = parser.parse_args()

    print()
    print("  OpenMontage v6 — Integrated Pipeline")
    print("  Therac-25 Documentary")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'PRODUCTION'}")
    print()

    # Stage 0: Parse
    script = stage_0_parse()

    # Stage 1: Validate
    if args.stage <= 1:
        stage_1_validate(script)

    # Stage 2: Duration Map
    if args.stage <= 2:
        dm = stage_2_duration_map(script, dry_run=args.dry_run)
    else:
        from lib.duration_map import load_duration_map
        dm = load_duration_map(ARTIFACTS_DIR / "duration_map_v6.json")

    # Stage 3: Visual Generation
    if args.stage <= 3:
        visual_assets = stage_3_visuals(script, dm, dry_run=args.dry_run)

    # Stage 4: Sync Check
    if args.stage <= 4:
        stage_4_sync_check(dm, visual_assets, dry_run=args.dry_run)

    # Stage 5: Assembly Manifest
    if args.stage <= 5:
        manifest = stage_5_assembly(script, dm, visual_assets, dry_run=args.dry_run)

    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
