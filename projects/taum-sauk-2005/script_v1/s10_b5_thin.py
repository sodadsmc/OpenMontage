"""seg_010 b5 'Saved by hand': grok timed out twice and the push fallback is
banned (Ken Burns). Deterministic build instead: cascade scroll whose velocity
AND moving-layer weight decay over the beat — the spillover visibly thins and
slows to a trickle. Lake stays flush with the coping (keyframe already
red-line-warped). $0."""
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
span = round(sp[4][1] - sp[4][0], 2)
print("b5 span:", span, flush=True)

src = BE_D / "seg_010_b5.png"
im = Image.open(src).convert("RGB")
W, H = im.size
A = np.asarray(im, dtype=float)
hsv = np.asarray(im.convert("HSV"), dtype=float)
S, V = hsv[:, :, 1], hsv[:, :, 2]
casc = (V > 150) & (S < 70)
sx = W / 1195.0; sy = H / 896.0
yy, xx = np.mgrid[0:H, 0:W]
arris_x = np.array([interp_x(ARRIS, y / sy) * sx for y in range(H)])
casc &= xx < arris_x[:, None] - 8 * sx
casc[:int(H * 0.20)] = False
print(f"mask px={int(casc.sum())} ({casc.mean()*100:.1f}%)", flush=True)

cm = Image.fromarray((casc * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(5))
M0 = np.asarray(cm, dtype=float)[..., None] / 255.0

period = 90.0
n = round(span * FPS)
td = tempfile.mkdtemp()
ys = np.arange(H)
phase = 0.0
for i in range(n):
    ts = i / FPS
    p = ts / span                      # 0 → 1 across the beat
    ease = 1 - 0.72 * (3 * p * p - 2 * p * p * p)   # smoothstep decay to 28%
    v = 110.0 * ease                   # cascade slows...
    phase += v / FPS
    Mw = M0 * (0.4 + 0.6 * ease)       # ...and thins toward the still plate
    off = phase % period
    l1 = np.roll(A, int(off), axis=0)
    l2 = np.roll(A, int(off - period), axis=0)
    w2 = (off % period) / period
    scr = l1 * (1 - w2) + l2 * w2
    out = A * (1 - Mw) + scr * Mw
    shim = 1 + 0.03 * np.sin(ys / 11.0 + ts * 2.2)[:, None, None]
    lake = (xx > arris_x[:, None] + 30 * sx)[..., None].astype(float)
    lake[:int(H * 0.25)] = 0
    out = out * (1 - lake * 0.5) + out * shim * (lake * 0.5)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")

clip = BE_D / "seg_010_b5.mp4"
crop_h = int(W * 9 / 16)
be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png",
        "-vf", f"crop={W - W % 2}:{crop_h}:0:100,scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(clip)])
print(f"b5 built: {be.durof(clip):.2f}s (span {span})", flush=True)
