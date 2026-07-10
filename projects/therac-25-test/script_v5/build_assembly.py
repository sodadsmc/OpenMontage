"""Build the v5 assembly from the parsed script + visual assets."""
import json
import subprocess
from pathlib import Path

from lib.script_parser import parse_showrunner_script
from lib.visual_router import route_scene

parsed = parse_showrunner_script("projects/therac-25-test/script_v5/script_raw.md")

audio_dir = Path("projects/therac-25-test/assets/audio_v5")
visuals_dir = Path("projects/therac-25-test/assets/visuals_v5")
visuals_dir.mkdir(parents=True, exist_ok=True)
gen_dir = Path("projects/therac-25-test/assets/video/generated")
stock_dir = Path("projects/therac-25-test/assets/video/clips")

# Get segment audio durations
seg_durs = {}
for seg in parsed.voice_segments:
    f = audio_dir / f"seg_{seg.index:03d}.mp3"
    if f.exists():
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
            capture_output=True, text=True,
        )
        seg_durs[seg.index] = float(p.stdout.strip())

# Build timing cue map
timing_map = {}
for cue in parsed.timing_cues:
    timing_map.setdefault(cue.after_segment, []).append(cue)

# Classify each visual cue into an asset type
# and map to the best available asset
def classify_visual(desc):
    """Determine asset type from visual cue description."""
    dl = desc.lower()
    if "manim" in dl or "animated diagram" in dl or "animation" in dl:
        return "manim"
    elif "text card" in dl or "text on black" in dl or "text —" in dl:
        return "text_card"
    elif "hospital" in dl or "treatment room" in dl or "corridor" in dl:
        return "generated"  # atmospheric footage
    elif "interior" in dl or "exterior" in dl:
        return "generated"
    elif "counter" in dl and ("255" in dl or "byte" in dl or "ticks" in dl):
        return "manim_overflow"
    elif "beam firing" in dl:
        return "manim_beam"
    elif "fda" in dl or "letterhead" in dl or "correspondence" in dl:
        return "generated"
    elif "empty room" in dl or "lights off" in dl or "lights dim" in dl:
        return "generated"
    elif "face" in dl or "flinch" in dl:
        return "generated"
    else:
        return "stock"

# Available Manim templates (pre-rendered)
manim_dir = Path("projects/therac-25-test/assets/visuals_v3/media/videos/1080p30")
manim_assets = {
    "radiation": str(manim_dir / "RadiationTherapy.mp4"),
    "interlocks": str(manim_dir / "SafetyInterlocks.mp4"),
    "race": str(manim_dir / "RaceCondition.mp4"),
    "overflow": str(manim_dir / "ByteOverflow.mp4"),
    "dose": str(manim_dir / "DoseChart.mp4"),
    "timeline": str(manim_dir / "IncidentTimeline.mp4"),
    "flow": str(manim_dir / "ProcessFlow.mp4"),
    "state": str(manim_dir / "StateMachine.mp4"),
}

# Generated footage — mapped by visual keywords
generated_map = {
    "corridor": str(gen_dir / "scene_corridor.mp4"),
    "treatment room": str(gen_dir / "scene_treatment_room.mp4"),
    "patient": str(gen_dir / "scene_patient_pain.mp4"),
    "flinch": str(gen_dir / "scene_patient_pain.mp4"),
    "pain": str(gen_dir / "scene_patient_pain.mp4"),
    "hospital exterior": str(gen_dir / "scene_hospital_exterior.mp4"),
    "kennestone": str(gen_dir / "scene_hospital_exterior.mp4"),
    "ontario": str(gen_dir / "scene_hospital_exterior_2.mp4"),
    "hamilton": str(gen_dir / "scene_hospital_exterior_2.mp4"),
    "canadian": str(gen_dir / "scene_hospital_exterior_2.mp4"),
    "yakima": str(gen_dir / "scene_hospital_exterior.mp4"),
    "tyler": str(gen_dir / "scene_tyler_interior.mp4"),
    "east texas": str(gen_dir / "scene_tyler_interior.mp4"),
    "interior": str(gen_dir / "scene_tyler_interior.mp4"),
    "fda": str(gen_dir / "scene_fda_documents.mp4"),
    "letterhead": str(gen_dir / "scene_fda_documents.mp4"),
    "correspondence": str(gen_dir / "scene_fda_documents.mp4"),
    "empty room": str(gen_dir / "scene_empty_room_end.mp4"),
    "lights off": str(gen_dir / "scene_empty_room_end.mp4"),
    "lights dim": str(gen_dir / "scene_empty_room_end.mp4"),
    "powered down": str(gen_dir / "scene_empty_room_end.mp4"),
}
generated_fallback = str(gen_dir / "scene_corridor.mp4")

# Stock footage pool
stock_pool = sorted(stock_dir.glob("pexels_*.mp4"))[:20]
stock_idx = 0

# Build visual cue → asset map
vis_cue_map = {}
for vc in parsed.visual_cues:
    vis_cue_map.setdefault(vc.after_segment, []).append(vc)

# Build cuts
cuts = []
cut_idx = 0
timeline = 0.0

def add_cut(source, duration, scene_label=""):
    global cut_idx, timeline
    cuts.append({
        "id": f"cut_{cut_idx:03d}",
        "source": source,
        "in_seconds": 0,
        "out_seconds": duration,
        "speed": 1.0,
        "_scene_id": scene_label,
        "_timeline_start": timeline,
    })
    timeline += duration
    cut_idx += 1

