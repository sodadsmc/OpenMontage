"""seg_009 sag v2 (operator: narration says the wall SANK but it only cracked;
water static; too subtle). The wall region now VISIBLY SETTLES DOWNWARD over
each beat (up to ~2% of frame height, eased), hairline cracks draw on, and the
lake gets real lapping/shimmer. $0 deterministic — 'sinks N inches' is a precise
contract grok/Omni drift on."""
import json, os, sys, tempfile
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
FPS = 30
ARRIS = [(225, 935), (250, 900), (320, 850), (420, 790), (540, 720), (700, 640), (896, 560)]
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
sp = be.spans("seg_009", [b["phrase_anchor"] for b in plans["seg_009"]["beats"]])

def ix(poly, y, sy, sx):
    ys = [p[0] for p in poly]; xs = [p[1] for p in poly]
    return float(np.interp(y / sy, ys, xs)) * sx

def crack_path(rs, x0, y0, n=7, step=22):
    pts = [(x0, y0)]
    for _ in range(n):
        x0 += rs.uniform(-step * 0.7, step * 0.7)
        y0 += rs.uniform(step * 0.5, step)
        pts.append((x0, y0))
    return pts

def build(bi, sink_px, seed):
    span = round(sp[bi - 1][1] - sp[bi - 1][0], 2)
    im = Image.open(BE / f"seg_009_b{bi}.png").convert("RGB")
    A = np.asarray(im, float); H, W = A.shape[:2]
    sx, sy = W / 1195.0, H / 896.0
    yy, xx = np.mgrid[0:H, 0:W]
    arris_x = np.array([ix(ARRIS, y, sy, sx) for y in range(H)])[:, None]
    wall = (xx < arris_x - 4 * sx).astype(float)          # wall + outer face/rocks
    wall[:int(H * 0.16)] = 0                              # keep the sky
    wallM = np.asarray(Image.fromarray((wall * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(6)), float) / 255.0
    lake = (xx > arris_x + 10 * sx).astype(float); lake[:int(H * 0.22)] = 0
    lakeM = np.asarray(Image.fromarray((lake * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(5)), float) / 255.0
    rs = np.random.RandomState(seed)
    cracks = [crack_path(rs, rs.uniform(W * 0.18, W * 0.5), rs.uniform(H * 0.35, H * 0.55))
              for _ in range(3)]
    n = round(span * FPS); td = tempfile.mkdtemp()
    ys_ = np.arange(H)
    sink = sink_px * sy
    for i in range(n):
        t = i / FPS; p = t / span
        ease = 0.2 * p + 0.8 * (3 * p * p - 2 * p ** 3)   # creep, never static
        dy = ease * sink
        # settle the wall region DOWN by dy (sample from above)
        src_y = np.clip(yy - dy * wallM, 0, H - 1.001)
        i0 = src_y.astype(int); f = (src_y - i0)
        out = np.zeros_like(A)
        for c in range(3):
            out[..., c] = A[i0, xx, c] * (1 - f) + A[np.minimum(i0 + 1, H - 1), xx, c] * f
        # hairline cracks draw on across the beat
        fr = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(fr)
        reveal = min(1.0, max(0.0, (p - 0.12) / 0.75))
        for ci, pts in enumerate(cracks):
            k = max(2, int(len(pts) * min(1.0, reveal * (1.15 - ci * 0.12))))
            wob = [(x + 1.2 * np.sin(int(t * 3) + j), y) for j, (x, y) in enumerate(pts[:k])]
            d.line(wob, fill=(26, 20, 14), width=2)
        out = np.asarray(fr, float)
        # lake: lap + shimmer (real water life)
        slosh = 3.0 * np.sin(2 * np.pi * 0.3 * t) + 1.4 * np.sin(2 * np.pi * 0.6 * t + 1)
        chop = (0.8 * np.sin(ys_ / 7.0 + t * 4.0) + 0.5 * np.sin(ys_ / 15.0 - t * 2.5))[:, None]
        dx = (slosh + chop) * lakeM
        xm = np.clip(xx + dx, 0, W - 1.001); j0 = xm.astype(int); g = xm - j0
        warp = np.zeros_like(out)
        for c in range(3):
            warp[..., c] = out[yy, j0, c] * (1 - g) + out[yy, np.minimum(j0 + 1, W - 1), c] * g
        out = out * (1 - lakeM[..., None]) + warp * lakeM[..., None]
        shim = 1 + 0.04 * np.sin(ys_ / 8.0 + t * 4.5)[:, None]
        out = out * (1 - lakeM[..., None] * 0.5) + out * shim[..., None] * (lakeM[..., None] * 0.5)
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    clip = BE / f"seg_009_b{bi}.mp4"
    crop_h = int(W * 9 / 16)
    be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png",
            "-vf", f"crop={W - W % 2}:{crop_h}:0:100,scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(clip)])
    print(f"009 b{bi} sag-v2: {be.durof(clip):.2f}s (span {span}, sink {sink_px}px)", flush=True)

import shutil
for bi in (1, 2, 4):
    f = BE / f"seg_009_b{bi}.mp4"
    b_ = BE / f"seg_009_b{bi}.sagv1.bak.mp4"
    if f.exists() and not b_.exists():
        shutil.move(str(f), str(b_))
build(1, 14, 91)
build(2, 18, 92)
build(4, 16, 94)
print("SAG V2 DONE", flush=True)
