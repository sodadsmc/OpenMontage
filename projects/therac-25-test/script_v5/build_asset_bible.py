"""Build the Asset Bible for the Therac-25 documentary.

Extracts recurring locations/subjects from the scored script, then generates a
single canonical reference image per asset with Nano Banana 2 (via image_selector).
These canonical images are the image-to-video anchors that keep each location/
subject visually consistent across every AI-generated shot.

Run this BEFORE build_v6.py so the Asset Bible exists when visuals are generated.

The channel style (lib/channel_style.py) is injected into every canonical prompt,
so the reference images carry the graphic-novel look and every i2v shot inherits it.

Usage:
    python projects/therac-25-test/script_v5/build_asset_bible.py            # generate MISSING canonicals
    python projects/therac-25-test/script_v5/build_asset_bible.py --dry-run  # plan only, no spend
    python projects/therac-25-test/script_v5/build_asset_bible.py --force    # regenerate ALL (e.g. after a style change), paid
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

NANO_COST_PER_IMAGE = 0.04  # approx, Nano Banana via Kie.ai (~$0.32 for the 8-asset bible)

PROJECT = Path("projects/therac-25-test")
SCRIPT_PATH = PROJECT / "script_v5" / "scored_script.yaml"
ARTIFACTS_DIR = PROJECT / "artifacts"
BIBLE_PATH = ARTIFACTS_DIR / "asset_bible_v6.json"
REF_DIR = PROJECT / "assets" / "asset_bible"


def _generate_canonical(asset, bible) -> bool:
    """Generate one canonical reference image via Nano Banana (image_selector)."""
    from tools.graphics.image_selector import ImageSelector

    from lib.channel_style import apply_to_prompt
    REF_DIR.mkdir(parents=True, exist_ok=True)
    out = REF_DIR / f"{asset.asset_id}.png"
    base = bible.build_prompt_anchor(asset.asset_id, asset.description)
    # Location canonicals are atmospheric establishing anchors for a faceless
    # channel — keep them empty so people/patients are not baked into the i2v
    # anchor (figures belong in specific shots, added per-segment, not the bible).
    if getattr(asset, "type", "") == "location":
        base = base.rstrip(". ") + (". Empty and unoccupied, no people, no figures, "
                                    "no patients. Period-accurate mid-1980s only; "
                                    "no modern vehicles, no modern signage, blank unlettered signs.")
    prompt = apply_to_prompt(base)
    res = ImageSelector().execute({
        "prompt": prompt,
        "preferred_provider": "nano_banana",
        "aspect_ratio": "16:9",
        "output_path": str(out),
    })
    if getattr(res, "success", False) and out.exists():
        asset.canonical_reference_image = str(out)
        asset.canonical_image_url = (res.data or {}).get("image_url", "")  # Kie URL (host-free anchor)
        asset.canonical_image_prompt = prompt
        return True
    print(f"    ! generation failed for {asset.asset_id}: {getattr(res, 'error', '')}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Therac-25 Asset Bible")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract assets and save the bible without generating images")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate ALL canonical images even if they already exist "
                             "(e.g. after a channel-style change). Paid.")
    parser.add_argument("--only", nargs="*", default=None, metavar="ASSET_ID",
                        help="Regenerate only these asset_id(s); others keep their images. Paid.")
    args = parser.parse_args()

    import tools.base_tool  # noqa: F401  -- importing loads .env into os.environ
    from lib.scored_script import load_scored_script
    from lib.asset_bible import extract_assets, validate_referential_integrity, AssetBible

    print("=" * 60)
    print("ASSET BIBLE BUILD" + ("  (--force: regenerating all)" if args.force else ""))
    print("=" * 60)

    script = load_scored_script(SCRIPT_PATH)

    # Carry over canonical images from a prior bible so we don't regenerate.
    # --force skips the carry-over so every asset regenerates (e.g. after a style change).
    existing = AssetBible.load(BIBLE_PATH) if BIBLE_PATH.exists() else None
    bible = extract_assets(script)
    if not args.force:
        for a in bible.assets:
            # 1) carry over the image path + host-free Kie URL + prompt from a prior bible
            prev = existing.get(a.asset_id) if existing is not None else None
            if prev and prev.canonical_reference_image and Path(prev.canonical_reference_image).exists():
                a.canonical_reference_image = prev.canonical_reference_image
                a.canonical_image_url = getattr(prev, "canonical_image_url", "") or ""
                a.canonical_image_prompt = prev.canonical_image_prompt
            # 2) self-heal: if the link is missing but the PNG exists on disk by
            #    convention (REF_DIR/<asset_id>.png), re-link it. Keeps the bible
            #    valid even if a prior run left it partial/corrupted.
            if not (a.canonical_reference_image and Path(a.canonical_reference_image).exists()):
                conv = REF_DIR / f"{a.asset_id}.png"
                if conv.exists():
                    a.canonical_reference_image = str(conv)

    # --only: force regen of just the named asset(s); others keep their images.
    if args.only:
        only = set(args.only)
        known = {a.asset_id for a in bible.assets}
        for missing in only - known:
            print(f"  WARNING: --only '{missing}' is not a known asset_id")
        for a in bible.assets:
            if a.asset_id in only:
                a.canonical_reference_image = ""

    n_loc = sum(a.type == "location" for a in bible.assets)
    n_subj = sum(a.type == "subject" for a in bible.assets)
    print(f"  Assets: {len(bible.assets)} ({n_loc} location, {n_subj} subject)")

    # Pre-flight + cost preview for the assets that will actually generate (the paid step).
    to_gen = [a for a in bible.assets
              if not (a.canonical_reference_image and Path(a.canonical_reference_image).exists())]
    if to_gen and not args.dry_run:
        print(f"  Will generate {len(to_gen)} canonical image(s) "
              f"(~${NANO_COST_PER_IMAGE * len(to_gen):.2f} via Nano Banana / Kie.ai)")
        if not os.environ.get("KIE_API_KEY"):
            print("  ERROR: KIE_API_KEY is not set — required for Nano Banana image generation.")
            sys.exit(1)

    generated = 0
    for a in bible.assets:
        if a.canonical_reference_image and Path(a.canonical_reference_image).exists():
            print(f"  = {a.asset_id}: canonical image exists")
            continue
        if args.dry_run:
            print(f"  . {a.asset_id}: would generate canonical image ({len(a.appears_in)} segments)")
            continue
        print(f"  + {a.asset_id}: generating canonical image ...")
        if _generate_canonical(a, bible):
            generated += 1

    if args.dry_run:
        # A dry-run is pure planning — it must NOT mutate the saved artifact.
        print(f"\n  (dry-run) bible NOT saved; {len(to_gen)} image(s) would be generated")
    else:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        bible.save(BIBLE_PATH)
        print(f"\n  Saved bible: {BIBLE_PATH}  ({generated} images generated)")

    errors = validate_referential_integrity(script, bible)
    if errors:
        print("  REFERENTIAL INTEGRITY ERRORS:")
        for e in errors:
            print(f"    {e}")
        if not args.dry_run:
            sys.exit(1)
    else:
        print("  Referential integrity: OK")


if __name__ == "__main__":
    main()
