"""The 3 beats grok couldn't land tonight (100% timeout). All are quiet/aftermath
moments -> deterministic environmental life, $0, no flaky-provider dependency.
  seg_022_b4  phone waiting: CRT screen glow-breath + lamp flicker (held tension)
  seg_021_b1  found in shallows: foreground water shimmer + rising breath/mist
  seg_020_b4  swept away: residual floodwater foam drifts downstream + cold mist
No wrap-scroll (reads as flashing) — one-way non-wrapping drift + additive wisps."""
import json, os, sys, tempfile
import numpy as np
from PIL import Image, ImageFilter
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

BE_D = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
FPS = 30
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
def span(sid, i):
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    return round(sp[i-1][1] - sp[i-1][0], 2)

def soft(W, H, box, blur):
    m = Image.new("L", (W, H), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(m).rectangle(box, fill=255)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(blur)), float)[..., None] / 255.0

def wisps(W, H, seed, n, band, rise, drift, r_range):
    """Deterministic drifting mist wisps -> list of (x0,y0,r,phase,speed)."""
    rs = np.random.RandomState(seed)
    out = []
    for _ in range(n):
        x0 = rs.uniform(0, W); y0 = rs.uniform(*band)
        r = rs.uniform(*r_range); ph = rs.uniform(0, 6.28)
        out.append((x0, y0, r, ph, rs.uniform(0.6, 1.4)))
    return out

def draw_wisps(canvas, ws, t, W, H, rise, drift, amp):
    yy, xx = np.mgrid[0:H, 0:W]
    add = np.zeros((H, W), float)
    for (x0, y0, r, ph, sp) in ws:
        cx = (x0 + drift * t * sp) % (W + 200) - 100
        cy = y0 - rise * t * sp
        fade = max(0.0, min(1.0, np.sin(0.5 * (t * sp + ph)) * 0.5 + 0.5))
        g = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
        add += g * fade
    add = np.asarray(Image.fromarray(np.clip(add * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4)), float) / 255.0
    return canvas + amp * add[..., None] * np.array([150.0, 158.0, 170.0])

def encode(td, n, out, crop_from=None):
    vf = be.NORM
    if crop_from:
        W, H = crop_from
        ch = int((W - W % 2) * 9 / 16)
        y = max(0, (H - ch) // 2)
        vf = f"crop={W-W%2}:{ch}:0:{y}," + be.NORM
    be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png", "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])

# ---- seg_022_b4: CRT glow-breath + lamp flicker (2.6s) ---------------------
def b_022_4():
    sp = span("seg_022", 4)
    A = np.asarray(Image.open(BE_D / "seg_022_b4.png").convert("RGB"), float)
    H, W = A.shape[:2]
    screen = soft(W, H, (392, 110, 720, 452), 22)
    warm = soft(W, H, (330, 380, 1120, 700), 60)   # desk lit by the screen
    n = round(sp * FPS); td = tempfile.mkdtemp()
    for i in range(n):
        t = i / FPS
        crt = 0.05 * np.sin(2*np.pi*0.7*t) + 0.02*np.sin(2*np.pi*3.3*t)  # slow breath + faint refresh
        out = A * (1 + screen * crt + warm * (0.02*np.sin(2*np.pi*0.7*t)))
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    encode(td, n, BE_D / "seg_022_b4.mp4", crop_from=(W, H))
    print(f"022 b4: {be.durof(BE_D/'seg_022_b4.mp4'):.2f}s (span {sp})", flush=True)

# ---- seg_021_b1: shallows shimmer + rising breath/mist (5.38s) --------------
def b_021_1():
    sp = span("seg_021", 1)
    A = np.asarray(Image.open(BE_D / "seg_021_b1.png").convert("RGB"), float)
    H, W = A.shape[:2]
    water = soft(W, H, (0, 690, W, H), 20)          # foreground shallows
    ws = wisps(W, H, 21, 10, (430, 560), rise=9, drift=6, r_range=(26, 60))
    breath = wisps(W, H, 77, 4, (455, 500), rise=14, drift=3, r_range=(10, 20))
    n = round(sp * FPS); td = tempfile.mkdtemp()
    ys = np.arange(H)
    for i in range(n):
        t = i / FPS
        shim = 0.028*np.sin(ys/9.0 + t*2.1)[:, None] + 0.012*np.sin(ys/21.0 - t*1.4)[:, None]
        out = A * (1 + water * shim[..., None])
        out = draw_wisps(out, ws, t, W, H, 9, 6, 0.10)
        out = draw_wisps(out, breath, t, W, H, 14, 3, 0.13)
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    encode(td, n, BE_D / "seg_021_b1.mp4", crop_from=(W, H))
    print(f"021 b1: {be.durof(BE_D/'seg_021_b1.mp4'):.2f}s (span {sp})", flush=True)

# ---- seg_020_b4: residual foam drifts downstream + cold mist (6.18s) --------
def b_020_4():
    sp = span("seg_020", 4)
    im = Image.open(BE_D / "seg_020_b4.png").convert("RGB")
    A = np.asarray(im, float)
    H, W = A.shape[:2]
    hsv = np.asarray(im.convert("HSV"), float)
    foam = ((hsv[:, :, 2] > 155) & (hsv[:, :, 1] < 90)).astype(float)
    fmask = np.zeros((H, W), float); fmask[470:600] = 1.0        # mid-ground water band
    foam = foam * fmask
    foam = np.asarray(Image.fromarray((foam*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), float)[..., None] / 255.0
    ws = wisps(W, H, 20, 9, (430, 560), rise=7, drift=-10, r_range=(30, 70))
    n = round(sp * FPS); td = tempfile.mkdtemp()
    for i in range(n):
        t = i / FPS
        shift = int(-22 * t)                                     # one-way downstream (leftward), no wrap
        rolled = np.roll(A, shift, axis=1)
        if shift < 0:
            rolled[:, shift:] = A[:, shift:]                     # don't wrap the tail back in
        out = A * (1 - foam) + rolled * foam
        out = draw_wisps(out, ws, t, W, H, 7, -10, 0.09)
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    encode(td, n, BE_D / "seg_020_b4.mp4", crop_from=(W, H))
    print(f"020 b4: {be.durof(BE_D/'seg_020_b4.mp4'):.2f}s (span {sp})", flush=True)

b_022_4()
b_021_1()
b_020_4()
print("DET LAST3 DONE", flush=True)
