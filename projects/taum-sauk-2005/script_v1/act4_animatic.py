"""Act 4 storyboard animatic ($0): beat keyframes held for their word-timed
spans; the already-built sketch scenes (seg_015/016) included as their real
renders; cut against the real act narration."""
import json, os, sys
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

PROJ = Path("projects/taum-sauk-2005")
BE_D = PROJ / "assets/ai_segments/_beats"
AI = PROJ / "assets/ai_segments"
AUD = PROJ / "assets/audio_v6"
OUT = PROJ / "renders"
TMP = OUT / "_animatic_act4"; TMP.mkdir(parents=True, exist_ok=True)
FPS = 30
NORM = be.NORM
DUR = {s["id"]: s for s in json.loads((PROJ / "artifacts/duration_map_v6.json").read_text(encoding="utf-8"))["segments"]}
plans = {p["scene_id"]: p for p in json.loads((PROJ / "artifacts/beat_plans.json").read_text(encoding="utf-8"))}
ACT4 = ["seg_015", "seg_016", "seg_017", "seg_018", "seg_019", "seg_020", "seg_021", "seg_022"]
BUILT = {"seg_015", "seg_016"}   # sketch scenes, already rendered

def still_clip(png, dur, out):
    be.run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-frames:v", str(round(dur * FPS)),
            "-vf", NORM, "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(out)])

parts = []
for sid in ACT4:
    total = DUR[sid]["total_duration_s"]
    if sid in BUILT:
        src = AI / f"{sid}.mp4"
        assert src.exists(), f"missing built scene {src}"
        c = TMP / f"{sid}.mp4"
        be.run(["ffmpeg", "-y", "-i", str(src), "-t", f"{total:.3f}", "-vf", NORM,
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(c)])
        parts.append(c)
        print(f"{sid}: built sketch {total:.1f}s", flush=True)
        continue
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    for i, (s, e) in enumerate(sp, 1):
        png = BE_D / f"{sid}_b{i}.png"
        assert png.exists(), f"missing keyframe {png}"
        c = TMP / f"{sid}_b{i}.mp4"; still_clip(png, e - s, c); parts.append(c)
    print(f"{sid}: {len(sp)} beat stills", flush=True)

lst = TMP / "concat.txt"
lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts))
vid = TMP / "video.mp4"
be.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(vid)])
aparts = []
for sid in ACT4:
    aparts.append(AUD / f"{sid}.mp3")
    sil = DUR[sid]["silence_after_s"]
    if sil > 0:
        sp = TMP / f"sil_{sid}.mp3"
        be.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(sil), "-c:a", "libmp3lame", "-b:a", "128k", str(sp)])
        aparts.append(sp)
alst = TMP / "aud.txt"
alst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in aparts))
aud = TMP / "audio.mp3"
be.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(alst), "-c:a", "libmp3lame", "-b:a", "128k", str(aud)])
final = OUT / "animatic_act4.mp4"
be.run(["ffmpeg", "-y", "-i", str(vid), "-i", str(aud), "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", "-shortest", str(final)])
print(f"ANIMATIC: {final} ({be.durof(final):.1f}s)", flush=True)
