"""Render the v6 Therac-25 documentary from the Duration Map + visual assets.

Builds a CONSTANT-frame-rate timeline: every segment is conformed to RENDER_FPS and
trimmed-or-freeze-frame-padded to EXACTLY its narration-slot duration, concatenated,
then muxed under the single continuous narration track. Sync is a HARD gate.

Consumes visual_assets_v6.json, which build_v6.py writes ONLY after its pre-assembly
sync gate passes — so the renderer always reads a gated, in-sync asset map (no more
split-brain with the retired generate_visuals_v6.py).
"""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from lib.duration_map import load_duration_map, get_video_duration
from lib.finishing import apply_finish
from lib.sync_validator import validate_post_render

# Uniform constant frame rate for the whole timeline (matches lib.visual_router.TARGET_FPS).
RENDER_FPS = int(os.environ.get("RENDER_FPS", "30"))
SCALE_VF = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:-1:-1:color=black")

# Paths
PROJECT = Path("projects/therac-25-test")
DM_PATH = PROJECT / "artifacts" / "duration_map_v6.json"
ASSETS_PATH = PROJECT / "artifacts" / "visual_assets_v6.json"
AUDIO_DIR = PROJECT / "assets" / "audio_v6"
RENDER_DIR = PROJECT / "renders"
RENDER_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT = RENDER_DIR / "therac25_v6.mp4"


def _black(path, dur):
    """Generate a black filler clip at the uniform fps."""
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s=1920x1080:d={dur}:r={RENDER_FPS}",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(path),
    ], capture_output=True, timeout=30)


# Load
dm = load_duration_map(DM_PATH)
if not ASSETS_PATH.exists():
    print(f"ERROR: {ASSETS_PATH} not found. Run build_v6.py first (it writes the "
          f"gated visual_assets_v6.json).")
    sys.exit(1)
with open(ASSETS_PATH) as f:
    visual_assets = json.load(f)

print(f"Duration Map: {dm.segment_count} segments, {dm.total_duration_s:.1f}s")
print(f"Visual assets: {len(visual_assets)}")
print()

# Step 1: Conform each visual to RENDER_FPS and EXACTLY its slot duration.
print(f"Step 1: Conforming visuals to {RENDER_FPS}fps + exact durations...")
trim_dir = PROJECT / "assets" / "trimmed_v6"
trim_dir.mkdir(parents=True, exist_ok=True)

trimmed_paths = []
for ts in dm.segments:
    # Conform to the frame CEILING of the slot: -t floors to the frame grid, so
    # flooring every segment leaves the concatenated video a few frames SHORT of
    # the narration (0.18s over 36 segments in practice) and the sync gate
    # rightly refuses to ship. Ceiling per segment keeps video >= narration by
    # construction (< 1 frame over per segment; the mux has no -shortest).
    target = math.ceil(ts.total_duration_s * RENDER_FPS - 1e-6) / RENDER_FPS
    asset_path = visual_assets.get(ts.id, "")
    trimmed = trim_dir / f"{ts.id}.mp4"

    if not asset_path or not Path(asset_path).exists():
        _black(trimmed, target)
        trimmed_paths.append(str(trimmed))
        print(f"  {ts.id}: BLACK ({target:.1f}s)")
        continue

    src_dur = get_video_duration(asset_path)
    vf = f"{SCALE_VF},fps={RENDER_FPS},setsar=1"
    # Freeze-frame pad whenever the source might not cover the ceiled target
    # (always with a 2-frame margin; -t clamps to the exact target). NEVER loop:
    # a loop restarts the clip mid-narration, which reads as a jarring jump-cut.
    if src_dur < target + (1.0 / RENDER_FPS):
        vf += (f",tpad=stop_mode=clone:stop_duration="
               f"{max(0.0, target - src_dur) + 2.0 / RENDER_FPS:.3f}")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(asset_path),
        "-vf", vf, "-t", f"{target:.3f}", "-an",
        "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(trimmed),
    ], capture_output=True, timeout=120)

    if trimmed.exists() and trimmed.stat().st_size > 1024:
        actual = get_video_duration(trimmed)
        trimmed_paths.append(str(trimmed))
        pad = "  (freeze-padded)" if src_dur < target - 0.1 else ""
        print(f"  {ts.id}: {actual:.1f}s (target {target:.1f}s){pad}")
    else:
        _black(trimmed, target)
        trimmed_paths.append(str(trimmed))
        print(f"  {ts.id}: CONFORM FAILED, using black")

