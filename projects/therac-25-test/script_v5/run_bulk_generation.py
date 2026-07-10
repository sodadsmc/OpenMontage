"""Phase B — bulk video generation (cloud; runs anywhere).

Reads the Shot Manifest from build_render_package.py and generates every pending
image-to-video shot in one batch (default provider: Grok Imagine i2v via Kie.ai —
cloud, no GPU box), then concatenates each segment's shots into a segment clip.
Resumable: shots already marked "done" are skipped, so you can re-run after an
interruption.

Requires: KIE_API_KEY (Grok/Nano via Kie.ai) + ffmpeg on PATH.
Free-compute alternative: set DEFAULT_VIDEO_PROVIDER="wan" in lib/visual_router.py
and run on a GPU box (VIDEO_GEN_LOCAL_ENABLED=true + Wan weights).

Usage:
  python projects/therac-25-test/script_v5/run_bulk_generation.py          # cost preview + pre-flight (no spend)
  python projects/therac-25-test/script_v5/run_bulk_generation.py --yes     # actually generate (paid)
  python projects/therac-25-test/script_v5/run_bulk_generation.py --yes --stop-on-fail   # pause on first failure (safe first run)
  python projects/therac-25-test/script_v5/run_bulk_generation.py --yes --provider grok-kie
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Rough per-second figure for the PRE-FLIGHT preview only. The AUTHORITATIVE spend is
# the real Kie credit balance logged before/after each run (see _kie_balance). Measured:
# grok-imagine i2v ~= 2.9 Kie credits/sec (Kie bills in credits, not dollars).
_COST_PER_SECOND = {"grok-kie": 0.017, "kie": 0.05, "wan": 0.0, "ltx": 0.0}


def _kie_balance() -> float | None:
    """Current Kie.ai credit balance, or None if unavailable. Kie exposes no
    per-transaction API — only the live balance — so we diff it across a run to log
    the REAL credits spent."""
    key = os.environ.get("KIE_API_KEY")
    if not key:
        return None
    base = (os.environ.get("KIE_BASE_URL") or "https://api.kie.ai").rstrip("/")
    try:
        import requests
        r = requests.get(base + "/api/v1/chat/credit",
                         headers={"Authorization": f"Bearer {key}"}, timeout=20)
        data = r.json()
        v = data.get("data") if isinstance(data, dict) else None
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None

PROJECT = Path("projects/therac-25-test")
ARTIFACTS_DIR = PROJECT / "artifacts"
MANIFEST_PATH = ARTIFACTS_DIR / "shot_manifest_v6.json"
SEGMENTS_DIR = PROJECT / "assets" / "ai_segments"
AI_VISUAL_ASSETS = ARTIFACTS_DIR / "ai_visual_assets_v6.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk video generation (Phase B, cloud)")
    parser.add_argument("--provider", default=None,
                        help="Only generate jobs for this provider (e.g. grok-kie, wan)")
    parser.add_argument("--no-gemini", action="store_true",
                        help="Disable Gemini quality checks")
    parser.add_argument("--yes", action="store_true",
                        help="Actually spend: run the paid bulk generation. Without it, this "
                             "prints a cost preview + pre-flight checks and exits without spending.")
    parser.add_argument("--stop-on-fail", action="store_true",
                        help="Pause the batch on the FIRST shot that fails (after its quality-gate "
                             "retries) instead of continuing through the rest. Caps worst-case spend "
                             "and lets you correct before running the remainder. Resume-safe "
                             "(done shots are skipped on the next run).")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="Quality-gate attempts per shot before it counts as failed (default 3). "
                             "Lower it (e.g. 1) to fail faster / spend less on a systemic problem.")
    args = parser.parse_args()

    import tools.base_tool  # noqa: F401  -- importing loads .env into os.environ
    from lib.shot_manifest import ShotManifest
    from lib import visual_router as vr

    print("=" * 60)
    print("PHASE B: BULK VIDEO GENERATION")
    print("=" * 60)

    if not MANIFEST_PATH.exists():
        print(f"  ERROR: {MANIFEST_PATH} not found. Run build_render_package.py first.")
        sys.exit(1)
    manifest = ShotManifest.load(MANIFEST_PATH)

    pending = manifest.pending_jobs(args.provider)
    print(f"  {len(pending)} pending shots "
          f"(total {len(manifest.shots)}, already done {manifest.counts()['done']})")

    # --- Pre-flight + cost gate (this is the paid step) ---------------------
    by_provider: dict[str, int] = {}
    for j in pending:
        by_provider[j.provider] = by_provider.get(j.provider, 0) + 1
    est = sum(_COST_PER_SECOND.get(j.provider, 0.0) * j.duration_s for j in pending)
    if by_provider:
        print("  Providers: " + ", ".join(f"{k}x{v}" for k, v in sorted(by_provider.items())))
        # Kie bills in CREDITS (~2.9/sec for grok-imagine i2v), NOT dollars — show that
        # as the headline so the --yes decision reflects the real spend + retry ceiling.
        grok_secs = sum(j.duration_s for j in pending if j.provider == "grok-kie")
        credits = grok_secs * 2.9
        print(f"  Estimated Kie credits (grok-kie ~2.9/s over {grok_secs:.0f}s): "
              f"~{credits:.0f}  (worst case ~{credits * 3:.0f} if every shot retries 3x)")
        print(f"  Authoritative spend = the Kie balance diff logged below.  (USD ref ~${est:.2f})")

    # Fail fast if a Kie provider is needed but the key is absent / ffmpeg is missing.
    if any(p in ("grok-kie", "kie") for p in by_provider) and not os.environ.get("KIE_API_KEY"):
        print("  ERROR: KIE_API_KEY is not set — required for grok-kie/kie generation.")
        sys.exit(1)
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("  ERROR: ffmpeg/ffprobe not found on PATH — required to concat shots.")
        sys.exit(1)

    # Guard: never spend on placeholder/empty prompts (a stale planning manifest).
    bad = [j for j in pending
           if not (j.video_prompt or "").strip() or (j.video_prompt or "").strip() == "(planned)"]
    if bad:
        print(f"  ERROR: {len(bad)} pending shots have empty/placeholder prompts "
              f"(e.g. {bad[0].key!r} -> {bad[0].video_prompt!r}).")
        print("  Re-run build_render_package.py to rebuild the manifest with real prompts/anchors.")
        sys.exit(1)

    if not pending:
        print("  Nothing pending — all shots already done. Proceeding to concat any existing clips.")
    elif not args.yes:
        print("\n  COST PREVIEW ONLY — no generation performed.")
        print("  Pre-flight checks passed. Re-run with --yes to spend the estimate above.")
        return

    bal_before = _kie_balance()
    if bal_before is not None:
        print(f"  Kie balance before run: {bal_before:.0f} credits")

    from lib.quality_gate import GenerationHardStop
    for i, job in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] {job.key} ({job.provider}, {job.duration_s:.1f}s) ...")
        Path(job.output).parent.mkdir(parents=True, exist_ok=True)
        # Chained leg: anchor to the PREVIOUS leg's final frame so this clip continues
        # the scene instead of re-rolling the same keyframe. Manifest order is
        # per-segment sequential, so the predecessor has already run in this loop
        # (or in a prior resumed run). Falls back to the planned keyframe if the
        # predecessor failed or frame extraction/hosting fails.
        anchor = job.keyframe or None
        chain_from = getattr(job, "chain_from", "")
        if chain_from:
            prev = manifest.get_shot(job.segment_id, chain_from)
            chained = None
            if prev is not None and prev.status == "done" and Path(prev.output).exists():
                chained = vr.resolve_chain_anchor(prev.output, Path(job.output).parent)
            if chained:
                anchor = chained
                print(f"      chain: continuing {chain_from} from its final frame")
            else:
                print(f"      chain: WARNING — {chain_from} unavailable, using planned keyframe")
        try:
            clip = vr.generate_shot(
                job.video_prompt, anchor, job.duration_s,
                job.provider, job.seed, job.output, enable_gemini=not args.no_gemini,
                max_attempts=args.max_attempts,
                description=getattr(job, "description", ""),
                narration=getattr(job, "narration", ""),
                ref_urls=getattr(job, "veo_refs", None) or None,
            )
        except GenerationHardStop as hs:
            # Out of credits / daily limit / host down — stop now, don't spend on
            # the remaining shots (they'd fail the same way). Resume later with --yes.
            print(f"    STOP: non-retryable failure -> {hs}")
            print(f"    Aborting after {i - 1}/{len(pending)} shots to save credits. "
                  f"Fix the cause and re-run; done shots are skipped.")
            break
        job.status = "done" if clip is not None else "failed"
        manifest.save(MANIFEST_PATH)  # checkpoint after each shot (resume-safe)
        if clip is None:
            print(f"    ! FAILED {job.key}")
            print(f"      prompt : {(job.video_prompt or '')[:90]!r}")
            print(f"      anchor : {job.keyframe or '(none / text-to-video)'}")
            if args.stop_on_fail:
                print(f"\n  PAUSED on first failure (--stop-on-fail) at shot {i}/{len(pending)}. "
                      f"Not spending on the remaining {len(pending) - i} shots. Inspect {job.key} "
                      f"(clip path: {job.output}) and the log above, fix the cause, then re-run "
                      f"with --yes to resume — done shots are skipped.")
                break

    bal_after = _kie_balance()
    if bal_before is not None and bal_after is not None:
        print(f"  Kie balance after run:  {bal_after:.0f} credits  |  REAL SPEND: "
              f"{bal_before - bal_after:.0f} credits for {len(pending)} shots")

    # Concatenate each segment's shots into one segment clip
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    visual_assets: dict[str, str] = {}
    for seg in manifest.segments:
        clips, complete = [], True
        for sid in seg.shot_ids:
            job = manifest.get_shot(seg.segment_id, sid)
            if job and job.status == "done" and Path(job.output).exists():
                clips.append(job.output)
            else:
                complete = False
        if not clips:
            print(f"  {seg.segment_id}: no clips — skipping concat")
            continue
        if not complete:
            # Do NOT ship a partial segment as the AI asset — it would be freeze-
            # padded to the full slot with missing content silently passing as good.
            # Write a _PARTIAL for inspection and let the downstream fallback fill it.
            partial = Path(seg.output).with_name(Path(seg.output).stem + "_PARTIAL.mp4")
            vr.concat_segment_shots(clips, partial, seg.target_duration_s)
            print(f"  {seg.segment_id}: INCOMPLETE ({len(clips)}/{len(seg.shot_ids)} shots) "
                  f"-> {partial.name} (NOT used; fallback will fill)")
            continue
        out = vr.concat_segment_shots(clips, seg.output, seg.target_duration_s)
        if out is None:
            print(f"  {seg.segment_id}: concat failed")
            continue
        # Accept only if the assembled segment is within tolerance of its slot.
        dur = vr._probe_duration(str(out))
        if dur is not None and abs(dur - seg.target_duration_s) > 0.15:
            print(f"  {seg.segment_id}: concat is {dur:.2f}s vs target "
                  f"{seg.target_duration_s:.2f}s (>150ms off) — NOT used")
            continue
        visual_assets[seg.segment_id] = str(out)
        print(f"  {seg.segment_id}: assembled {len(clips)} shots -> {out}")

    AI_VISUAL_ASSETS.write_text(json.dumps(visual_assets, indent=2), encoding="utf-8")
    print(f"\n  Wrote {AI_VISUAL_ASSETS} ({len(visual_assets)} AI segments)")
    failed = [j.key for j in manifest.shots if j.status == "failed"]
    if failed:
        print(f"  {len(failed)} shots FAILED: {', '.join(failed[:10])}")
    print("  Next: build_v6.py (picks up these AI segments) -> render_v6.py")


if __name__ == "__main__":
    main()
