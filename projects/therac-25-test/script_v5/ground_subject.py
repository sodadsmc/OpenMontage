"""Reference Grounding — give a real subject an ACCURATE canonical.

Sources a real photo (broad Google image search), lets a HUMAN verify it, then restyles
it into the channel's graphic-novel look (Nano Banana edit) and re-grounds the Asset Bible
so every i2v shot of that subject inherits the correct real-world form.

Two phases with a HUMAN GATE between them:

  Phase 1 (FREE)  — search + download candidates:
    python .../ground_subject.py --search "Therac-25 radiation therapy machine" --slug therac_machine
      -> downloads the top web-image candidates to assets/_reference/_candidates/<slug>/
      -> a human REVIEWS them and picks the correct one.

  Phase 2 (PAID)  — restyle the approved photo + re-ground a bible asset:
    python .../ground_subject.py --restyle <candidate.jpg> --asset loc_kennestone_treatment_room \
        --style "graphic-novel inked navy+amber treatment room; preserve the Therac-25's exact form"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

PROJECT = Path("projects/therac-25-test")
REF_DIR = PROJECT / "assets" / "_reference"
BIBLE_PATH = PROJECT / "artifacts" / "asset_bible_v6.json"
ASSET_DIR = PROJECT / "assets" / "asset_bible"


def cmd_search(query: str, slug: str, num: int) -> None:
    import tools.base_tool  # noqa: F401  -- loads .env
    from lib.image_search import search_images, download_image, is_configured

    ready, reason = is_configured()
    if not ready:
        print(f"  NOT READY: {reason}")
        print("  -> create a Programmable Search Engine + set GOOGLE_CSE_ID (see lib/image_search.py).")
        sys.exit(2)

    print(f"  Google image search: {query!r}")
    cands = search_images(query, num=num, imgType="photo", imgSize="large")
    out = REF_DIR / "_candidates" / slug
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, c in enumerate(cands):
        ext = ".jpg" if "jpeg" in (c.get("mime") or "") else ".png"
        p = out / f"cand_{i:02d}{ext}"
        if download_image(c["url"], p):
            saved.append({"idx": i, "path": str(p), **c})
            print(f"  [{i}] {c.get('width')}x{c.get('height')}  {p.name}  | {str(c.get('title'))[:60]}")
    (out / "candidates.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(f"\n  {len(saved)} candidates -> {out}")
    print("  HUMAN GATE: review them, pick the correct one, then run --restyle on it.")


def _resolve_ref_url(image: str) -> str | None:
    """A PUBLIC URL for the reference (Kie/nano-banana edits from URLs, not local files):
    the arg itself if it's already a URL, else the original source URL of a downloaded
    candidate, looked up by filename in its sibling candidates.json."""
    if image.startswith(("http://", "https://")):
        return image
    p = Path(image)
    cj = p.parent / "candidates.json"
    if cj.exists():
        try:
            for c in json.loads(cj.read_text(encoding="utf-8")):
                if Path(c.get("path", "")).name == p.name:
                    return c.get("url")
        except Exception:  # noqa: BLE001
            pass
    # No candidate URL -> host the local file (catbox/0x0) so Kie can fetch it.
    try:
        from lib.image_host import upload_image
        return upload_image(str(p))
    except Exception:  # noqa: BLE001
        return None


def cmd_restyle(image: str, asset_id: str, style: str) -> None:
    import tools.base_tool  # noqa: F401
    from tools.graphics.image_selector import ImageSelector
    from lib.channel_style import apply_to_prompt
    from lib.asset_bible import AssetBible

    is_url = image.startswith(("http://", "https://"))
    if not is_url and not Path(image).exists():
        print(f"  no such image: {image}")
        sys.exit(1)
    if not BIBLE_PATH.exists():
        print(f"  bible not found: {BIBLE_PATH}")
        sys.exit(1)

    ref_url = _resolve_ref_url(image)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    out = ASSET_DIR / f"{asset_id}.png"
    prompt = apply_to_prompt(style)
    inputs = {
        "prompt": prompt, "preferred_provider": "nano_banana",
        "generation_mode": "edit", "aspect_ratio": "16:9", "output_path": str(out),
    }
    if ref_url:
        inputs["image_url"] = ref_url
        print(f"  TRUE image-edit from reference URL: {ref_url[:80]}")
    else:
        print("  WARNING: no public URL for the reference -> text-to-image fallback")
    res = ImageSelector().execute(inputs)
    if not (getattr(res, "success", False) and out.exists()):
        print(f"  restyle FAILED: {getattr(res, 'error', '')}")
        sys.exit(1)

    url = (res.data or {}).get("image_url", "")
    bible = AssetBible.load(BIBLE_PATH)
    a = bible.get(asset_id)
    if a is None:
        print(f"  asset {asset_id!r} not in bible — saved image to {out} but NOT linked.")
        sys.exit(1)
    a.canonical_reference_image = str(out)
    a.canonical_image_url = url
    a.canonical_image_prompt = prompt
    bible.save(BIBLE_PATH)
    print(f"  re-grounded {asset_id}: {out}")
    print(f"  host-free URL: {'yes' if url else 'LOCAL ONLY (i2v will host it at gen time)'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Reference grounding (search -> human gate -> restyle)")
    ap.add_argument("--search", metavar="QUERY", help="Phase 1: Google-image-search this subject")
    ap.add_argument("--slug", default="subject", help="folder name for candidates")
    ap.add_argument("--num", type=int, default=8)
    ap.add_argument("--restyle", metavar="IMAGE", help="Phase 2: restyle this approved photo")
    ap.add_argument("--asset", help="bible asset_id to re-ground (with --restyle)")
    ap.add_argument("--style", default="", help="restyle prompt (with --restyle)")
    a = ap.parse_args()
    if a.search:
        cmd_search(a.search, a.slug, a.num)
    elif a.restyle:
        if not a.asset:
            print("  --restyle requires --asset ASSET_ID")
            sys.exit(1)
        cmd_restyle(a.restyle, a.asset, a.style or "graphic-novel illustration, preserve the subject's exact form")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
