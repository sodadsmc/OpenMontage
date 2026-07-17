"""Act 5 stills-first: beat keyframes for seg_024/025/027 into _beats/.
3 direct master reuses ($0), 10 gemini gens grounded on sheets + master echoes."""
import json, os, shutil, sys
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
from dotenv import load_dotenv; load_dotenv(r"D:\OpenMontage2\.env")
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

DIRECT = {("seg_024", 3), ("seg_025", 2), ("seg_027", 1)}
ECHO = {
    ("seg_024", 1): ST / "seg_024.png", ("seg_024", 2): ST / "seg_024.png",
    ("seg_024", 4): ST / "seg_024.png", ("seg_024", 5): ST / "seg_024.png",
    ("seg_025", 1): ST / "seg_025.png", ("seg_025", 3): ST / "seg_025.png",
    ("seg_025", 4): ST / "seg_025.png",
    ("seg_027", 2): ST / "seg_027.png", ("seg_027", 3): ST / "seg_027.png",
    ("seg_027", 4): ST / "seg_027.png",
}

plans = {p["scene_id"]: p for p in json.loads(Path("projects/taum-sauk-2005/artifacts/beat_plans.json").read_text(encoding="utf-8"))}
made = reused = skipped = failed = 0
for sid in ["seg_024", "seg_025", "seg_027"]:
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
