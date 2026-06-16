"""First-last-frame (FLF) beats — the controllable lane for EXPLANATORY state-change shots.

Open image-to-video (Grok) structurally drifts on precise, countable, or contained-change
beats: it adds camera motion, miscounts objects, applies global washes, and breaks object
permanence (props/machines appearing and disappearing across chained legs). The fix, proven on
the seg_006 "six overdosed -> three die" beat and the seg_009 "the removed safety fuse goes
cold" beat, is to pin BOTH ends of the shot and let the model only interpolate between them:

  1. Author ONE start keyframe (the subject present / lit), in the channel style.
  2. Derive the END keyframe DETERMINISTICALLY from the start (drain to navy, dim a band) —
     NEVER a generative Nano "edit", which drifts the composition and breaks the FLF match.
  3. Kling 3.0 FLF interpolates between the two pinned, pixel-matched frames
     (lib.visual_router.generate_flf_shot -> tools.video.kling_kie_video).

Because both endpoints are hand-authored and pixel-matched, exact counts, per-object state, a
locked camera, and object permanence hold BY CONSTRUCTION — the model cannot morph, miscount,
or hallucinate. `drain_endpoint` is the deterministic endpoint author; `flf_beat` is the full
author -> derive -> interpolate -> conform recipe.

See skills/pipelines/narrated-documentary/ai-visual-director.md and
docs/research/controllable-explainer-footage-2026-06.md.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

NAVY: tuple[int, int, int] = (10, 20, 40)  # #0a1428 — the channel shadow colour
MAX_FLF_SECONDS = 15                        # Kling 3.0 clip cap; longer slots are freeze-held


def drain_endpoint(src_png: str | Path, out_png: str | Path, *,
                   drain: float = 0.8, navy: tuple[int, int, int] = NAVY,
                   band: tuple[float, float] | None = None) -> str:
    """Author an FLF END frame from the START by blending toward navy — the warm/lit subject
    goes cold and dead. The result IS the start frame darkened, so it is pixel-matched: Kling
    interpolates only the lighting change, never the composition.

    drain : 0..1 blend weight toward ``navy`` (0.8 = strong / near-dead).
    band  : ``None`` drains the WHOLE frame uniformly (e.g. seg_009: the fuse going cold).
            ``(lo, hi)`` ramps the drain over a vertical region (fractions of height): no drain
            above ``lo``, full drain below ``hi``, linear between — e.g. seg_006 ``(0.50, 0.62)``
            drains the front/bottom three figures while the back three stay lit.
    Returns the output path.
    """
    from PIL import Image

    img = Image.open(src_png).convert("RGB")
    px = img.load()
    w, h = img.size
    lo, hi = band if band else (0.0, 0.0)
    for y in range(h):
        if band:
            frac = (y / h - lo) / (hi - lo) if hi > lo else (1.0 if y / h >= lo else 0.0)
            k = max(0.0, min(1.0, frac)) * drain
        else:
            k = drain
        if k <= 0:
            continue
        for x in range(w):
            r, g, b = px[x, y]
            px[x, y] = (int(r * (1 - k) + navy[0] * k),
                        int(g * (1 - k) + navy[1] * k),
                        int(b * (1 - k) + navy[2] * k))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return str(out_png)


def _probe_duration(path: str | Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def freeze_pad(clip: str | Path, target_s: float, out: str | Path | None = None) -> str:
    """Hold the last frame to extend ``clip`` to ``target_s`` — invisible on a locked/static
    end frame (the FLF 'dead' state). Returns the clip unchanged if already long enough."""
    clip = Path(clip)
    out = Path(out) if out else clip.with_name(clip.stem + "_pad.mp4")
    cur = _probe_duration(clip)
    if cur >= target_s - 0.02:
        return str(clip)
    pad = target_s - cur
    subprocess.run(["ffmpeg", "-y", "-i", str(clip), "-vf",
                    f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out), "-loglevel", "error"],
                   check=True)
    return str(out)


def flf_beat(start_png: str | Path, prompt: str, window_s: float, out: str | Path, *,
             derive=None, mode: str = "std", aspect_ratio: str = "16:9") -> str | None:
    """The full FLF beat recipe: author -> derive -> interpolate -> conform to the slot.

    start_png : a channel-style keyframe with the subject present/lit (the FLF START).
    prompt    : the transition description (<=500 chars; describes start -> end).
    window_s  : the beat's slot length; the clip is conformed (trimmed or freeze-held) to it.
    derive    : callable(start_png, end_png) producing the DETERMINISTIC matched END frame.
                Defaults to a uniform cold-drain (``drain_endpoint``). For region transitions
                pass e.g. ``lambda s, o: drain_endpoint(s, o, band=(0.50, 0.62))``.
    Returns the conformed clip path, or None on generation failure.
    """
    from lib.image_host import upload_image
    from lib import visual_router as vr

    start_png = Path(start_png)
    end_png = start_png.with_name(start_png.stem + "_flfend.png")
    (derive or drain_endpoint)(str(start_png), str(end_png))
    start_url = upload_image(str(start_png))
    end_url = upload_image(str(end_png))
    if not (start_url and end_url):
        return None

    out = Path(out)
    if window_s <= MAX_FLF_SECONDS:
        return vr.generate_flf_shot(start_url, end_url, prompt, window_s, out,
                                    mode=mode, aspect_ratio=aspect_ratio)
    # Slot longer than one Kling clip: interpolate the full transition, then freeze the dead end.
    raw = out.with_name(out.stem + "_flfraw.mp4")
    clip = vr.generate_flf_shot(start_url, end_url, prompt, float(MAX_FLF_SECONDS), raw,
                                mode=mode, aspect_ratio=aspect_ratio)
    if clip is None:
        return None
    return freeze_pad(clip, window_s, out)
