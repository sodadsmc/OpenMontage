"""seg_010 b4 rebuild: the warm-white cascade failed the blue-leaning color
mask (0.4% motion). New mask: HSV bright+desaturated, restricted to the
downstream face (left of the ARRIS coping line), sky excluded. $0."""
import json, os, sys, tempfile
import numpy as np
from PIL import Image, ImageFilter
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

BE_D = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
FPS = 30
ARRIS = [(225, 935), (250, 900), (320, 850), (420, 790), (540, 720), (700, 640), (896, 560)]

def interp_x(poly, y):
    ys = [p[0] for p in poly]; xs = [p[1] for p in poly]
    return float(np.interp(y, ys, xs))

plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
sp = be.spans("seg_010", [b["phrase_anchor"] for b in plans["seg_010"]["beats"]])
span = round(sp[3][1] - sp[3][0], 2)
print("b4 span:", span, flush=True)

src = BE_D / "seg_010_b4.png"   # red-line-warped keyframe (promoted earlier)
im = Image.open(src).convert("RGB")
W, H = im.size
A = np.asarray(im, dtype=float)
hsv = np.asarray(im.convert("HSV"), dtype=float)
S, V = hsv[:, :, 1], hsv[:, :, 2]

# cascade: bright + desaturated (white foam, even warm-tinted)
casc = (V > 150) & (S < 70)
# geometric restriction: downstream face only (left of the coping line), sky out
sx = W / 1195.0; sy = H / 896.0
yy, xx = np.mgrid[0:H, 0:W]
arris_x = np.array([interp_x(ARRIS, y / sy) * sx for y in range(H)])
casc &= xx < arris_x[:, None] - 8 * sx        # left of the wall coping
casc[:int(H * 0.20)] = False                  # sky out
print(f"mask px={int(casc.sum())} ({casc.mean()*100:.1f}%)", flush=True)

cm = Image.fromarray((casc * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(5))
M = np.asarray(cm, dtype=float)[..., None] / 255.0

v, period = 110.0, 90.0
n = round(span * FPS)
td = tempfile.mkdtemp()
ys = np.arange(H)
for i in range(n):
    ts = i / FPS
    off = (ts * v) % period
    l1 = np.roll(A, int(off), axis=0)
    l2 = np.roll(A, int(off - period), axis=0)
    w2 = (off % period) / period
    scr = l1 * (1 - w2) + l2 * w2
    out = A * (1 - M) + scr * M
    # lake shimmer right of the coping
    shim = 1 + 0.035 * np.sin(ys / 11.0 + ts * 2.6)[:, None, None]
    lake = (xx > arris_x[:, None] + 30 * sx)[..., None].astype(float)
    lake[:int(H * 0.25)] = 0
    out = out * (1 - lake * 0.5) + out * shim * (lake * 0.5)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")

clip = BE_D / "seg_010_b4.mp4"
crop_h = int(W * 9 / 16)
be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png",
        "-vf", f"crop={W - W % 2}:{crop_h}:0:100,scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(clip)])
print(f"b4 rebuilt: {be.durof(clip):.2f}s (span {span})", flush=True)
