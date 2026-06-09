"""Burn a styled caption over a clip — the "text over footage" treatment.

A dark band across the lower frame + caption lines fading in sequentially: the
emphasis line in warm amber and large, the rest in cream. Channel palette, so the
text reads as part of the graphic-novel piece rather than a slate cutaway.

The visual keeps PLAYING under the caption (no static text card).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)

AMBER = "0xe8a44c"
CREAM = "0xe6dcc6"
_DEFAULT_FONT = "C:/Windows/Fonts/arialbd.ttf"
_FALLBACK_FONT = "C:/Windows/Fonts/arial.ttf"


def _san(s: str) -> str:
    """Escape/strip characters that break ffmpeg drawtext. The value is single-quoted
    (so commas are safe), but a literal colon, apostrophe, or backslash is not — the
    colon must be escaped as \\: even inside quotes."""
    return s.replace("\\", "").replace("'", "").replace(":", r"\:").strip()


def apply_text_overlay(src, out, lines, emphasis: int = -1,
                       font: str | None = None, fade: float = 0.5):
    """Overlay caption `lines` on the clip `src` -> `out`. `emphasis` is the index
    of the amber/large line (-1 = all equal weight). Returns the out Path or None.

    ffmpeg's drawtext can't parse a Windows font path's drive colon, so we run with
    cwd = the font's directory and reference it by basename (a colon-free relative
    path), while src/out stay absolute.
    """
    # Resolve to ABSOLUTE paths first: we run ffmpeg with cwd=the font's directory
    # (so the font is referenceable by colon-free basename), which would otherwise
    # reinterpret any relative src/out path against C:/Windows/Fonts and fail.
    src = Path(src).resolve()
    out = Path(out).resolve()
    lines = [str(l) for l in (lines or []) if l and str(l).strip()]
    if not lines:
        return None
    fp = Path(font or _DEFAULT_FONT)
    if not fp.exists():
        fp = Path(_FALLBACK_FONT)
    if not fp.exists():
        _log.warning("text_overlay: no usable font found")
        return None
    cwd, fname = str(fp.parent), fp.name

    n = len(lines)
    step = 80
    band_h = 130 + n * step
    big, small = 92, 50

    parts = [f"drawbox=x=0:y=ih-{band_h}:w=iw:h={band_h}:color=0x0a1428@0.58:t=fill"]
    margin = 140
    avail = 1920 - 2 * margin
    for i, line in enumerate(lines):
        emph = (i == emphasis)
        target = big if emph else small
        txt = _san(line)
        # Auto-fit the font to the frame width so long captions never clip
        # (estimate from the DISPLAY length, before colon-escaping inflates it).
        size = max(30, min(target, int(avail / (max(1, len(line)) * 0.82))))
        color = AMBER if emph else CREAM
        t0 = 0.3 + i * 0.45
        const = band_h - 70 - i * step      # y = h - const  (steps downward per line)
        parts.append(
            f"drawtext=fontfile={fname}:text='{txt}':fontcolor={color}:"
            f"fontsize={size}:x={margin}:y=h-{const}:"
            f"alpha='max(0,min((t-{t0:.2f})/{fade},1))'"
        )
    vf = ",".join(parts)

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)],
            capture_output=True, timeout=300, check=True, cwd=cwd,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("text_overlay: ffmpeg failed: %s", exc)
        return None
    return out
