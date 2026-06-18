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


AMBER_RGB: tuple[int, int, int] = (232, 164, 76)   # #e8a44c — channel amber
CREAM_RGB: tuple[int, int, int] = (230, 220, 198)   # #e6dcc6
_GLYPH_FONT = "C:/Windows/Fonts/arialbd.ttf"


def _resolve_color(c) -> tuple[int, int, int]:
    if isinstance(c, (tuple, list)) and len(c) == 3:
        return tuple(int(x) for x in c)  # type: ignore[return-value]
    s = str(c or "amber").strip().lower()
    if s == "cream":
        return CREAM_RGB
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    return AMBER_RGB


def composite_text(src_png: str | Path, out_png: str | Path, text: str,
                   box: tuple[float, float, float, float], *,
                   color="amber", font_path: str = _GLYPH_FONT) -> str:
    """Draw ``text`` fitted into ``box`` (x, y, w, h as FRACTIONS of the frame) on a COPY of
    ``src_png`` — the matched-frame technique for a CONTENT state-morph (X->E, an error code
    appearing). The START frame carries the 'before' glyph and the END a pixel-matched copy with
    the 'after', so Kling interpolates only the glyph, never the composition. Empty text = a clean
    copy (e.g. the START before a code appears). Returns ``out_png``.

    Why composite, not a generative edit: a Nano 'edit' of the base redraws the whole frame and
    breaks the FLF pixel-match (and Grok paints garbled screen text) — drawing the glyph
    deterministically is the only drift-free way to carry legible on-screen text.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(src_png).convert("RGB")
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()
    if not text:
        img.save(out_png)
        return str(out_png)

    w, h = img.size
    bx, by, bw, bh = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    draw = ImageDraw.Draw(img)
    size = max(8, int(bh))
    font = None
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception:
        font = ImageFont.load_default()

    def _measure(f):
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        return r - l, b - t

    tw, th = _measure(font)
    while (tw > bw or th > bh) and size > 8:
        size -= 2
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            break
        tw, th = _measure(font)

    draw.text((bx + bw / 2, by + bh / 2), text, font=font, fill=_resolve_color(color),
              anchor="mm", stroke_width=max(1, size // 18), stroke_fill=NAVY)
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


def flf_segment(spec, window_s: float, out: str | Path, *, keyframe_dir: str | Path,
                bible=None, mode: str = "std") -> str | None:
    """Generate one EXPLANATORY beat from a ``lib.scored_script.FLFSpec`` — the lane the
    assembly auto-routes ``visual.flf`` beats to (the analogue of the Manim/diagram lane).

    Authors the START keyframe via Nano Banana in the channel style — grounded on a bible
    canonical when ``spec.anchor`` is an asset_id (else fresh) — derives the deterministic END,
    interpolates via Kling 3.0 FLF, and conforms to ``window_s``. Returns the UN-finished clip
    path (the episode finishing pass is applied once at assembly), or None on failure.
    """
    from lib import visual_router as vr
    from lib import channel_style

    keyframe_dir = Path(keyframe_dir)
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    out = Path(out)
    start_png = keyframe_dir / f"{out.stem}_flf_start.png"

    refs = None
    if spec.anchor and spec.anchor != "fresh" and bible is not None:
        ref = bible.resolve_reference_image(spec.anchor)
        if ref and Path(ref).exists():
            from lib.image_host import upload_image
            hosted = upload_image(str(ref))
            refs = [hosted] if hosted else None
    kf = vr._nano_image(channel_style.apply_to_prompt(spec.start_prompt), start_png, image_urls=refs)
    if kf is None:
        return None
    # _nano_image may return a provider URL; pull it local so drain_endpoint can author the end.
    if str(kf).startswith(("http://", "https://")):
        import requests
        start_png.write_bytes(requests.get(str(kf), timeout=60).content)
    else:
        start_png = Path(str(kf))

    # CONTENT state-morph (a legible glyph/text change): composite the changed element onto
    # matched START/END frames. Otherwise: derive the END by draining toward navy.
    morph = getattr(spec, "morph", None)
    if morph:
        from lib.image_host import upload_image
        s_png = keyframe_dir / f"{out.stem}_flf_start.png"
        e_png = keyframe_dir / f"{out.stem}_flf_end.png"
        box = tuple(morph.get("box") or (0.40, 0.42, 0.20, 0.16))
        composite_text(start_png, s_png, morph.get("start_text", ""), box, color=morph.get("color", "amber"))
        composite_text(start_png, e_png, morph.get("end_text", ""), box, color=morph.get("color", "amber"))
        su, eu = upload_image(str(s_png)), upload_image(str(e_png))
        if not (su and eu):
            return None
        if window_s <= MAX_FLF_SECONDS:
            return vr.generate_flf_shot(su, eu, spec.transition, window_s, out, mode=mode)
        raw = out.with_name(out.stem + "_flfraw.mp4")
        clip = vr.generate_flf_shot(su, eu, spec.transition, float(MAX_FLF_SECONDS), raw, mode=mode)
        return freeze_pad(clip, window_s, out) if clip else None

    return flf_beat(start_png, spec.transition, window_s, out, mode=mode,
                    derive=lambda s, o: drain_endpoint(s, o, drain=spec.drain, band=spec.band))


def flf_beats_in_scope(script, seg_ids=None) -> list:
    """The segments routed to the FLF lane (visual.flf set), optionally limited to seg_ids."""
    return [s for s in script.segments
            if getattr(s.visual, "flf", None) is not None
            and (seg_ids is None or s.id in seg_ids)]


def generate_flf_segments(script, dm, out_dir: str | Path, *, bible=None,
                          keyframe_dir: str | Path | None = None, seg_ids=None) -> dict:
    """Generate every FLF beat in scope to ``out_dir/{seg_id}.mp4`` — the controllable lane,
    the analogue of the Manim diagram lane. Idempotent: an existing clip is reused, so this is
    safe to call on every assembly. PAID (Nano + Kling), so gate the CALL behind --yes.

    ``dm`` is a duration map (``dm.get_segment(id).total_duration_s`` = the slot). Returns
    ``{seg_id: clip_path_or_None}``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keyframe_dir = Path(keyframe_dir) if keyframe_dir else out_dir / "_keyframes"
    results: dict[str, str | None] = {}
    for seg in flf_beats_in_scope(script, seg_ids):
        out = out_dir / f"{seg.id}.mp4"
        if out.exists():
            results[seg.id] = str(out)
            continue
        ts = dm.get_segment(seg.id)
        slot = ts.total_duration_s if ts else 5.0
        results[seg.id] = flf_segment(seg.visual.flf, slot, out,
                                      keyframe_dir=keyframe_dir, bible=bible)
    return results
