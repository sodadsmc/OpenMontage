"""Derive IDENTITY TOKENS for a bible asset from its reference images (vision model).

Identity tokens are the per-project data that keeps a recurring subject on-model across
stochastic generations: they ride in every keyframe/video prompt ("the AECL Therac-25: a
massive beige floor-standing housing... NOT a C-arm or open gantry ring"). The pipeline
mechanics are universal; whether a NEW documentary's machine/character/location stays
consistent depends almost entirely on how well its tokens are written.

This module codifies the standard that was discovered the expensive way on the Therac-25
(paid re-rolls until a human noticed "the model keeps drawing the head boxy" and hand-wrote
the missing attribute). A good token set is a POLICE DESCRIPTION for an illustrator:

1. SILHOUETTE  — the overall form in one clause (what you'd recognize at 50 ft).
2. PARTS      — 2-4 distinctive components with SHAPE adjectives ("smooth ROUNDED
                cylindrical treatment head hanging from the curved overhead arm").
3. SURFACE    — material/color/era ("beige enamel housing, 1980s medical beige").
4. CONFUSABLES — explicit negatives for the nearest things an image model WILL draw
                instead ("NOT a C-arm, NOT an open CT gantry ring, NOT a dental X-ray
                arm"). Image models drift to the most common look-alike in their prior;
                naming it is what stops the drift.

Usage (CLI, from repo root):
    python -m lib.identity_tokens <bible.json> <asset_id>            # derive + print
    python -m lib.identity_tokens <bible.json> <asset_id> --apply    # also write back

The derived tokens are a STARTING set — the operator curates. Re-roll hints from the
dashboard (logged in the feedback event log) are the ongoing signal for which attribute
is still missing: when a hint keeps repeating ("rounded head", "hospital gown"), promote
it into the asset's tokens here.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Same fast vision model the QC gate uses; tokens are derived rarely (bible-build time).
_MODEL = os.environ.get("GEMINI_QC_MODEL", "gemini-2.5-flash")

_PROMPT = """You are writing IDENTITY TOKENS for an illustrator who must draw this exact \
{kind} many times, in many poses/angles, WITHOUT looking at the photos again — only your \
words. Any attribute you fail to name precisely will be reinvented differently each time.

Study the image(s) and return ONLY JSON:
{{"tokens": ["...", "...", "..."], "confusables": ["...", "..."]}}

Rules for "tokens" (3-5 strings, each a self-contained prompt clause):
- Token 1 = SILHOUETTE: name the subject ("{name}") + the overall form in one clause.
- Then 2-4 DISTINCTIVE PARTS, each with SHAPE adjectives (rounded/boxy/tapered/curved),
  their position, and how they attach. Prefer the parts an artist gets WRONG without
  guidance (the head/arm/base shapes, not generic parts every such {kind} has).
- One token for SURFACE: material, color, era/wear.
- Concrete and visual only — no history, no function, no feelings.

Rules for "confusables" (1-3 strings):
- The look-alike objects an image model would drift toward when drawing this from memory
  (for a radiotherapy machine: a C-arm, a CT donut gantry). Phrase each as
  "NOT a <thing> (<the visible difference>)".
"""


def derive_identity_tokens(image_paths: list[str | Path], subject_name: str,
                           kind: str = "machine") -> list[str]:
    """One vision call -> a standard-shape token list (tokens + NOT-confusables merged).
    Returns [] on any failure (caller keeps existing tokens)."""
    imgs = [Path(p) for p in image_paths if p and Path(p).exists()]
    if not imgs:
        _log.warning("identity_tokens: no local images to derive from")
        return []
    try:
        import google.generativeai as genai
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            _log.warning("identity_tokens: GOOGLE_API_KEY not set")
            return []
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            _MODEL,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2, max_output_tokens=2048,
                response_mime_type="application/json"),
        )
        parts = []
        uploaded = []
        for p in imgs[:4]:
            uf = genai.upload_file(path=str(p), display_name=p.stem)
            parts.append(uf)
            uploaded.append(uf)
        parts.append(_PROMPT.format(kind=kind, name=subject_name))
        out = json.loads(model.generate_content(parts).text)
        for uf in uploaded:
            try:
                genai.delete_file(uf.name)
            except Exception:  # noqa: BLE001
                pass
        tokens = [t.strip() for t in (out.get("tokens") or []) if t and t.strip()]
        negs = [c.strip() for c in (out.get("confusables") or []) if c and c.strip()]
        if negs:
            tokens.append("; ".join(negs))
        return tokens
    except Exception as e:  # noqa: BLE001
        _log.warning("identity_tokens: derivation failed: %s", e)
        return []


def _asset_local_images(bible_path: Path, asset: dict) -> list[Path]:
    """The asset's LOCAL grounding images: real reference photos first (resolved by
    basename under the project, since stored URLs expire), then the model sheet."""
    proj = bible_path.parent.parent  # artifacts/asset_bible.json -> project root
    out: list[Path] = []
    for url in (asset.get("reference_images") or []):
        name = (url or "").rstrip("/").rsplit("/", 1)[-1]
        if name:
            hit = next(iter(proj.glob(f"assets/**/{name}")), None)
            if hit and hit.is_file():
                out.append(hit)
    sheet = asset.get("reference_sheet") or ""
    if sheet:
        p = Path(sheet)
        if not p.is_absolute():
            p = proj.parent.parent / sheet  # repo-root-relative convention
        if p.is_file():
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    bible_path = Path(argv[0])
    asset_id = argv[1]
    apply = "--apply" in argv
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    asset = next((a for a in bible.get("assets", []) if a.get("asset_id") == asset_id), None)
    if asset is None:
        print(f"asset {asset_id!r} not in {bible_path}")
        return 2
    imgs = _asset_local_images(bible_path, asset)
    print(f"deriving from {len(imgs)} image(s):")
    for p in imgs:
        print("  -", p)
    kind = "person" if asset.get("type") == "subject" else "machine"
    name = asset.get("subject") or asset_id
    tokens = derive_identity_tokens(imgs, name, kind=kind)
    if not tokens:
        print("derivation FAILED — existing tokens untouched")
        return 1
    print("\nderived tokens:")
    for t in tokens:
        print("  *", t)
    if apply:
        existing = asset.get("identity_tokens") or []
        merged = existing + [t for t in tokens if t not in existing]
        asset["identity_tokens"] = merged
        bible_path.write_text(json.dumps(bible, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\napplied: {len(existing)} existing + {len(merged) - len(existing)} new "
              f"-> {bible_path}")
    else:
        print("\n(dry run — pass --apply to merge into the bible)")
    return 0


if __name__ == "__main__":
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main(sys.argv[1:]))
