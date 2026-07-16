"""Act 4 pre-batch: the $0 beats + the two FLF takes, installed at executor
paths so beat_exec reuses them and only animates the grok beats.
  seg_017_b1  deterministic: pump-two indicator bank dies + console glow breathes
  seg_017_b2  deterministic: gauge glow breathing (needle holds its lie steady)
  seg_018_b4  reuse: act-3 NW-overflow FLF full-flow tail, night-graded
  seg_022_b1/b2  the call-list card split at the beat boundary
  seg_017_b5  FLF: nightified tip-top -> first-spill (approved same-camera pair)
  seg_019_b2  FLF: breach pouring -> pool drained to mud (deterministic endpoint)
"""
import json, os, shutil, sys, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
from dotenv import load_dotenv; load_dotenv(r"D:\OpenMontage2\.env")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

BE_D = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
FPS = 30
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
def span(sid, i):
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    return round(sp[i-1][1] - sp[i-1][0], 2)

def soft_box(W, H, box, blur=18):
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rectangle(box, fill=255)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(blur)), float)[..., None] / 255.0

def encode(td, n, out, crop=None):
    vf = (f"crop={crop}," if crop else "") + be.NORM
    be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png",
            "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])

# ---- seg_017_b1: indicator bank dies (7.2s) --------------------------------
def build_017_b1():
    sp = span("seg_017", 1)
    A = np.asarray(Image.open(BE_D / "seg_017_b1.png").convert("RGB"), float)
    H, W = A.shape[:2]
    dim = soft_box(W, H, (575, 332, 705, 368), blur=8)
    glows = [soft_box(W, H, b) for b in
             [(100, 460, 290, 660), (995, 375, 1135, 455), (1140, 385, 1250, 575),
              (855, 385, 915, 445), (705, 332, 840, 368)]]
    n = round(sp * FPS)
    td = tempfile.mkdtemp()
    T_OFF = 2.2
    for i in range(n):
        t = i / FPS
        out = A.copy()
        # stronger, always-on console breath so the post-dim hold stays alive
        for k, g in enumerate(glows):
            out = out * (1 + g * 0.06 * np.sin(2 * np.pi * 0.55 * t + k * 1.4)
                             + g * 0.025 * np.sin(2 * np.pi * 2.3 * t + k))
        if t < T_OFF:
            lvl = 1.0
        else:
            p = min(1.0, (t - T_OFF) / 1.2)
            flick = 0.75 if (0.10 < t - T_OFF < 0.16 or 0.30 < t - T_OFF < 0.34) else 1.0
            lvl = (1.0 - 0.78 * (3 * p * p - 2 * p ** 3)) * flick
        out = out * (1 - dim) + out * lvl * dim
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    encode(td, n, BE_D / "seg_017_b1.mp4")
    print(f"017 b1 det: {be.durof(BE_D / 'seg_017_b1.mp4'):.2f}s (span {sp})", flush=True)

# ---- seg_017_b2: gauge glow breathing (2.69s) ------------------------------
def build_017_b2():
    sp = span("seg_017", 2)
    im = Image.open(BE_D / "seg_017_b2.png").convert("RGB")
    A = np.asarray(im, float)
    hsv = np.asarray(im.convert("HSV"), float)
    warm = ((hsv[:, :, 2] > 150) & (hsv[:, :, 1] < 140)).astype(float)
    warm = np.asarray(Image.fromarray((warm * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)), float)[..., None] / 255.0
    n = round(sp * FPS)
    td = tempfile.mkdtemp()
    for i in range(n):
        t = i / FPS
        out = A * (1 + warm * (0.028 * np.sin(2 * np.pi * 0.5 * t) + 0.012 * np.sin(2 * np.pi * 1.3 * t + 1.1)))
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    encode(td, n, BE_D / "seg_017_b2.mp4")
    print(f"017 b2 det: {be.durof(BE_D / 'seg_017_b2.mp4'):.2f}s (span {sp})", flush=True)

# ---- seg_018_b4: act-3 FLF tail night-graded (1.64s) -----------------------
def build_018_b4():
    sp = span("seg_018", 4)
    src = BE_D / "s10_b3_flf.mp4"
    d = be.durof(src)
    be.run(["ffmpeg", "-y", "-ss", f"{max(0, d - sp - 0.05):.2f}", "-i", str(src), "-t", f"{sp:.2f}",
            "-vf", "colorchannelmixer=rr=0.40:gg=0.46:bb=0.62,eq=brightness=0.02," + be.NORM,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(BE_D / "seg_018_b4.mp4")])
    print(f"018 b4 reuse: {be.durof(BE_D / 'seg_018_b4.mp4'):.2f}s (span {sp})", flush=True)

