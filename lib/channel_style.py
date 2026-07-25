"""Channel visual style — the look applied to every AI shot + the finishing pass.

Stylizing hard (graphic-novel ink / halftone / duotone / grain) is the documented
way to hide the AI uncanny valley and unify inconsistent frames into one authored
look (see docs/Visual Treatment Styles...md). This module exposes the style to the
pipeline:

  - apply_to_prompt(p): append the style MEDIUM to an image/video gen prompt
  - negative(): negative-prompt terms for providers that support them
  - finish_filter(): an ffmpeg -vf chain for the uniform finishing pass

The active style is ``CHANNEL_STYLE`` env (default 'graphic-novel-disaster'),
loaded from styles/channel_styles/<name>.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_STYLE = "graphic-novel-disaster"
_STYLE_DIR = Path(__file__).resolve().parent.parent / "styles" / "channel_styles"
_cache: dict[str, dict] = {}


def load_style(name: str | None = None) -> dict[str, Any]:
    name = name or os.environ.get("CHANNEL_STYLE", DEFAULT_STYLE)
    if name not in _cache:
        p = _STYLE_DIR / f"{name}.yaml"
        _cache[name] = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _cache[name]


def prompt_suffix(name: str | None = None) -> str:
    return " ".join((load_style(name).get("prompt_style") or "").split()).strip()


def negative(name: str | None = None) -> str:
    return " ".join((load_style(name).get("negative_style") or "").split()).strip()


def apply_to_prompt(prompt: str, name: str | None = None) -> str:
    """Append the style medium to a generation prompt (idempotent-ish)."""
    suffix = prompt_suffix(name)
    if not suffix:
        return prompt
    base = (prompt or "").rstrip(". ").strip()
    return f"{base}. {suffix}" if base else suffix


# The prohibitions that actually stopped photoreal drift on taum-sauk. Gemini and
# Omni have NO separate negative-prompt channel, so `negative()` was silently
# unused for years and every ad-hoc script hand-rolled a weaker inline clause —
# the direct cause of ~10 "this doesn't match the animation style / looks too
# real" rejections. This wording (front-loaded FLAT 2D + explicit NOTs, restated
# as a hard hold) is what landed the fixes; keep it verbatim.
_HARD_MEDIUM = ("FLAT 2D hand-drawn illustration — a hand-inked comic panel, "
                "bold black ink outlines, halftone dot shading")
_HARD_DENY = ("NOT photorealistic, NOT a 3D render, NO photographic texture or "
              "realistic lighting, NO live-action look")
_FRAME_HYGIENE = ("Full-bleed: the image fills the entire frame edge to edge, "
                  "NO panel border, NO frame, NO caption, NO text")


def gen_clause(name: str | None = None, hold: bool = True) -> str:
    """The FULL prompt-side style lock for generators with no negative channel.

    Positive medium + palette (from the style file) + inlined prohibitions +
    frame hygiene. Use this instead of hand-writing a style string; a weaker,
    locally-invented clause is how style drift gets in.

    `hold=True` adds the "for the WHOLE shot" restatement that video generators
    need — Omni in particular drifts toward photorealism mid-clip on wide
    natural scenes, so the end frame must be constrained, not just the first.
    """
    parts = [_HARD_MEDIUM]
    suffix = prompt_suffix(name)
    if suffix:
        parts.append(suffix)
    parts.append(_HARD_DENY)
    if hold:
        parts.append("hold this exact medium for the WHOLE shot, start to finish")
    parts.append(_FRAME_HYGIENE)
    return " " + ". ".join(parts) + "."


def _hex(h: str) -> tuple[float, float, float]:
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return 0.0, 0.0, 0.0
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def finish_filter(name: str | None = None) -> str | None:
    """Build an ffmpeg -vf filter chain for the finishing pass, or None if disabled."""
    style = load_style(name)
    f = style.get("finishing") or {}
    if not f.get("enabled"):
        return None

    parts: list[str] = []

    contrast = f.get("contrast", 1.0)
    if contrast and contrast != 1.0:
        parts.append(f"eq=contrast={contrast}")

    if f.get("duotone"):
        pal = style.get("palette") or {}
        sr, sg, sb = _hex(pal.get("shadow", "0a1428"))
        hr, hg, hb = _hex(pal.get("highlight", "e8a44c"))
        parts.append("hue=s=0")  # desaturate to luma first
        parts.append(
            f"curves=r='0/{sr:.3f} 1/{hr:.3f}':"
            f"g='0/{sg:.3f} 1/{hg:.3f}':"
            f"b='0/{sb:.3f} 1/{hb:.3f}'"
        )

    grain = f.get("grain", 0)
    if grain:
        parts.append(f"noise=alls={int(round(float(grain) * 100))}:allf=t+u")

    return ",".join(parts) if parts else None
