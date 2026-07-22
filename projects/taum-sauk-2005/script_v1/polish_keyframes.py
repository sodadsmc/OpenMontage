"""Full-episode polish: all replacement keyframes, hard style-locked.
Chained: 012_b6 anchors to the new 012_b5 (same operator); 024_b4 to 024_b3."""
import os, sys
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
from dotenv import load_dotenv; load_dotenv(r"D:\OpenMontage2\.env")
from lib.gemini_image import gemini_image
from lib.frame_hygiene import find_border_box
from PIL import Image
from pathlib import Path

B = Path("projects/taum-sauk-2005/assets/asset_bible")
BE = Path("projects/taum-sauk-2005/assets/ai_segments/_beats")
ST = Path("projects/taum-sauk-2005/assets/stills")
STYLE = (" FLAT 2D hand-drawn graphic-novel illustration: bold black ink outlines, halftone dot "
         "shading, duotone deep navy and warm amber, high contrast, film grain. NOT photorealistic, "
         "NOT a 3D render, NO realistic lighting or photographic detail — a hand-inked comic panel. "
         "Full-bleed, fills the frame edge to edge, NO panel border, NO caption, NO text.")

def gen(out, prompt, refs, tag=None):
    """Generate to a TEMP name; only on success bak the original and swap in.
    (v1 renamed originals BEFORE generating — an API outage left 10 beats with
    no keyframe at all.)"""
    out = Path(out)
    if tag and out.with_suffix(f".{tag}.bak.png").exists():
        print(f"{out.name}: already done, skip", flush=True); return True
    if tag is None and out.exists():
        print(f"{out.name}: already done, skip", flush=True); return True
    tmp = out.with_suffix(".new.png")
    refs = [str(r) for r in refs if Path(r).exists()][:3]
    ok = gemini_image(prompt + STYLE, str(tmp), image_paths=refs or None)
    if ok and tmp.exists():
        box = find_border_box(Image.open(tmp).convert("RGB"))
        if box: Image.open(tmp).convert("RGB").crop(box).save(tmp)
        if out.exists() and tag:
            b_ = out.with_suffix(f".{tag}.bak.png")
            if not b_.exists(): os.replace(out, b_)
        os.replace(tmp, out)
        print(f"{out.name}: OK", flush=True)
        return True
    print(f"{out.name}: FAIL", flush=True)
    return False

# --- story fixes ---
gen(BE/"s003_tail.png",
    "Night: a small wooden house in a bare winter forest coming APART in a surging black torrent — "
    "walls buckling and splitting, timbers wrenching loose, black water blasting through, spray "
    "exploding, trees whipping. Terrifying, kinetic.",
    refs=[B/"loc_toops_house.png", ST/"seg_020.png"])
gen(BE/"seg_006_b1.png",
    tag="round", prompt=    "Aerial: a KIDNEY-BEAN-SHAPED ring of rockfill crowning a forested mountaintop, topped with a "
    "thin pale concrete parapet wall, dark water filling the kidney-shaped basin to the brim, "
    "late golden light. The rim curves in the distinctive kidney silhouette.",
    refs=[B/"loc_upper_reservoir.png", BE/"seg_010_b2.png"])
gen(BE/"seg_012_b5.png",
    tag="hands", prompt=    "A HEAVYSET male plant operator in a light short-sleeve work shirt, seen from three-quarter "
    "behind, seated at the 1960s control-room console, one hand working the computer keyboard, "
    "CRT glow on his face, dim night room, a clipboard resting beside the keyboard.",
    refs=[B/"loc_control_room.png"])
gen(BE/"seg_012_b6.png",
    tag="hands", prompt=    "THE SAME heavyset male operator in the same light work shirt at the same 1960s console, now "
    "holding the clipboard up and reading it, other hand still on the keyboard, CRT glow, dim "
    "night room. Same man, same room, same framing family.",
    refs=[BE/"seg_012_b5.png", B/"loc_control_room.png"])
# --- time-lapse construction trio ---
gen(BE/"seg_024_b3.png",
    tag="static", prompt=    "Aerial construction time-lapse stage: the kidney-shaped reservoir rim half-built — fresh "
    "stepped concrete walls rising section by section, cranes and forms along the rim, the basin "
    "floor bare, bright daylight.",
    refs=[BE/"seg_024_b1.png", B/"loc_rebuilt_dam.png"])
gen(BE/"seg_024_b4.png",
    tag="static", prompt=    "Aerial construction time-lapse stage: the kidney-shaped concrete rim nearly complete, a crane "
    "lowering a tall instrument sensor mast into place on the rim while workers guide it, two "
    "masts already standing, the basin starting to hold shallow water, daylight.",
    refs=[BE/"seg_024_b3.png", B/"loc_rebuilt_dam.png"])
gen(BE/"seg_024_b5.png",
    tag="static", prompt=    "Aerial: the finished kidney-shaped concrete reservoir filling with dark water, the engineered "
    "spillway channel carrying a smooth controlled sheet of water down its steps, instrument masts "
    "standing along the completed rim, daylight.",
    refs=[BE/"seg_024_b4.png", ST/"seg_024.png"])
# --- style restyles ---
gen(BE/"seg_004_b1.png",
    tag="photo", prompt=    "Aerial: a raw scoured channel carved down a forested mountainside — stripped bedrock and mud "
    "where the flood tore through, splintered trees along the edges, grey morning light.",
    refs=[B/"loc_valley_scour.png", BE/"seg_019_b1.png"])
gen(BE/"seg_019_b2.png",
    tag="photo", prompt=    "Aerial: the breached reservoir rim — a huge ragged gap torn in the rockfill ring, the dark "
    "lake pouring out through the notch as a white torrent blasting down the outer slope, "
    "pre-dawn gloom.",
    refs=[B/"loc_reservoir_breached.png", BE/"seg_019_b1.png"])
gen(BE/"seg_019_b3.png",
    tag="photo", prompt=    "Aerial: a white wall of water, rock and shredded trees roaring DOWN a dark forested "
    "mountainside, mowing the forest flat in a widening scour path, pre-dawn gloom.",
    refs=[B/"loc_valley_scour.png", B/"loc_proffit_mountain.png"])
gen(BE/"seg_020_b4.png",
    tag="photo", prompt=    "Night: dark churning floodwater racing through bare winter trees, carrying broken timbers and "
    "wreckage past a ruined house, foam streaking the black water.",
    refs=[BE/"seg_021_b2.png", B/"loc_valley_scour.png"])
print("KEYFRAMES DONE", flush=True)
