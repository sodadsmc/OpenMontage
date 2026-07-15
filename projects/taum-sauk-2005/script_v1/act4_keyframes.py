"""Act 4 stills-first: author beat keyframes for seg_017-022 into _beats/
(executor paths), grounded on sheets + scene-master echoes. Four beats reuse
their approved scene master directly ($0). Border-checked. ~$0.034/gen."""
import json, os, shutil, sys
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); k, v = k.strip(), v.split("#")[0].strip()
        if v: os.environ.setdefault(k, v)
from PIL import Image
from lib.gemini_image import gemini_image
from lib.frame_hygiene import find_border_box
from pathlib import Path

B = Path("projects/taum-sauk-2005/assets/asset_bible")
BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
ST = Path("projects/taum-sauk-2005/assets/stills")
STYLE = (" Graphic-novel illustration, bold black ink linework, halftone shading, "
         "duotone deep navy and warm amber, high contrast, film grain, illustrated "
         "NOT photorealistic. Full-bleed, image fills the entire frame edge to edge, "
         "NO panel border, NO frame, NO caption, NO text.")

# approved scene master reused directly as a beat keyframe ($0)
DIRECT = {("seg_017", 5), ("seg_018", 1), ("seg_019", 3), ("seg_021", 1)}
# scene-master echo refs per beat (identity/palette anchor alongside sheets)
ECHO = {
    ("seg_017", 3): ST / "seg_017.png", ("seg_017", 4): ST / "seg_017.png",
    ("seg_018", 2): ST / "seg_018.png", ("seg_018", 4): ST / "seg_018.png",
    ("seg_018", 5): ST / "seg_018.png",
    ("seg_019", 1): ST / "seg_019.png", ("seg_019", 2): ST / "seg_019.png",
    ("seg_019", 4): ST / "seg_020.png",
    ("seg_020", 1): ST / "seg_020.png", ("seg_020", 3): ST / "seg_020.png",
    ("seg_020", 4): ST / "seg_020.png",
    ("seg_021", 2): ST / "seg_021.png", ("seg_021", 3): ST / "seg_021.png",
    ("seg_021", 4): ST / "seg_021.png", ("seg_021", 5): ST / "seg_021.png",
    ("seg_022", 5): ST / "seg_022.png",
}

plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
made = reused = skipped = failed = 0
for sid in ["seg_017", "seg_018", "seg_019", "seg_020", "seg_021", "seg_022"]:
    for i, b in enumerate(plans[sid]["beats"], 1):
        out = BE / f"{sid}_b{i}.png"
        if out.exists():
            print(f"{sid} b{i}: exists, skip"); skipped += 1; continue
        if (sid, i) in DIRECT:
            shutil.copy(str(ST / f"{sid}.png"), str(out))
            print(f"{sid} b{i} '{b['label']}': master reused ($0)", flush=True)
            reused += 1; continue
        refs = [str(B / s) for s in b.get("sheets", []) if (B / s).exists()][:2]
        extra = ECHO.get((sid, i))
        if extra and Path(extra).exists():
            refs = (refs + [str(extra)])[:3]
        ok = gemini_image(b["keyframe_prompt"] + STYLE, str(out), image_paths=refs or None)
        if ok:
            box = find_border_box(Image.open(out).convert("RGB"))
            if box:
                Image.open(out).convert("RGB").crop(box).save(out)
            print(f"{sid} b{i} '{b['label']}': OK{' (deborder)' if box else ''}", flush=True)
            made += 1
        else:
            print(f"{sid} b{i}: FAIL", flush=True); failed += 1
print(f"KEYFRAMES DONE made={made} reused={reused} skipped={skipped} failed={failed}", flush=True)
