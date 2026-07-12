"""Taum Sauk assembler: build a render (or per-act preview) from whatever
sources exist per scene — animated ai clips, doc-still push-ins, or storyboard
stills (manim placeholders) — conformed to each scene's audio slot, concatenated,
muxed under the narration, graded once.

  python assemble.py                 # full episode -> renders/taum_sauk_v1.mp4
  python assemble.py --act act_2     # just that act -> renders/preview_<act>.mp4
"""
from __future__ import annotations
import argparse, json, math, os, subprocess, sys
from pathlib import Path

ROOT = Path(r"D:\OpenMontage2")
PROJ = ROOT / "projects" / "taum-sauk-2005"
AI = PROJ / "assets" / "ai_segments"
STILLS = PROJ / "assets" / "stills"
AUDIO = PROJ / "assets" / "audio_v6"
RENDERS = PROJ / "renders"; RENDERS.mkdir(exist_ok=True)
FPS = 30
NORM = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#091327,fps=30,format=yuv420p")

def run(a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(str(a[:3]) + " :: " + r.stderr[-300:])

def dur_of(p):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                        "-of","csv=p=0",str(p)], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def conform_clip(src, target, out):
    """Trim or freeze-pad a video to exactly target seconds (frame-ceiling)."""
    tgt = math.ceil(target * FPS - 1e-6) / FPS
    run(["ffmpeg","-y","-i",str(src),"-t",f"{tgt:.3f}","-vf",NORM,
         "-c:v","libx264","-preset","fast","-crf","18","-an",str(out)])
    have = dur_of(out)
    if have < tgt - 0.02:
        fr = str(out)+".png"; run(["ffmpeg","-y","-sseof","-0.1","-i",str(out),"-frames:v","1",fr])
        pad = str(out)+".pad.mp4"
        run(["ffmpeg","-y","-loop","1","-i",fr,"-frames:v",str(round((tgt-have)*FPS)+1),
             "-vf",f"scale=1920:1080,fps={FPS},format=yuv420p","-c:v","libx264",
             "-preset","fast","-crf","18","-an",pad])
        lst=str(out)+".txt"; open(lst,"w").write(f"file '{out}'\nfile '{pad}'\n")
        j=str(out)+".j.mp4"
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",lst,"-c:v","libx264",
             "-preset","fast","-crf","18","-an",j]); os.replace(j,out)

def still_push(src, target, out, doc=False):
    """Ken-Burns push on a still (subtle for docs, slow drift for placeholders)."""
    n=round(target*FPS)
    z = "1+0.06*on/NF" if not doc else "1.0+0.04*on/NF"
    vf=(f"scale=3840:2160:flags=lanczos,zoompan=z='{z}':d={n}:x='iw/2-(iw/zoom/2)'"
        f":y='ih/2-(ih/zoom/2)':s=1920x1080:fps={FPS},format=yuv420p").replace("NF",str(n))
    run(["ffmpeg","-y","-loop","1","-i",str(src),"-frames:v",str(n),"-vf",vf,
         "-c:v","libx264","-preset","fast","-crf","18","-an",str(out)])

def placeholder(sid, target, out):
    """Navy card for a not-yet-built scene (manim diagrams)."""
    from PIL import Image, ImageDraw, ImageFont
    im=Image.new("RGB",(1920,1080),(9,19,39)); d=ImageDraw.Draw(im)
    try: f1=ImageFont.truetype("arialbd.ttf",70); f2=ImageFont.truetype("arialbd.ttf",40)
    except OSError: f1=f2=ImageFont.load_default()
    d.text((960,480),sid,font=f1,fill=(243,165,65),anchor="mm")
    d.text((960,600),"DIAGRAM — TO BUILD",font=f2,fill=(224,217,197),anchor="mm")
    p=str(out)+".png"; im.save(p); still_push(p,target,out)

DOC={"seg_011","seg_013","seg_023"}
MANIM={"seg_005","seg_008","seg_015","seg_016","seg_026"}

def build(act=None):
    dm=json.loads((PROJ/"artifacts"/"duration_map_v6.json").read_text(encoding="utf-8"))
    segs=[s for s in dm["segments"] if (act is None or s["act"]==act)]
    tmp=RENDERS/("_asm_"+(act or "full")); tmp.mkdir(exist_ok=True)
    parts=[]
    for s in segs:
        # conform to the TOTAL slot (audio + silence); clips freeze-hold through
        # the silence tail so the video covers the narration track exactly
        sid=s["id"]; target=s["total_duration_s"]; out=tmp/f"{sid}.mp4"
        clip=AI/f"{sid}.mp4"; still=STILLS/f"{sid}.png"
        if clip.exists() and clip.stat().st_size>50000:
            conform_clip(clip, target, out)
        elif sid in MANIM or not still.exists():
            placeholder(sid, target, out)
        elif sid in DOC:
            still_push(still, target, out, doc=True)
        else:
            still_push(still, target, out)
        parts.append(out); print(f"  {sid}: {'clip' if clip.exists() else ('doc' if sid in DOC else 'manim' if sid in MANIM else 'still')}", flush=True)
    # concat video
    lst=tmp/"concat.txt"; lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    video=tmp/"video.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),"-c:v","libx264",
         "-preset","fast","-crf","18","-an",str(video)])
    # audio: full narration for episode, or concat this act's seg mp3s+silence for a preview
    if act is None:
        audio=AUDIO/"narration_v6.mp3"
    else:
        aparts=[]
        for s in segs:
            aparts.append(AUDIO/f"{s['id']}.mp3")
            if s["silence_after_s"]>0:
                sil=tmp/f"sil_{s['id']}.mp3"
                run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo",
                     "-t",str(s["silence_after_s"]),"-c:a","libmp3lame","-b:a","128k",str(sil)])
                aparts.append(sil)
        alst=tmp/"aud.txt"; alst.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in aparts))
        audio=tmp/"audio.mp3"
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(alst),"-c:a","libmp3lame","-b:a","128k",str(audio)])
    out=RENDERS/(f"preview_{act}.mp4" if act else "taum_sauk_v1.mp4")
    run(["ffmpeg","-y","-i",str(video),"-i",str(audio),"-c:v","copy","-c:a","aac",
         "-b:a","192k","-shortest",str(out)])
    print(f"OUTPUT: {out} ({dur_of(out):.1f}s)")
    return out

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--act",default=None); a=ap.parse_args()
    build(a.act)
