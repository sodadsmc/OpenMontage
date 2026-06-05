"""Apply the channel finishing pass (ffmpeg) to a rendered video.

The finishing pass (duotone grade + film grain, per the active channel style) is
the uniform layer that hides AI-frame artifacts and gives the channel one
authored look. Run it as the LAST step, after concat/mux.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from lib.channel_style import finish_filter

_log = logging.getLogger(__name__)


def apply_finish(input_path: str | Path, output_path: str | Path,
                 style: str | None = None) -> str | None:
    """Apply the channel finishing filter to a video.

    Returns the output path, or the input path unchanged if the style has no
    finishing pass, or None on ffmpeg failure.
    """
    vf = finish_filter(style)
    if not vf:
        return str(input_path)  # style has no finishing pass -> passthrough

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)],
            capture_output=True, timeout=1800, check=True,
        )
        return str(out)
    except Exception as exc:  # noqa: BLE001
        _log.warning("finishing: ffmpeg failed: %s", exc)
        return None