# Step 2: Concat the (now uniform CFR) segments. Stream-copy since codec params
# match — single encode, clean splices; re-encode only if stream-copy is rejected.
print(f"\nStep 2: Concatenating {len(trimmed_paths)} segments...")
video_concat = trim_dir / "video_concat.txt"
with open(video_concat, "w") as f:
    for path in trimmed_paths:
        safe = str(Path(path).resolve()).replace("\\", "/")
        f.write(f"file '{safe}'\n")

video_only = trim_dir / "video_only.mp4"
result = subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(video_concat),
    "-c", "copy", str(video_only),
], capture_output=True, timeout=600)
if result.returncode != 0:
    print("  stream-copy concat rejected; re-encoding to a uniform CFR timeline...")
    result = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(video_concat),
        "-r", str(RENDER_FPS), "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-pix_fmt", "yuv420p", str(video_only),
    ], capture_output=True, timeout=600)
if result.returncode != 0:
    print(f"  Video concat FAILED: {result.stderr[-300:]}")
    sys.exit(1)

video_dur = get_video_duration(video_only)
print(f"  Video track: {video_dur:.1f}s")

# Step 3: Mux video + continuous narration. NO -shortest: each segment is conformed
# slightly-over from frame rounding, so the video track (~+0.3s) fully covers the
# narration; -shortest would instead clamp the muxed video a hair UNDER the audio.
# A genuinely short video is caught by the post-render gate (video-stream vs total).
print(f"\nStep 3: Muxing video + narration audio...")
narration = AUDIO_DIR / "narration_v6.mp3"
if not narration.exists():
    print(f"  ERROR: Narration file not found: {narration}")
    sys.exit(1)

pre_finish = trim_dir / "muxed_prefinish.mp4"
result = subprocess.run([
    "ffmpeg", "-y",
    "-i", str(video_only),
    "-i", str(narration),
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "192k",
    str(pre_finish),
], capture_output=True, timeout=300)
if result.returncode != 0:
    print(f"  Mux FAILED: {result.stderr[-300:]}")
    sys.exit(1)

# Step 3b: channel finishing pass over the WHOLE assembled timeline. This is the
# single uniform grade (duotone + grain per the active channel style) that makes the
# AI footage AND the hand-drawn diagrams share one look — the great equalizer. Run it
# once here, at the end, so nothing is double-graded. FINISH=0 skips it (debug).
print(f"\nStep 3b: Applying channel finishing pass (duotone + grain)...")
if os.environ.get("FINISH", "1") == "0":
    shutil.copyfile(pre_finish, OUTPUT)
    print("  FINISH=0 -> skipped finishing (shipping ungraded mux).")
else:
    graded = apply_finish(str(pre_finish), str(OUTPUT))
    if graded is None:
        print("  Finishing FAILED -> shipping the ungraded mux.")
        shutil.copyfile(pre_finish, OUTPUT)
    elif Path(graded).resolve() != OUTPUT.resolve():
        # passthrough: active style has no finishing filter -> graded == pre_finish
        shutil.copyfile(graded, OUTPUT)
        print("  (active style has no finishing filter; shipped ungraded)")
    else:
        print("  Finishing applied to the full timeline.")

final_dur = get_video_duration(OUTPUT)
print(f"  Final render: {final_dur:.1f}s ({final_dur/60:.1f} min)")
print(f"  Output: {OUTPUT}")

# Step 4: Post-render sync validation — HARD gate (matches sync_validator's contract).
print(f"\nStep 4: Post-render sync validation...")
report = validate_post_render(OUTPUT, dm)
print(report.summary())
if not report.passed:
    print("\n  RENDER FAILED sync validation — not shipping a desynced render.")
    sys.exit(1)

print("\n  Render PASSED all sync checks")
print(f"\nDone. Output: {OUTPUT}")
