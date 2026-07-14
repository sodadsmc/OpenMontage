"""Beat executor: take a per-scene beat plan, resolve beat timing from the word
alignment, author a BORDER-SAFE keyframe per beat (grounded on its sheets),
animate the verb with a short grok clip, conform to the beat span, concat ->
ai_segments/{sid}.mp4. Border safety is enforced at BOTH the keyframe and the
clip (the operator's hard rule: NO borders).

  python beat_exec.py <plans.json> [seg_006 seg_007 ...]   # subset = an act
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); k, v = k.strip(), v.split("#")[0].strip()
        if v: os.environ.setdefault(k, v)
from PIL import Image
from lib.gemini_image import gemini_image
from lib.frame_hygiene import find_border_box, video_border_hits
from lib import visual_router as vr

PROJ = Path("projects/taum-sauk-2005")
B = PROJ / "assets" / "asset_bible"
AI = PROJ / "assets" / "ai_segments"; AI.mkdir(parents=True, exist_ok=True)
AUD = PROJ / "assets" / "audio_v6"
WORK = AI / "_beats"; WORK.mkdir(exist_ok=True)
FPS = 30
NORM = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#091327,fps=30,format=yuv420p")
KFSTYLE = (" Graphic-novel illustration, bold black ink linework, halftone shading, "
           "duotone deep navy and warm amber, high contrast, film grain, illustrated "
           "NOT photorealistic. Full-bleed, image fills the entire frame edge to edge, "
           "NO panel border, NO frame, NO caption, NO text.")
MOT = " Graphic-novel ink illustration style held exactly; ONE continuous shot, no cuts."
DUR = {s["id"]: s for s in json.loads((PROJ / "artifacts" / "duration_map_v6.json").read_text(encoding="utf-8"))["segments"]}

def run(a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(str(a[:3]) + " :: " + r.stderr[-300:])
def durof(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def spans(sid, anchors):
    """Resolve phrase anchors -> [start,end] per beat via char alignment."""
    al = json.loads((AUD / f"{sid}.alignment.json").read_text(encoding="utf-8"))
    chars = al["alignment"]["characters"]; starts = al["alignment"]["character_start_times_seconds"]
    low = "".join(chars).lower()
    audio = DUR[sid]["audio_duration_s"]; sil = DUR[sid]["silence_after_s"]
    ts = []
    for a in anchors:
        i = low.find(a.strip().lower()[:40])
        ts.append(round(float(starts[i]), 2) if i >= 0 and i < len(starts) else None)
    # first beat starts at 0; fill Nones by interpolation
    ts[0] = 0.0
    for k in range(1, len(ts)):
        if ts[k] is None or ts[k] <= (ts[k-1] or 0):
            ts[k] = round((ts[k-1] or 0) + audio / len(ts), 2)
    out = []
    for k in range(len(ts)):
        end = ts[k+1] if k+1 < len(ts) else audio
        out.append((ts[k], round(end, 2)))
    # last beat absorbs the silence tail (freeze-hold)
    s, e = out[-1]; out[-1] = (s, round(audio + sil, 2))
    return out

def author_kf(prompt, sheets, out):
    refs = [str(B / s) for s in sheets if (B / s).exists()][:2]
    ok = gemini_image(prompt + KFSTYLE, str(out), image_paths=refs or None)
    if not ok: return False
    box = find_border_box(Image.open(out).convert("RGB"))
    if box:  # crop any baked-in panel border
        Image.open(out).convert("RGB").crop(box).save(out)
    return True

def animate(kf, motion, span, out, seed):
    clip = None
    for attempt in range(2):  # retry transient grok timeouts before falling back
        raw = str(out) + f".raw{attempt}.mp4"
        try:
            c = vr._gen_shot_clip(motion + MOT, Path(kf), min(6.0, span), "grok-kie",
                                  seed + attempt * 13, Path(raw))
            if c and Path(c).exists():
                clip = c; break
        except Exception as e:
            print(f"    grok attempt {attempt+1} failed: {str(e)[:90]}", flush=True)
    if not clip or not Path(clip).exists():
        # last resort: gentle push on the (clean, action-carrying) keyframe
        print(f"    -> PUSH FALLBACK for {Path(out).name}", flush=True)
        n = round(span * FPS)
        vf = (f"scale=3840:2160:flags=lanczos,zoompan=z='1+0.05*on/NF':d={n}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps={FPS},format=yuv420p").replace("NF", str(n))
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(kf), "-frames:v", str(n), "-vf", vf,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])
        clip = out
    # border-safe: crop clip if a border slipped through
    vf = NORM
    hits = video_border_hits(clip, 0, min(3, durof(clip)))
    if hits:
        vf = "crop=iw*0.89:ih*0.89:iw*0.055:ih*0.055," + NORM
    # conform to span (trim or freeze-pad)
    run(["ffmpeg", "-y", "-i", str(clip), "-t", f"{span:.3f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])
    have = durof(out)
    if have < span - 0.05:
        fr = str(out) + ".png"; run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(out), "-frames:v", "1", fr])
        pad = str(out) + ".pad.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-i", fr, "-frames:v", str(round((span-have)*FPS)+1),
             "-vf", f"scale=1920:1080,fps={FPS},format=yuv420p", "-c:v", "libx264",
             "-preset", "fast", "-crf", "18", "-an", pad])
        lst = str(out) + ".txt"
        open(lst, "w").write(f"file '{Path(out).resolve().as_posix()}'\nfile '{Path(pad).resolve().as_posix()}'\n")
        j = str(out) + ".j.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "libx264",
             "-preset", "fast", "-crf", "18", "-an", j]); os.replace(j, out)

DISSOLVE = 0.5  # crossfade length between beats flagged for a dissolve

def conform_to_slot(src, target, out):
    run(["ffmpeg", "-y", "-i", str(src), "-t", f"{target:.3f}", "-vf", NORM,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])
    have = durof(out)
    if have < target - 0.03:  # freeze-pad the time the dissolves removed
        fr = str(out) + ".png"; run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(out), "-frames:v", "1", fr])
        pad = str(out) + ".pad.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-i", fr, "-frames:v", str(round((target - have) * FPS) + 1),
             "-vf", f"scale=1920:1080,fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", pad])
        l2 = str(out) + ".cf.txt"; open(l2, "w").write(f"file '{Path(out).resolve().as_posix()}'\nfile '{Path(pad).resolve().as_posix()}'\n")
        j = str(out) + ".cj.mp4"; run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", l2, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", j]); os.replace(j, out)

def stitch(parts, flags, out, slot):
    """Join beats: hard-concat where flags[i] is False, crossfade where True.
    flags[0] is ignored (first beat has no incoming transition)."""
    if not any(flags[1:]):  # all hard cuts -> fast concat demuxer, beats already sum to slot
        lst = str(out) + ".cat.txt"
        open(lst, "w").write("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in parts))
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])
        return
    acc = parts[0]; acc_dur = durof(acc)
    for k in range(1, len(parts)):
        nxt = parts[k]; step = str(out) + f".s{k}.mp4"
        if flags[k]:
            # freeze-pad acc's tail by DISSOLVE so the xfade consumes the pad,
            # not timeline — keeps every beat word-synced (no cumulative shrink)
            fr = str(out) + f".p{k}.png"; run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(acc), "-frames:v", "1", fr])
            pad = str(out) + f".p{k}.mp4"
            run(["ffmpeg", "-y", "-loop", "1", "-i", fr, "-frames:v", str(round(DISSOLVE * FPS) + 1),
                 "-vf", "scale=1920:1080,fps=30,format=yuv420p", "-c:v", "libx264",
                 "-preset", "fast", "-crf", "18", "-an", pad])
            lstp = str(out) + f".p{k}.txt"
            open(lstp, "w").write(f"file '{Path(acc).resolve().as_posix()}'\nfile '{Path(pad).resolve().as_posix()}'\n")
            accp = str(out) + f".ap{k}.mp4"
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lstp, "-c:v", "libx264",
                 "-preset", "fast", "-crf", "18", "-an", accp])
            off = acc_dur  # xfade starts where the real content ends (inside the pad)
            run(["ffmpeg", "-y", "-i", str(accp), "-i", str(nxt), "-filter_complex",
                 f"[0:v][1:v]xfade=transition=fade:duration={DISSOLVE}:offset={off:.3f},format=yuv420p",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", step])
            acc_dur = off + durof(nxt)
        else:
            lst = str(out) + f".c{k}.txt"
            open(lst, "w").write(f"file '{Path(acc).resolve().as_posix()}'\nfile '{Path(nxt).resolve().as_posix()}'\n")
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", step])
            acc_dur += durof(nxt)
        acc = step
    conform_to_slot(acc, slot, out)  # freeze-pad the slack the dissolves ate

def build_scene(plan, seed0):
    sid = plan["scene_id"]; beats = plan["beats"]
    sp = spans(sid, [b["phrase_anchor"] for b in beats])
    scene_diss = plan.get("dissolve", False)
    parts = []; flags = []
    for i, (b, (s, e)) in enumerate(zip(beats, sp), 1):
        span = round(e - s, 2)
        if span < 0.6: continue
        diss = scene_diss or b.get("dissolve_in", False)
        beat = WORK / f"{sid}_b{i}.mp4"
        if beat.exists() and abs(durof(beat) - span) < 0.15:
            parts.append(beat); flags.append(diss); print(f"  {sid} b{i} reuse [{s}-{e}] {span:.1f}s", flush=True); continue
        kf = WORK / f"{sid}_b{i}.png"
        if not author_kf(b["keyframe_prompt"], b.get("sheets", []), kf):
            print(f"  {sid} b{i}: KF FAIL"); continue
        animate(kf, b["motion_prompt"], span, beat, seed0 + i)
        parts.append(beat); flags.append(diss)
        print(f"  {sid} b{i} '{b['label']}' [{s}-{e}] {span:.1f}s{' [diss]' if diss else ''}", flush=True)
    if not parts: return
    out = AI / f"{sid}.mp4"
    stitch(parts, flags, out, round(sp[-1][1], 2))
    hits = video_border_hits(out, 0, durof(out))
    print(f"{sid}: {len(parts)} beats -> {durof(out):.1f}s {'BORDER!' if hits else 'clean'}", flush=True)

def main():
    plans = {p["scene_id"]: p for p in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))}
    want = sys.argv[2:] or list(plans.keys())
    seed = 300
    for sid in want:
        if sid not in plans: print(f"{sid}: no plan"); continue
        seed += 20
        try:
            build_scene(plans[sid], seed)
        except Exception as e:
            print(f"{sid}: SCENE FAILED — {str(e)[:120]}", flush=True)
    print("BEAT EXEC DONE", flush=True)

if __name__ == "__main__":
    main()
