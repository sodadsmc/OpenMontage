"""Act-4 polish (operator round, grok down): richer deterministic motion on the
three screen/water beats grok can't do well anyway.
  seg_017_b1  control room: CRTs scroll, indicator bank dances, ALARM + pump-2
              light dies on 'shuts itself down' (t~4.2)
  seg_017_b2  gauge red-lines: needle climbs into a red danger arc, quivering
  seg_017_b4  brimming lake: sloshes back-and-forth, wind chop, driving rain
No wrap-scroll of whole textures (reads as flashing); slosh = smooth sinusoid,
rain = many small elements."""
import json, os, sys, tempfile
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
from scipy import ndimage
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import beat_exec as be
from pathlib import Path

BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
FPS = 30
plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
def span(sid, i):
    sp = be.spans(sid, [b["phrase_anchor"] for b in plans[sid]["beats"]])
    return round(sp[i-1][1] - sp[i-1][0], 2)

def enc(td, n, out, W, H):
    ch = int((W - W % 2) * 9 / 16); y = max(0, (H - ch) // 2)
    vf = (f"crop={W-W%2}:{ch}:0:{y}," if abs(W/H - 16/9) > 0.02 else "") + be.NORM
    be.run(["ffmpeg","-y","-framerate",str(FPS),"-i",f"{td}/f%05d.png","-vf",vf,
            "-c:v","libx264","-preset","fast","-crf","18","-an",str(out)])

# ============ seg_017_b1: control room alive + alarm ========================
def b1_controlroom():
    sp = span("seg_017", 1); T_ALARM = 4.2
    im = Image.open(BE / "seg_017_b1.png").convert("RGB")
    A = np.asarray(im, float); H, W = A.shape[:2]
    hsv = np.asarray(im.convert("HSV"), float)
    warm = (hsv[:, :, 2] > 145) & (hsv[:, :, 1] < 165) & (hsv[:, :, 0] > 8) & (hsv[:, :, 0] < 60)
    lbl, nlab = ndimage.label(warm)
    comps = []
    for k in range(1, nlab + 1):
        ys, xs = np.where(lbl == k)
        if len(xs) < 120: continue
        comps.append(dict(k=k, cx=xs.mean(), cy=ys.mean(), area=len(xs),
                          y0=ys.min(), y1=ys.max(), big=len(xs) > 2500))
    # pump-2 light: a small warm component in the indicator bank (row y 320-380)
    bank = [c for c in comps if 320 <= c["cy"] <= 385 and not c["big"]]
    pump2 = min(bank, key=lambda c: abs(c["cx"] - 690)) if bank else None
    warmf = np.asarray(Image.fromarray((warm*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), float)/255.0
    # per-component phase for independent flicker
    rs = np.random.RandomState(17)
    ph = {c["k"]: rs.uniform(0, 6.28) for c in comps}
    yy = np.arange(H)[:, None]
    n = round(sp * FPS); td = tempfile.mkdtemp()
    # alarm red mask over the whole panel wall (upper 2/3)
    alarm_reg = np.zeros((H, W), float); alarm_reg[:int(H*0.72)] = 1.0
    alarm_reg = np.asarray(Image.fromarray((alarm_reg*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(40)), float)/255.0
    for i in range(n):
        t = i / FPS
        out = A.copy()
        flick = np.ones((H, W), float)
        for c in comps:
            m = (lbl == c["k"])
            f = 1 + 0.10*np.sin(2*np.pi*(1.1 if c["big"] else 3.2)*t + ph[c["k"]])
            if not c["big"]:
                f += 0.14*np.sin(2*np.pi*5.5*t + ph[c["k"]]*2)  # small lights 'dance'
            flick = np.where(m, f, flick)
        out = out * (1 - warmf[..., None]) + out * flick[..., None] * warmf[..., None]
        # scrolling scanline band on the big screens
        for c in comps:
            if not c["big"]: continue
            m = (lbl == c["k"])
            band = 1 + 0.22*np.exp(-((yy - (c["y0"] + ((t*90 + c["cx"]) % max(1, c["y1"]-c["y0"]))))**2)/(2*10**2))
            out = np.where(m[..., None], np.clip(out*band[..., None], 0, 255), out)
        # ALARM
        if t >= T_ALARM:
            a = min(1.0, (t - T_ALARM)/0.35)
            beat = 0.5 + 0.5*np.sin(2*np.pi*2.4*(t - T_ALARM))
            red = np.array([225.0, 38.0, 28.0])
            amt = a * (0.16 + 0.34*beat) * alarm_reg[..., None]
            out = out*(1 - amt) + red*amt
            # the indicator bank flashes red in sync (small warm lights in the bank row)
            for c in comps:
                if not c["big"] and 315 <= c["cy"] <= 390:
                    m = (lbl == c["k"])
                    rb = a*beat
                    out = np.where(m[..., None], out*(1-rb) + red*rb, out)
            if pump2 is not None:                       # pump-2 light goes dark & stays dark
                m = (lbl == pump2["k"])
                out = np.where(m[..., None], out*0.18, out)
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    enc(td, n, BE / "seg_017_b1.mp4", W, H)
    print(f"017 b1 controlroom+alarm: {be.durof(BE/'seg_017_b1.mp4'):.2f}s (span {sp}, {len(comps)} screens, pump2={'y' if pump2 else 'n'})", flush=True)

# ============ seg_017_b2: gauge red-lines ==================================
def b2_gauge():
    sp = span("seg_017", 2)
    im = Image.open(BE / "seg_017_b2.png").convert("RGB")
    A0 = np.asarray(im, float); H, W = A0.shape[:2]
    piv = np.array([590.0, 432.0]); L = 172.0
    # erase baked needle: cover a generous cone around it with locally-sampled
    # dial cream, feathered, so no ghost highlight remains
    base = Image.fromarray(A0.astype(np.uint8)).copy()
    erase = Image.new("L", (W, H), 0)
    ImageDraw.Draw(erase).polygon([(piv[0]-16, piv[1]+6), (piv[0]+16, piv[1]+6),
                                   (610, 250), (585, 250)], fill=255)
    erase = erase.filter(ImageFilter.GaussianBlur(3))
    EM = np.asarray(erase, float)[..., None]/255.0
    # horizontal smear inpaint: downscale x then back up smears the thin needle
    # into the surrounding cream, matching the dial's local shading exactly
    im0 = Image.fromarray(A0.astype(np.uint8))
    smear = np.asarray(im0.resize((max(1, W//22), H), Image.BILINEAR).resize((W, H), Image.BILINEAR), float)
    A = A0*(1-EM) + smear*EM
    # clean red danger band (filled annular sector, upper-right)
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bb = [piv[0]-166, piv[1]-166, piv[0]+166, piv[1]+166]
    bb2 = [piv[0]-140, piv[1]-140, piv[0]+140, piv[1]+140]
    bd.pieslice(bb, -74, -32, fill=(198, 40, 30, 235))
    bd.pieslice(bb2, -74, -32, fill=(0, 0, 0, 0))
    A = np.asarray(Image.alpha_composite(Image.fromarray(A.astype(np.uint8)).convert("RGBA"), band).convert("RGB"), float)
    crt = np.zeros((H, W), float); crt[270:490, 915:1105] = 1.0
    crt = np.asarray(Image.fromarray((crt*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(8)), float)/255.0
    a_start, a_end = np.radians(-88), np.radians(-42)
    n = round(sp * FPS); td = tempfile.mkdtemp()
    for i in range(n):
        t = i / FPS; p = t/sp
        ease = 3*p*p - 2*p**3
        jit = np.radians(3.0)*np.sin(2*np.pi*7*t) * (0.4 + 0.6*ease)
        a_ = a_start + (a_end - a_start)*ease + jit
        tip = piv + L*np.array([np.cos(a_), np.sin(a_)])
        fr = Image.fromarray(A.astype(np.uint8)).copy(); dd = ImageDraw.Draw(fr)
        dd.line([tuple(piv), tuple(tip)], fill=(20, 16, 12), width=5)
        dd.ellipse([piv[0]-11, piv[1]-11, piv[0]+11, piv[1]+11], fill=(30, 24, 18))
        out = np.asarray(fr, float)
        # CRT flicker
        out = out * (1 + crt[..., None]*0.06*np.sin(2*np.pi*4*t))
        # red pulse once the needle is in the danger arc
        redz = max(0.0, (a_ - np.radians(-64)) / np.radians(22))
        if redz > 0:
            beat = 0.5 + 0.5*np.sin(2*np.pi*2.5*t)
            g = np.zeros((H, W), float)
            gd = ImageDraw.Draw(Image.fromarray((g).astype(np.uint8)))
            yy, xx = np.mgrid[0:H, 0:W]
            gm = np.exp(-(((xx-590)**2 + (yy-360)**2)/(2*180**2)))
            out = out*(1 - min(1,redz)*beat*0.16*gm[..., None]) + np.array([210,45,35])*(min(1,redz)*beat*0.16*gm[..., None])
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    enc(td, n, BE / "seg_017_b2.mp4", W, H)
    print(f"017 b2 gauge redline: {be.durof(BE/'seg_017_b2.mp4'):.2f}s (span {sp})", flush=True)

# ============ seg_017_b4: slosh + wind + rain ==============================
def b4_slosh():
    sp = span("seg_017", 4)
    im = Image.open(BE / "seg_017_b4.png").convert("RGB")
    A = np.asarray(im, float); H, W = A.shape[:2]
    ARRIS = [(225,935),(250,900),(320,850),(420,790),(540,720),(700,640),(896,560)]
    sx, sy = W/1195.0, H/896.0
    def ix(y):
        ys=[p[0] for p in ARRIS]; xs=[p[1] for p in ARRIS]; return float(np.interp(y/sy, ys, xs))*sx
    yy, xx = np.mgrid[0:H, 0:W]
    arris = np.array([ix(y) for y in range(H)])[:, None]
    lake = (xx > arris + 14*sx).astype(float); lake[:int(H*0.22)] = 0
    lake = np.asarray(Image.fromarray((lake*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(5)), float)/255.0
    ys = np.arange(H)
    # rain streaks
    rs = np.random.RandomState(9)
    NR = 260
    rx = rs.uniform(0, W, NR); ry = rs.uniform(0, H, NR); rlen = rs.uniform(16, 34, NR); rsp = rs.uniform(1.0, 1.6, NR)
    n = round(sp * FPS); td = tempfile.mkdtemp()
    for i in range(n):
        t = i / FPS
        # horizontal SLOSH (low freq, whole lake sways) + vertical chop
        slosh = 7.0*np.sin(2*np.pi*0.28*t) + 3.0*np.sin(2*np.pi*0.5*t + 1.0)
        chop = (0.9*np.sin(ys/7.0 + t*4.2) + 0.5*np.sin(ys/15.0 - t*2.7))[:, None]
        dx = (slosh + chop) * lake
        xmap = np.clip(xx + dx, 0, W-1.001); i0 = xmap.astype(int); f = xmap - i0
        warped = np.zeros_like(A)
        for c in range(3):
            warped[..., c] = A[yy, i0, c]*(1-f) + A[yy, np.minimum(i0+1, W-1), c]*f
        out = A*(1-lake[..., None]) + warped*lake[..., None]
        # whitecap shimmer on the lake
        shim = 1 + 0.05*np.sin(ys/6.0 + t*6.0)[:, None]
        out = out*(1-lake[..., None]*0.5) + out*shim[..., None]*(lake[..., None]*0.5)
        # driving rain (wind-blown, diagonal), wraps by element
        fr = Image.fromarray(np.clip(out,0,255).astype(np.uint8)).convert("RGB")
        dr = ImageDraw.Draw(fr, "RGBA")
        wind = 9.0
        for j in range(NR):
            yj = (ry[j] + (t*rsp[j]*620)) % (H+40) - 20
            xj = (rx[j] + (t*rsp[j]*wind*30)) % (W+40) - 20
            dr.line([(xj, yj), (xj - wind*0.5, yj + rlen[j])], fill=(200, 214, 230, 70), width=1)
        fr.save(f"{td}/f{i:05d}.png")
    enc(td, n, BE / "seg_017_b4.mp4", W, H)
    print(f"017 b4 slosh+rain: {be.durof(BE/'seg_017_b4.mp4'):.2f}s (span {sp})", flush=True)

import sys as _s
which = _s.argv[1] if len(_s.argv) > 1 else "all"
if which in ("all","b1"): b1_controlroom()
if which in ("all","b2"): b2_gauge()
if which in ("all","b4"): b4_slosh()
print("POLISH DONE", flush=True)
