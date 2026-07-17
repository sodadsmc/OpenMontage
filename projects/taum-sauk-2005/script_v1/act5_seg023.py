"""seg_023 (32.53s): investigation desk (deterministic lamp-glow life)
-> hand-inked findings card (word-synced, [6.9-29.5])
-> back to the desk on 'And the paperwork knew' (lamp flickers once). $0."""
import json, os, sys, tempfile
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
os.environ.setdefault("CHANNEL_STYLE", "graphic-novel-disaster")
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import taum_sketch as TS
import beat_exec as be
from lib.sketch_diagrams import resolve_cues, render_template
from pathlib import Path

PROJ = Path("projects/taum-sauk-2005")
AI = PROJ / "assets/ai_segments"
FPS = 30
DUR = {s["id"]: s for s in json.loads((PROJ / "artifacts/duration_map_v6.json").read_text(encoding="utf-8"))["segments"]}
TOTAL = DUR["seg_023"]["total_duration_s"]
T_CARD0, T_CARD1 = 6.9, 29.5

# ---- desk segments (deterministic lamp life) --------------------------------
im = Image.open(PROJ / "assets/stills/seg_023.png").convert("RGB")
A = np.asarray(im, float); H, W = A.shape[:2]
def soft(box, blur):
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rectangle(box, fill=255)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(blur)), float)[..., None] / 255.0
lamp = soft((40, 20, 330, 300), 40)                 # the lamp head + cone
paper = soft((int(W*0.12), int(H*0.30), int(W*0.92), int(H*0.98)), 70)  # lit papers
def desk_clip(t0, dur, out, flicker_at=None):
    n = round(dur * FPS); td = tempfile.mkdtemp()
    for i in range(n):
        t = t0 + i / FPS
        f = 1 + 0.05 * np.sin(2*np.pi*0.5*t) + 0.02 * np.sin(2*np.pi*2.1*t + 1.0)
        fl = 1.0
        if flicker_at is not None and 0 <= (t - flicker_at) < 0.22:
            fl = 0.72 if (t - flicker_at) < 0.11 else 0.88
        out_f = A * (1 + lamp * (f * fl - 1)) * (1 + paper * (0.03 * np.sin(2*np.pi*0.5*t + 0.6) * fl))
        Image.fromarray(np.clip(out_f, 0, 255).astype(np.uint8)).save(f"{td}/f{i:05d}.png")
    be.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"{td}/f%05d.png", "-vf", be.NORM,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])

tmp = Path(tempfile.mkdtemp())
desk_clip(0.0, T_CARD0, tmp / "open.mp4")
desk_clip(T_CARD1, TOTAL - T_CARD1, tmp / "tail.mp4", flicker_at=30.9)
print("desk segments done", flush=True)

# ---- the findings card ------------------------------------------------------
factory, cues = TS.SCENES["seg_023_card"]
C = resolve_cues(json.load(open(PROJ / "assets/audio_v6/seg_023.alignment.json", encoding="utf-8")), cues)
print("cues:", {k: round(v, 2) for k, v in C.items()}, flush=True)
inner = factory(C)
res = render_template(lambda ax, t, dur: inner(ax, t + T_CARD0, dur), T_CARD1 - T_CARD0,
                      tmp / "card.mp4", fps=15, finish=False, boil_grain=True)
card = Path(res)

# ---- concat -----------------------------------------------------------------
lst = tmp / "list.txt"
lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in
                       [tmp / "open.mp4", card, tmp / "tail.mp4"]))
out = AI / "seg_023.mp4"
be.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-t", f"{TOTAL:.3f}",
        "-vf", be.NORM, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(out)])
print(f"seg_023: {be.durof(out):.2f}s (slot {TOTAL})", flush=True)
