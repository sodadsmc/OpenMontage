"""Polish round part 2: takes for the 11 new keyframes (grok, premiumize host).
QC-gated installs; s003_tail conforms to the 7.43s splice window."""
import os, sys, json, shutil
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
from dotenv import load_dotenv; load_dotenv(r"D:\OpenMontage2\.env")
os.environ.pop("IMAGE_HOST", None)
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from lib import visual_router as vr
from lib.frame_hygiene import zoom_still, motion_floor
from pathlib import Path

BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
def span(sid, i):
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    return round(sp[i-1][1] - sp[i-1][0], 2)

PIN = ("; keep the exact hand-inked graphic-novel style with bold outlines and halftone "
       "shading the whole time, no photorealism, no morphing")
JOBS = [
 ("s003_tail", None, 7.43,
  "the wooden house buckles further apart in the surging black torrent — timbers wrench loose "
  "and are carried off, spray explodes, trees whip in the flood" + PIN),
 ("seg_006_b1", 1, None,
  "slow aerial drift toward the kidney-shaped mountaintop reservoir, wind rippling the dark "
  "brimming water, late light shifting over the rockfill ring; the water stays at the very top" + PIN),
 ("seg_012_b5", 5, None,
  "the heavyset operator types steadily at the console, the CRT flickers softly on his face, "
  "he leans in slightly, small natural movements" + PIN),
 ("seg_012_b6", 6, None,
  "the heavyset operator lifts the clipboard and reads it, glancing between the page and the "
  "glowing screen" + PIN),
 ("seg_024_b3", 3, None,
  "construction time-lapse: the cranes swing quickly, tiny workers hurry along the forms, a "
  "concrete section settles into place on the rising kidney-shaped rim" + PIN),
 ("seg_024_b4", 4, None,
  "construction time-lapse: the crane lowers the tall sensor mast into place while tiny workers "
  "guide it upright on the rim" + PIN),
 ("seg_024_b5", 5, None,
  "the dark water level RISES steadily inside the finished kidney-shaped reservoir while the "
  "spillway carries a smooth sheet of water down its steps; all water flows downhill" + PIN),
 ("seg_004_b1", 1, None,
  "floodwater still tears down the raw scoured channel, white spray bursting over stripped "
  "bedrock, all water rushing strictly downhill" + PIN),
 ("seg_019_b2", 2, None,
  "the dark lake pours violently out through the breach gap, the torrent blasting down the "
  "outer slope, the pool behind the rim visibly shrinking; water strictly downhill" + PIN),
 ("seg_019_b3", 3, None,
  "the white wall of water, rock and shredded trees races DOWN the mountainside, mowing the "
  "forest flat as it widens; everything moves strictly downhill" + PIN),
 ("seg_020_b4", 4, None,
  "the black floodwater races through the bare trees carrying broken timbers past the ruined "
  "house, foam streaking, debris bobbing and rushing by" + PIN),
]
for name, bi, fixed_span, motion in JOBS:
    if bi is not None:
        sid = name.rsplit("_b", 1)[0]
        sp = span(sid, bi)
        out = BE / f"{name}.mp4"
        tag = "prepolish"
    else:
        sp = fixed_span
        out = BE / f"{name}.mp4"
        tag = None
    cand = BE / f"{name}_p2.mp4"
    if not cand.exists():
        clip = None
        for attempt in range(2):
            try:
                raw = BE / f"{name}_p2.raw{attempt}.mp4"
                c = vr._gen_shot_clip(motion + be.MOT, BE / f"{name}.png",
                                      min(6.0, sp), "grok-kie", 5000 + hash(name) % 900 + attempt, raw)
                if c and Path(c).exists():
                    clip = c; break
            except Exception as e:
                print(f"  {name} attempt {attempt+1} failed: {str(e)[:70]}", flush=True)
        if not clip:
            print(f"{name}: FAILED both attempts", flush=True); continue
        have = be.durof(clip); vf = be.NORM
        if have < sp - 0.05:
            vf = f"setpts=PTS*{sp/max(0.1,have):.5f}," + vf
        be.run(["ffmpeg","-y","-i",str(clip),"-t",f"{sp:.3f}","-vf",vf,
                "-c:v","libx264","-preset","fast","-crf","18","-an",str(cand)])
    zv,_,_ = zoom_still(cand); fm, sd, dead = motion_floor(cand)
    if zv == "zoom-still" or dead:
        print(f"{name}: QC REJECT (zoom={zv} frame={fm:.1f} span={sd:.1f})", flush=True); continue
    if out.exists() and tag:
        b_ = BE / f"{name}.{tag}.bak.mp4"
        if not b_.exists(): shutil.move(str(out), str(b_))
    shutil.copy(str(cand), str(out))
    print(f"{name}: INSTALLED {be.durof(out):.2f}s (span {sp}, frame {fm:.1f})", flush=True)
print("TAKES2 DONE", flush=True)
