"""Focused end-to-end test of the STYLED ai-video pipeline on the clean corridor
canonical: real seam (plan_ai_video -> generate_shot) -> finishing pass.

Validates: (1) host-free URL anchor threads through generate_shot, (2) Grok i2v
continues the graphic-novel look in motion, (3) the ffmpeg finishing pass applies.
Extracts 3 frames (start/mid/end) for visual review.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.base_tool  # noqa: F401  -- loads .env
from lib.asset_bible import AssetBible
from lib import visual_router as vr
from lib.finishing import apply_finish

PROJECT = ROOT / "projects" / "therac-25-test"
OUT = PROJECT / "assets" / "_style_test"
OUT.mkdir(parents=True, exist_ok=True)

bible = AssetBible.load(PROJECT / "artifacts" / "asset_bible_v6.json")
asset = bible.get("loc_kennestone_corridor")
print("anchor (canonical_image_url):", (asset.canonical_image_url or "")[:72])

# Minimal seg_001-like spec (corridor, atmospheric, single ~10s shot)
spec = SimpleNamespace(
    effective_prompt="completely empty deserted 1985 hospital corridor, fluorescent "
                     "ceiling tubes, speckled linoleum, closed exam-room doors, no people",
    description="1985 hospital corridor",
    ai_style="cold clinical emptiness, oppressive quiet, no people, deserted",
    # Gentle, minimal-reveal motion: a long dolly exposes new geometry the i2v
    # model hallucinates into. Keep it nearly static so little new area appears.
    ai_motion="extremely slow subtle forward drift, nearly static camera, minimal reveal",
    asset_ref="loc_kennestone_corridor",
    location_id="kennestone_corridor",
    shots=None,
)

jobs = vr.plan_ai_video("seg_001_test", spec, OUT, target_duration_s=8.0,
                        bible=bible, asset=asset)
job = jobs[0]
print(f"shots planned: {len(jobs)}")
print("keyframe/anchor resolved to:", str(job['keyframe'])[:72])
print("anchor is URL:", str(job['keyframe']).startswith('http'))
print("provider:", job['provider'], "| duration:", job['duration_s'], "s")
print("video_prompt:", job['video_prompt'][:160], "...")

raw = OUT / "corridor_clip_raw.mp4"
clip = vr.generate_shot(
    job["video_prompt"], job["keyframe"] or None, job["duration_s"],
    job["provider"], job["seed"], raw, enable_gemini=False,
)
if clip is None:
    print("RESULT: generation FAILED")
    sys.exit(2)
print("raw clip:", clip, f"({Path(clip).stat().st_size/1e6:.2f} MB)")

final = OUT / "corridor_clip_final.mp4"
fin = apply_finish(str(clip), str(final))
print("finished clip:", fin)

# Extract 3 frames for review
src = Path(fin) if fin and Path(fin).exists() else Path(clip)
for label, ss in (("start", "0.2"), ("mid", "4.0"), ("end", "7.5")):
    fp = OUT / f"corridor_frame_{label}.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", ss, "-i", str(src),
                    "-frames:v", "1", str(fp)], check=False)
    print("frame:", fp, "exists:", fp.exists())
print("OK")
