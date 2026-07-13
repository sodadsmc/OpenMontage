"""Render the 5 manim diagrams at 1080p, conform each to its total slot
(freeze-pad the ending hold), and install to ai_segments/{sid}.mp4 so the
assembler picks them up as real clips.

  python render_diagrams.py [seg_005 seg_008 ...]   # subset, default all
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")

PROJ = Path("projects/taum-sauk-2005")
AI = PROJ / "assets" / "ai_segments"
SCRIPT = PROJ / "script_v1" / "taum_diagrams.py"
MEDIA = Path(r"C:\Users\Soda\AppData\Local\Temp\claude\D--OpenMontage2\2050e49f-8bdc-4332-a901-426801e202a0\scratchpad\manim_media")
PY = r"C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe"
FPS = 30
NORM = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#091327,fps=30,format=yuv420p")
DUR = {s["id"]: s for s in json.loads((PROJ / "artifacts" / "duration_map_v6.json").read_text(encoding="utf-8"))["segments"]}

SCENES = {  # sid -> manim class
    "seg_005": "PumpedStorageCycle",
    "seg_008": "SensorGap008",
    "seg_015": "SensorGap015",
    "seg_016": "SensorGap016",
    "seg_026": "SensorGap026",
}

def run(a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(str(a[:3]) + " :: " + r.stderr[-400:])
    return r

def durof(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def conform(src, target, out):
    run(["ffmpeg", "-y", "-i", str(src), "-t", f"{target:.3f}", "-vf", NORM,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", str(out)])
    have = durof(out)
    if have < target - 0.03:  # freeze-pad the closing hold
        fr = str(out) + ".png"; run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(out), "-frames:v", "1", fr])
        pad = str(out) + ".pad.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-i", fr, "-frames:v", str(round((target - have) * FPS) + 1),
             "-vf", f"scale=1920:1080,fps={FPS},format=yuv420p", "-c:v", "libx264",
             "-preset", "medium", "-crf", "18", "-an", pad])
        lst = str(out) + ".txt"
        open(lst, "w").write(f"file '{Path(out).resolve().as_posix()}'\nfile '{Path(pad).resolve().as_posix()}'\n")
        j = str(out) + ".j.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "libx264",
             "-preset", "medium", "-crf", "18", "-an", j]); os.replace(j, out)

def main():
    want = sys.argv[1:] or list(SCENES.keys())
    for sid in want:
        cls = SCENES[sid]
        run([PY, "-m", "manim", "-qh", "--fps", "30", "--media_dir", str(MEDIA), str(SCRIPT), cls])
        raw = MEDIA / "videos" / "taum_diagrams" / "1080p30" / f"{cls}.mp4"
        if not raw.exists():
            print(f"{sid}: RENDER MISSING {raw}"); continue
        target = DUR[sid]["total_duration_s"]
        out = AI / f"{sid}.mp4"
        conform(raw, target, out)
        print(f"{sid} <- {cls}: raw={durof(raw):.1f}s -> slot {target:.1f}s  ({durof(out):.1f}s)", flush=True)
    print("DIAGRAMS DONE", flush=True)

if __name__ == "__main__":
    main()
