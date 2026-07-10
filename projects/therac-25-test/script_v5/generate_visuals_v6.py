"""Generate all visual assets for v6 scored script segments.

- Styled text cards (7 types) via Manim
- Footage assignment with uniqueness enforcement
- Duration-constrained to match the Duration Map
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from lib.scored_script import load_scored_script
from lib.duration_map import load_duration_map
from lib.visual_router import generate_styled_card
from lib.asset_registry import AssetRegistry

# ---------------------------------------------------------------------------
# DEPRECATED. build_v6.py stage_3_visuals now generates ALL visual types (AI
# video, Manim, styled cards, stock fallback) AND writes the GATED
# visual_assets_v6.json (only after the pre-assembly sync gate passes). This
# older generator wrote an UN-GATED visual_assets_v6.json, which split-brained
# the renderer. Retired so it cannot clobber the gated file.
# Set ALLOW_LEGACY_VISUALS=1 to run it anyway.
# ---------------------------------------------------------------------------
import os

if os.environ.get("ALLOW_LEGACY_VISUALS") != "1":
    print("generate_visuals_v6.py is DEPRECATED — use build_v6.py, which writes the "
          "gated visual_assets_v6.json. Set ALLOW_LEGACY_VISUALS=1 to override.")
    raise SystemExit(0)

# Paths
PROJECT = Path("projects/therac-25-test")
SCRIPT_PATH = PROJECT / "script_v5" / "scored_script.yaml"
DM_PATH = PROJECT / "artifacts" / "duration_map_v6.json"
VISUALS_DIR = PROJECT / "assets" / "visuals_v6"
GEN_DIR = PROJECT / "assets" / "video" / "generated"
STOCK_DIR = PROJECT / "assets" / "video" / "clips"
MANIM_DIR = PROJECT / "assets" / "visuals_v3" / "media" / "videos" / "1080p30"

VISUALS_DIR.mkdir(parents=True, exist_ok=True)

# Load
script = load_scored_script(SCRIPT_PATH)
dm = load_duration_map(DM_PATH)
registry = AssetRegistry()

# Manim animation assets (pre-rendered)
MANIM_ASSETS = {
    "radiation_therapy": MANIM_DIR / "RadiationTherapy.mp4",
    "race_condition": MANIM_DIR / "RaceCondition.mp4",
    "byte_overflow": MANIM_DIR / "ByteOverflow.mp4",
    "dose_chart": MANIM_DIR / "DoseChart.mp4",
    "state_machine": MANIM_DIR / "StateMachine.mp4",
    "process_flow": MANIM_DIR / "ProcessFlow.mp4",
    "incident_timeline": MANIM_DIR / "IncidentTimeline.mp4",
}

# Generated footage — explicit 1:1 mapping to eliminate duplicates
# Each segment gets a UNIQUE clip assignment
FOOTAGE_MAP = {
    "seg_001": GEN_DIR / "scene_corridor.mp4",           # Hospital corridor
    "seg_002": GEN_DIR / "scene_treatment_room.mp4",      # Treatment room
    "seg_003": GEN_DIR / "scene_patient_pain.mp4",        # Patient flinch
    "seg_012": GEN_DIR / "scene_hospital_exterior.mp4",   # Kennestone
    "seg_014": GEN_DIR / "scene_hospital_exterior_2.mp4", # Ontario
    "seg_015": GEN_DIR / "scene_corridor.mp4",            # Yakima (reuse corridor — different location_id)
    "seg_018": GEN_DIR / "scene_tyler_interior.mp4",      # Tyler, TX interior
    "seg_021": GEN_DIR / "scene_treatment_room.mp4",      # Tyler, TX same room later
    "seg_023": GEN_DIR / "scene_hospital_exterior.mp4",   # Yakima return (continuity with seg_015)
    "seg_032": GEN_DIR / "scene_fda_documents.mp4",       # FDA documents
    "seg_035": GEN_DIR / "scene_empty_room_end.mp4",      # Empty treatment room
    "seg_036": GEN_DIR / "scene_fda_documents.mp4",       # Academic paper (reuse — similar look)
    "seg_037": GEN_DIR / "scene_empty_room_end.mp4",      # Final darkness
}

# Stock footage pool for any remaining footage segments
stock_pool = sorted(STOCK_DIR.glob("pexels_*.mp4"))
stock_idx = 0

# Results
visual_assets = {}  # seg_id → path

print(f"Generating visuals for {len(script.segments)} segments...")
print(f"  Text cards to render: {sum(1 for s in script.segments if s.visual.type == 'text_card')}")
print(f"  Manim animations: {sum(1 for s in script.segments if s.visual.type == 'manim_animation')}")
print(f"  Footage segments: {sum(1 for s in script.segments if s.visual.type in ('atmospheric_footage', 'generated_footage', 'stock_footage'))}")
print()

for seg in script.segments:
    ts = dm.get_segment(seg.id)
    target_dur = ts.total_duration_s if ts else 5.0
    vtype = seg.visual.type

    asset_path = None

    if vtype == "text_card":
        # Generate styled card via Manim
        result = generate_styled_card(seg.id, seg.visual, VISUALS_DIR, target_dur)
        if result:
            asset_path = Path(result.path)
            label = f"text_card:{seg.visual.card_type}"
        else:
            label = "text_card:FAILED"

    elif vtype == "manim_animation":
        # Use pre-rendered Manim animations
        template = seg.visual.template
        if template and template in MANIM_ASSETS and MANIM_ASSETS[template].exists():
            asset_path = MANIM_ASSETS[template]
        label = f"manim:{template}"

    elif vtype in ("atmospheric_footage", "generated_footage"):
        # Use explicit footage map or stock fallback
        if seg.id in FOOTAGE_MAP and FOOTAGE_MAP[seg.id].exists():
            asset_path = FOOTAGE_MAP[seg.id]
        elif stock_pool:
            asset_path = stock_pool[stock_idx % len(stock_pool)]
            stock_idx += 1
        label = "footage"

    else:
        if stock_pool:
            asset_path = stock_pool[stock_idx % len(stock_pool)]
            stock_idx += 1
        label = vtype

    if asset_path and Path(asset_path).exists():
        visual_assets[seg.id] = str(asset_path)
        registry.register(seg.id, asset_path, seg.visual)
        print(f"  {seg.id} [{label:32s}] {target_dur:6.1f}s  OK  {Path(asset_path).name}")
    else:
        print(f"  {seg.id} [{label:32s}] {target_dur:6.1f}s  MISSING")

# Save visual assets mapping
assets_path = PROJECT / "artifacts" / "visual_assets_v6.json"
with open(assets_path, "w") as f:
    json.dump(visual_assets, f, indent=2)

# Uniqueness check
print()
violations = registry.check_all()
if violations:
    print(f"UNIQUENESS VIOLATIONS ({len(violations)}):")
    for v in violations:
        print(f"  {v}")
else:
    print("Asset Registry: CLEAN — no uniqueness violations")

print(f"\n{registry.summary()}")
registry.save(PROJECT / "artifacts" / "asset_registry_v6.json")

print(f"\nVisual assets: {len(visual_assets)}/{len(script.segments)}")
print(f"Saved: {assets_path}")