def add_black(duration):
    """Add black screen via a very short silence + black frame."""
    global cut_idx, timeline
    # Use a black text card
    black_path = visuals_dir / "black.mp4"
    if not black_path.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=5:r=30",
            "-c:v", "libx264", "-crf", "23", "-t", "5", str(black_path),
        ], capture_output=True, timeout=10)
    cuts.append({
        "id": f"cut_{cut_idx:03d}",
        "source": str(black_path),
        "in_seconds": 0,
        "out_seconds": duration,
        "speed": 1.0,
        "_scene_id": "black",
        "_timeline_start": timeline,
    })
    timeline += duration
    cut_idx += 1

def pick_manim(desc):
    """Pick a Manim animation based on the visual description."""
    dl = desc.lower()
    if "byte" in dl or "counter" in dl or "255" in dl or "increment" in dl:
        return manim_assets.get("overflow")
    elif "race condition" in dl or "parallel timeline" in dl or "diverging" in dl:
        return manim_assets.get("race")
    elif "two modes" in dl or "electron beam direct" in dl or "x-ray with target" in dl:
        return manim_assets.get("state")
    elif "schematic" in dl or "linear accelerator" in dl:
        return manim_assets.get("radiation")
    elif "terminal" in dl or "operator" in dl:
        return manim_assets.get("flow")
    elif "beam firing" in dl:
        return manim_assets.get("dose")
    else:
        return manim_assets.get("flow")

def pick_generated(visual_desc=""):
    """Pick a generated footage clip matching the visual description."""
    dl = visual_desc.lower()
    for keyword, path in generated_map.items():
        if keyword in dl and Path(path).exists():
            return path
    if Path(generated_fallback).exists():
        return generated_fallback
    return None

def pick_stock():
    """Pick next stock footage clip."""
    global stock_idx
    if stock_pool:
        clip = str(stock_pool[stock_idx % len(stock_pool)])
        stock_idx += 1
        return clip
    return None

# Process each segment
for seg in parsed.voice_segments:
    seg_dur = seg_durs.get(seg.index, 5.0)
    label = f"seg_{seg.index:03d}_{seg.act}"

    # Get visual cues for this segment
    vis_cues = vis_cue_map.get(seg.index, []) + vis_cue_map.get(seg.index - 1, [])

    if vis_cues:
        vc = vis_cues[-1]  # use the most recent visual cue
        atype = classify_visual(vc.description)

        if atype == "manim" or atype == "manim_overflow" or atype == "manim_beam":
            asset = pick_manim(vc.description)
            if asset and Path(asset).exists():
                add_cut(asset, seg_dur, label)
            else:
                fallback = pick_generated(vc.description) or pick_stock()
                if fallback:
                    add_cut(fallback, seg_dur, label)
        elif atype == "text_card":
            # Generate text card from description
            text = vc.description.replace("Text card — ", "").replace("Text card, white on black — ", "")
            text = text.replace("white on black: ", "").strip('"')

            from lib.visual_router import _generate_text_card
            asset = _generate_text_card(
                {"scene_id": f"tc_{seg.index}", "narration": text},
                visuals_dir,
            )
            if asset and Path(asset.path).exists():
                add_cut(asset.path, seg_dur, label)
            else:
                add_black(seg_dur)
        elif atype == "generated":
            asset = pick_generated(vc.description)
            if asset:
                add_cut(asset, min(seg_dur, 8), label)
                # Fill remaining with stock if needed
                remaining = seg_dur - 8
                if remaining > 0.5:
                    fallback = pick_stock()
                    if fallback:
                        add_cut(fallback, remaining, label)
            else:
                fallback = pick_stock()
                if fallback:
                    add_cut(fallback, seg_dur, label)
        else:
            fallback = pick_stock()
            if fallback:
                add_cut(fallback, seg_dur, label)
    else:
        # No visual cue — use stock
        fallback = pick_stock()
        if fallback:
            add_cut(fallback, seg_dur, label)

    # Add timing cues (silence/black)
    cues = timing_map.get(seg.index, [])
    for cue in cues:
        if cue.cue_type == "cut_to_black":
            add_black(cue.duration)
        elif cue.cue_type == "silence":
            add_black(cue.duration)

# Save manifest
manifest = {"version": "1.0", "assembly_manifest": {"version": "1.0", "cuts": cuts}}
out_path = Path("projects/therac-25-test/artifacts/assembly_manifest_v5.json")
with open(out_path, "w") as f:
    json.dump(manifest, f, indent=2)

# Count by type
manim_cuts = sum(1 for c in cuts if "visuals_v3" in c["source"] or "visuals_v5" in c["source"])
black_cuts = sum(1 for c in cuts if c["_scene_id"] == "black")
gen_cuts = sum(1 for c in cuts if "generated" in c["source"])
stock_cuts = len(cuts) - manim_cuts - black_cuts - gen_cuts
text_cuts = sum(1 for c in cuts if "text_card" in c["source"])

print("Assembly: %d cuts, %.1fs (%.1f min)" % (len(cuts), timeline, timeline / 60))
print("  Manim animations: %d" % manim_cuts)
print("  Text cards: %d" % text_cuts)
print("  Generated footage: %d" % gen_cuts)
print("  Black (silence): %d" % black_cuts)
print("  Stock footage: %d" % stock_cuts)
