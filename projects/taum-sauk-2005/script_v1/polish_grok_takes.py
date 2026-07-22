"""Polish takes on recovered grok (default image host = premiumize).
All from EXISTING ink keyframes; installs pass zoom_still + motion_floor gates."""
import os, sys, json
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
from dotenv import load_dotenv; load_dotenv(r"D:\OpenMontage2\.env")
os.environ.pop("IMAGE_HOST", None)
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from lib import visual_router as vr
from lib.frame_hygiene import zoom_still, motion_floor
from pathlib import Path
import shutil

BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
def span(sid, i):
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    return round(sp[i-1][1] - sp[i-1][0], 2)

STYLE_PIN = ("; keep the exact hand-inked graphic-novel illustration style with bold outlines and "
             "halftone shading the whole time, no photorealism, no morphing")
JOBS = [
 ("seg_004", 2, "slow smooth pan left-to-right across the empty night control room, CRT glow "
               "flickering softly on the dead consoles, dust motes in the beams" + STYLE_PIN, "pan"),
 ("seg_004", 4, "very slow drift forward over the silent debris field at dawn, thin cold mist "
               "sliding between the shattered trees" + STYLE_PIN, "drift"),
 ("seg_021", 1, "rescuers in heavy gear step slowly and carefully over the tangled flood debris "
               "and fallen trunks, carrying the wrapped children toward the camera, breath fogging "
               "in the freezing first light, gentle handheld sway" + STYLE_PIN, "omni-redo"),
 ("seg_021", 2, "the kneeling rescuer gently lifts the small child up out of the icy shallow "
               "water into his arms, the child holding on, ripples spreading, breath fogging" + STYLE_PIN, "omni-redo"),
 ("seg_021", 3, "the rescuer carries the small wrapped child out of the shallow water toward "
               "safety, stepping carefully, first light" + STYLE_PIN, "omni-redo"),
 ("seg_025", 2, "the small distant swimmers wade and splash in the bright water, one pushes off "
               "a rock and drifts, sun sparkling on the moving surface, foliage swaying" + STYLE_PIN, "omni-redo"),
]
for sid, bi, motion, tag in JOBS:
    sp = span(sid, bi)
    out = BE / f"{sid}_b{bi}.mp4"
    cand = BE / f"{sid}_b{bi}_grok_new.mp4"
    if cand.exists():
        print(f"{sid} b{bi}: candidate exists, skip gen", flush=True)
    else:
        clip = None
        for attempt in range(2):
            try:
                raw = BE / f"{sid}_b{bi}_polish.raw{attempt}.mp4"
                c = vr._gen_shot_clip(motion + be.MOT, BE / f"{sid}_b{bi}.png",
                                      min(6.0, sp), "grok-kie", 4000 + bi * 7 + attempt, raw)
                if c and Path(c).exists():
                    clip = c; break
            except Exception as e:
                print(f"  {sid} b{bi} attempt {attempt+1} failed: {str(e)[:70]}", flush=True)
        if not clip:
            print(f"{sid} b{bi}: FAILED both attempts", flush=True); continue
        have = be.durof(clip); vf = be.NORM
        if have < sp - 0.05:
            vf = f"setpts=PTS*{sp/max(0.1,have):.5f}," + vf
        be.run(["ffmpeg","-y","-i",str(clip),"-t",f"{sp:.3f}","-vf",vf,
                "-c:v","libx264","-preset","fast","-crf","18","-an",str(cand)])
    zv, _, _ = zoom_still(cand)
    fm, sd, dead = motion_floor(cand)
    if zv == "zoom-still" or dead:
        print(f"{sid} b{bi}: QC REJECT (zoom={zv}, frame={fm:.1f}, span={sd:.1f}) — kept old", flush=True)
        continue
    b_ = BE / f"{sid}_b{bi}.{tag}.bak.mp4"
    if out.exists() and not b_.exists():
        shutil.move(str(out), str(b_))
    shutil.copy(str(cand), str(out))
    print(f"{sid} b{bi}: INSTALLED {be.durof(out):.2f}s (span {sp}, frame {fm:.1f})", flush=True)
print("GROK TAKES DONE", flush=True)
