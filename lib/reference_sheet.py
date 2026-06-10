"""Reference sheets — multi-view model sheets for named Asset Bible subjects.

A single canonical image locks ONE camera angle: every keyframe edit of the
asset inherits that exact view, and any prompt asking for a different angle
gives the model license to invent a different-looking machine. A reference
sheet fixes that: the curated REAL photos of the subject (collected via
lib/image_search + human curation) are composed into one style-locked sheet
showing the subject from several views with consistent proportions and
details. The sheet is stored on the AssetEntry (``reference_sheet`` /
``reference_sheet_url``) and rides into every keyframe EDIT as an extra
reference — new angles stay on-model instead of being re-imagined.

This is the "character sheet" pattern from the channel research (image models
hold identity across many reference inputs far better than across re-rolls of
one image). It applies equally to machines and locations as to characters.

Spend note: one sheet = one Nano Banana multi-image edit (~$0.04-0.09) plus
free hosting. Build once per asset, reuse for every episode that asset appears
in.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# What a model sheet must look like for this pipeline. Style/medium language is
# appended by the caller via channel_style so the sheet matches the canonical
# art the keyframes are edited from.
#
# Lesson from the first Therac sheet attempt: a loose "three views side by
# side" instruction let the model ABSTRACT the machine into a generic cabinet —
# the defining silhouette vanished. The template therefore (a) anchors hard on
# "the exact machine shown in the reference images", (b) names the silhouette
# via {identity} inside every view's direction, and (c) forbids simplification
# explicitly.
_SHEET_TEMPLATE = (
    "Technical model reference sheet of the EXACT machine shown in the "
    "reference images — {subject}: {identity}. "
    "One 16:9 board with three large views of this same machine, each "
    "preserving its full silhouette (support column, gantry arm, treatment "
    "head, and patient table where visible): "
    "LEFT — the complete machine in three-quarter view exactly as in the "
    "reference images; "
    "CENTER — a side profile showing the full gantry arm and head; "
    "RIGHT — a front view facing the treatment head; "
    "plus one small inset panel: close detail of {detail}. "
    "Identical proportions, panel lines, materials and colors in every view — "
    "this is the SAME physical machine drawn from different angles, never "
    "redesigned, never simplified into a plain cabinet, no parts omitted. "
    "Plain dark backdrop, generous spacing between views, no text, no labels, "
    "no captions, no people."
)


def build_reference_sheet(
    subject: str,
    identity_tokens: list[str],
    ref_urls: list[str],
    output_path: str | Path,
    detail: str = "the most distinctive component",
    apply_channel_style: bool = True,
    source_note: str = "",
) -> tuple[str | None, str | None]:
    """Compose a multi-view reference sheet from curated real-photo URLs.

    Args:
        subject: the proper name ("Therac-25").
        identity_tokens: locked physical description from the AssetEntry.
        ref_urls: PUBLIC URLs of the curated reference images (real photos
            and/or the approved grounded canonical). First entries carry the
            most weight; capped at 7 (provider limit).
        output_path: where to save the sheet locally if the provider returns
            bytes instead of a URL.
        detail: what the inset detail panel should show.
        apply_channel_style: append the channel medium so the sheet matches
            the canonical art (sheets are edit references for styled keyframes,
            so a photoreal sheet would fight the style).

    Returns:
        (local_path_or_url, hosted_url) — hosted_url is what keyframe edits
        consume; None/None on failure (callers keep the single-canonical flow).
    """
    refs = [u for u in ref_urls if u and str(u).startswith(("http://", "https://"))][:7]
    if not refs:
        _log.warning("reference_sheet: no usable public ref URLs for %s", subject)
        return None, None

    prompt = _SHEET_TEMPLATE.format(
        subject=subject,
        detail=detail,
        identity=", ".join(identity_tokens) if identity_tokens else subject,
    )
    if source_note:
        # e.g. "the references are labeled technical line diagrams — reproduce the
        # machine's exact geometry from them, ignore the room/labels/text"
        prompt = f"{prompt} {source_note.strip()}"
    if apply_channel_style:
        try:
            from lib.channel_style import apply_to_prompt
            prompt = apply_to_prompt(prompt)
        except Exception:  # noqa: BLE001
            pass

    try:
        from tools.graphics.image_selector import ImageSelector
        sel = ImageSelector()
        res = sel.execute({
            "prompt": prompt,
            "preferred_provider": "nano_banana",
            "generation_mode": "edit",
            "image_urls": refs,
            "aspect_ratio": "16:9",
            "output_path": str(output_path),
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning("reference_sheet: generation error for %s: %s", subject, exc)
        return None, None
    if not getattr(res, "success", False):
        _log.warning("reference_sheet: generation failed for %s: %s",
                     subject, getattr(res, "error", ""))
        return None, None

    data = res.data or {}
    url = data.get("image_url")
    local = data.get("output") or (res.artifacts[0] if getattr(res, "artifacts", None) else None)

    hosted = url if (url and str(url).startswith(("http://", "https://"))) else None
    if hosted is None and local and Path(str(local)).exists():
        try:
            from lib.image_host import upload_image
            hosted = upload_image(local)
        except Exception as exc:  # noqa: BLE001
            _log.warning("reference_sheet: hosting failed for %s: %s", subject, exc)
    return (str(local) if local else hosted), hosted


def attach_sheet(asset: Any, local: str | None, hosted: str | None,
                 ref_urls: list[str] | None = None) -> None:
    """Record a built sheet on an AssetEntry (caller saves the bible)."""
    if local:
        asset.reference_sheet = str(local)
    if hosted:
        asset.reference_sheet_url = hosted
    if ref_urls:
        asset.reference_images = list(ref_urls)