# ---- seg_022 b1/b2: card split ---------------------------------------------
def build_022_card():
    s1, s2 = span("seg_022", 1), span("seg_022", 2)
    card = BE_D / "seg_022_card.mp4"
    be.run(["ffmpeg", "-y", "-i", str(card), "-t", f"{s1:.2f}", "-vf", be.NORM,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(BE_D / "seg_022_b1.mp4")])
    be.run(["ffmpeg", "-y", "-ss", f"{s1:.2f}", "-i", str(card), "-t", f"{s2:.2f}", "-vf", be.NORM,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(BE_D / "seg_022_b2.mp4")])
    print(f"022 card: b1 {be.durof(BE_D / 'seg_022_b1.mp4'):.2f}s b2 {be.durof(BE_D / 'seg_022_b2.mp4'):.2f}s", flush=True)

# ---- seg_017_b5 FLF: tip-top -> first-spill --------------------------------
def build_017_b5():
    from lib.flf import flf_beat
    sp = span("seg_017", 5)
    a = Image.open(BE_D / "seg_017_b4.png").convert("RGB")
    b = Image.open(BE_D / "seg_017_b5.png").convert("RGB")
    W, H = a.size
    ch = int((W - W % 2) * 9 / 16)
    box = (0, 100, W - W % 2, 100 + ch)
    a.crop(box).save(BE_D / "s17_b5_start.png")
    b.crop(box).save(BE_D / "s17_b5_end.png")
    prompt = ("Hand-inked night dam, locked camera. The black lake sits flush at the very top of the "
              "concrete wall. A first thin tongue of water slips over the lip at the low panel and runs "
              "DOWN the outer face, thickening slightly. All water flows strictly downward; the lake "
              "level does NOT drop; the wall never moves or morphs; no people.")
    out = flf_beat(BE_D / "s17_b5_start.png", prompt, sp, BE_D / "s17_b5_flf.mp4",
                   derive=lambda s, o: shutil.copy(str(BE_D / "s17_b5_end.png"), str(o)),
                   mode="std", aspect_ratio="16:9")
    if out:
        be.run(["ffmpeg", "-y", "-i", str(out), "-vf", be.NORM,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(BE_D / "seg_017_b5.mp4")])
        print(f"017 b5 FLF: {be.durof(BE_D / 'seg_017_b5.mp4'):.2f}s (span {sp})", flush=True)
    else:
        print("017 b5 FLF FAILED", flush=True)

# ---- seg_019_b2 FLF: pouring -> pool drained to mud ------------------------
def build_019_b2():
    from lib.flf import flf_beat
    sp = span("seg_019", 2)
    src = Image.open(BE_D / "seg_019_b2.png").convert("RGB")   # portrait 843x1264
    box = (0, 330, 842, 804)                                    # 842x474 = 16:9 on the breach
    start = src.crop(box)
    start.save(BE_D / "s19_b2_start.png")
    A = np.asarray(start, float)
    H, W = A.shape[:2]
    # pool region (crop coords): behind-rim strip + notch-mouth pool -> matte mud
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon([(250, 8), (830, 8), (830, 100), (620, 190), (400, 190), (290, 110)], fill=255)
    M = np.asarray(m.filter(ImageFilter.GaussianBlur(9)), float)[..., None] / 255.0
    gray = (A @ np.array([0.299, 0.587, 0.114]))[..., None]
    mud = gray * 0.42 + np.array([74.0, 64.0, 52.0])
    end = A * (1 - M) + mud * M
    Image.fromarray(np.clip(end, 0, 255).astype(np.uint8)).save(BE_D / "s19_b2_end.png")
    prompt = ("Hand-inked pre-dawn aerial, locked camera. The breached reservoir rim: black water pours "
              "hard through the broken notch and blasts downhill as a white torrent. Over the clip the "
              "dark pool behind the rim visibly SHRINKS and drains away to bare mud — the lake is leaving "
              "the mountain through the gap. The torrent keeps flowing strictly downhill. The rim and "
              "terrain never move or morph.")
    out = flf_beat(BE_D / "s19_b2_start.png", prompt, sp, BE_D / "s19_b2_flf.mp4",
                   derive=lambda s, o: shutil.copy(str(BE_D / "s19_b2_end.png"), str(o)),
                   mode="std", aspect_ratio="16:9")
    if out:
        be.run(["ffmpeg", "-y", "-i", str(out), "-vf", be.NORM,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(BE_D / "seg_019_b2.mp4")])
        print(f"019 b2 FLF: {be.durof(BE_D / 'seg_019_b2.mp4'):.2f}s (span {sp})", flush=True)
    else:
        print("019 b2 FLF FAILED", flush=True)

build_017_b1()
build_017_b2()
build_018_b4()
build_022_card()
build_017_b5()
build_019_b2()
print("ZERO+FLF DONE", flush=True)
