"""Frame hygiene: mechanical detectors for the defect classes that polish passes
kept finding by eyeball — cream panel borders, freeze tails, and 1-2 frame
stray-shot flashes.

Born from the therac-25 polish rounds (2026-07): a cream comic border rode from
one generation's keyframes into ~20 scenes of derivations; takes shorter than
their slots froze on the last frame while narration continued; and old takes
carried 1-2 frame fragments of other shots that flashed at splice seams.

All detectors are advisory — they return findings, they never modify media.
Repair helpers (`overscan_vf`) build ffmpeg filter strings; the caller decides.

CLI:
    python -m lib.frame_hygiene <clip.mp4> [--slot SECONDS]
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# A border must run along at least this many of the four edges to count —
# panel frames are full rectangles; 2-edge hits are usually scene content.
_MIN_EDGES = 3
# Cream run-length cap as a fraction of the frame: full-cream frames
# (document cards, paper diagrams) are content, not borders.
_RUN_CAP_FRAC = 0.12


def probe_duration(path: str | Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def extract_frame(src: str | Path, t: float, out: str | Path | None = None,
                  scale: str | None = None) -> Path | None:
    """Grab one frame at t (fast seek). Returns the path or None."""
    if out is None:
        out = Path(tempfile.gettempdir()) / f"_fh_{os.getpid()}_{t:.3f}.png"
    args = ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1"]
    if scale:
        args += ["-vf", f"scale={scale}"]
    args.append(str(out))
    subprocess.run(args, capture_output=True)
    return Path(out) if os.path.exists(out) else None


# ---------------------------------------------------------------------------
# borders
# ---------------------------------------------------------------------------

def last_frame(src: str | Path, out: str | Path) -> Path:
    """The clip's LITERAL final frame — the only correct anchor for a
    continuation (FLF/i2v leg) of that clip.

    Never re-extract 'the boundary' by timestamp arithmetic on a spliced
    canonical: seeking near a splice can land on the wrong side of it (a
    $0.42 FLF was burned on exactly that during the therac-25 polish).
    """
    run = subprocess.run(["ffmpeg", "-y", "-sseof", "-0.05", "-i", str(src),
                          "-frames:v", "1", str(out)], capture_output=True)
    if run.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"could not extract last frame of {src}")
    return Path(out)


def find_border_box(im: Image.Image):
    """Inner content box past navy padding + a cream comic border, or None.

    Scans inward from each edge: skips dark letterbox padding, then counts
    bright low-saturation ("cream") rows/cols. A hit needs >=3 cream px on
    >=3 edges, and the run must END within _RUN_CAP_FRAC of the frame — a
    cream run that never ends is a paper/document background, not a border.
    """
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    h, w, _ = a.shape
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = 0.45 * r + 0.45 * g + 0.10 * b
    cream = ((r > 150) & (g > 140) & (b > 105) & (np.abs(r - g) < 55) &
             (np.abs(g - b) < 80) & (r >= b - 8))
    rowc, rowl = cream.mean(1), lum.mean(1)
    colc, coll = cream.mean(0), lum.mean(0)
    cap = max(10, int(min(h, w) * _RUN_CAP_FRAC))

    def margin(cfr, lfr, n):
        i = 0
        while i < min(int(n * 0.06), n - 1) and lfr[i] < 60 and cfr[i] < 0.5:
            i += 1
        j, cnt = i, 0
        while j < n - 1 and cnt < cap and cfr[j] >= 0.55:
            j += 1
            cnt += 1
        if cnt < 3 or cnt >= cap:
            return 0, 0
        # allow the dark rule line drawn at the border's inner edge
        k = j
        while k < min(j + 6, n - 1) and cfr[k] < 0.4 and lfr[k] < 110:
            k += 1
        return k, cnt

    t_, tc = margin(rowc, rowl, h)
    b_, bc = margin(rowc[::-1], rowl[::-1], h)
    l_, lc = margin(colc, coll, w)
    r_, rc = margin(colc[::-1], coll[::-1], w)
    if sum(1 for c in (tc, bc, lc, rc) if c >= 3) < _MIN_EDGES:
        return None
    pad = 2
    x0 = l_ + pad if lc >= 3 else 0
    y0 = t_ + pad if tc >= 3 else 0
    x1 = w - (r_ + pad) if rc >= 3 else w
    y1 = h - (b_ + pad) if bc >= 3 else h
    if x1 - x0 < w * 0.6 or y1 - y0 < h * 0.6:
        return None
    return (x0, y0, x1, y1)


def rgb_stream(src: str | Path, start: float = 0.0, dur: float | None = None,
               fps: int = 4, size: tuple[int, int] = (480, 270)) -> np.ndarray:
    """Decode a window to (N, h, w, 3) uint8 RGB via one rawvideo pipe."""
    w, h = size
    args = ["ffmpeg", "-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if dur is not None:
        args += ["-t", f"{dur:.3f}"]
    args += ["-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(args, capture_output=True)
    n = len(r.stdout) // (w * h * 3)
    if n == 0:
        return np.zeros((0, h, w, 3), dtype=np.uint8)
    return np.frombuffer(r.stdout[: n * w * h * 3],
                         dtype=np.uint8).reshape(n, h, w, 3)


def video_border_hits(src: str | Path, start: float = 0.0,
                      dur: float | None = None, fps: int = 4,
                      chunk_s: float = 60.0) -> list:
    """Check EVERY frame (at fps) of a window; return [(t, box)] hits.

    Streamed, not sampled — intermittent/flickering borders live in slivers
    that point-sampling misses (proven on the therac seg_001 backup).
    """
    if dur is None:
        dur = probe_duration(src) - start
    hits = []
    c0 = start
    while c0 < start + dur - 0.05:
        clen = min(chunk_s, start + dur - c0)
        frames = rgb_stream(src, c0, clen, fps=fps)
        for i in range(len(frames)):
            box = find_border_box(Image.fromarray(frames[i]))
            if box:
                hits.append((round(c0 + i / fps, 2), box))
        c0 += clen
    return hits


def overscan_vf(pct: float = 0.045, norm: str | None = None) -> str:
    """ffmpeg -vf string cropping pct off every edge (then optional NORM chain).

    THE cure for hand-drawn borders that wobble frame to frame: detection +
    run-split cropping FLICKERS (some frames cropped, some not); a uniform
    overscan is invisible and total. Apply to the ORIGINAL, never to an
    already-partially-cropped output (that double-crops -> zoom jumps).
    """
    vf = f"crop=iw*{1 - 2 * pct}:ih*{1 - 2 * pct}:iw*{pct}:ih*{pct}"
    return vf + ("," + norm if norm else "")


# ---------------------------------------------------------------------------
# freeze tails + flash frames (one gray-stream pass)
# ---------------------------------------------------------------------------

def gray_stream(src: str | Path, start: float = 0.0, dur: float | None = None,
                fps: int = 30, size: tuple[int, int] = (96, 54)) -> np.ndarray:
    """Decode a window to a (N, h, w) uint8 gray array via one rawvideo pipe."""
    w, h = size
    args = ["ffmpeg", "-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if dur is not None:
        args += ["-t", f"{dur:.3f}"]
    args += ["-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"]
    r = subprocess.run(args, capture_output=True)
    n = len(r.stdout) // (w * h)
    if n == 0:
        return np.zeros((0, h, w), dtype=np.uint8)
    return np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w)


def motion_floor(src: str | Path, frame_floor: float = 0.4, span_floor: float = 1.5,
                 fps: int = 6, size: tuple[int, int] = (320, 180)):
    """Detect a beat that is ESSENTIALLY A STILL — the third QC leg alongside
    `zoom_still` (fake/push motion) and `freeze_tail` (dead tail). This one
    catches a beat that isn't frozen and isn't a push but has so little real
    change it reads dead (deterministic lake shimmer, a blinking beacon on a
    held frame). The operator caught several of these across taum acts 4–5; the
    freeze/zoom scans passed them all.

    Uses TWO signals, because consecutive-frame delta ALONE undercounts slow-
    but-real motion (a wall sagging over 7s scores frame≈0.2 yet clearly moves —
    its START→END delta is large). A beat is "dead" only when BOTH are low:
      - frame delta (mean |Δ| between consecutive sampled frames)  < frame_floor
      - span  delta (|last − first| frame)                         < span_floor
    Calibrated on taum: dead inserts read (0.13, 0.35); an accepted slow sag
    reads (0.21, 5.3); accepted grok footage (1.4, 12+). Returns
    (frame_mean, span_delta, reads_dead).

    This is a COARSE aid, not an authority: a LONG hero/establishing shot with
    technically-nonzero-but-tiny motion can still read static to the operator
    (a role/duration judgment no single metric replicates — see the
    'deterministic shimmer is not animated footage' rule). Exempt cards,
    diagrams, and close quiet inserts at the call site.
    """
    d = probe_duration(src)
    g = gray_stream(src, start=0.0, dur=d, fps=fps, size=size).astype(float)
    if len(g) < 2:
        return 0.0, 0.0, True
    frame_mean = float(np.abs(np.diff(g, axis=0)).mean())
    span_delta = float(np.abs(g[-1] - g[0]).mean())
    reads_dead = frame_mean < frame_floor and span_delta < span_floor
    return frame_mean, span_delta, reads_dead


def zoom_still(src: str | Path, size: tuple[int, int] = (320, 180)):
    """Detect a Ken-Burns push impersonating motion: a slow zoom on a STILL
    generates healthy frame deltas that fool naive freeze/motion scans (this
    certified two crashed push-fallback artifacts as 'animated' on taum act-4;
    the operator's eyes caught them, the metric did not).

    Method: take frames at 10%/90% of the clip; try to EXPLAIN the late frame
    as a centered zoom (1.00-1.16x) + small shift of the early frame. If the
    best fit removes most of the apparent motion, nothing in the scene actually
    moved — it's a zoom-on-still.

    Returns (verdict, raw_delta, residual) with verdict in
    {'static', 'zoom-still', 'real'}.
    """
    from PIL import Image as _Im
    d = probe_duration(src)
    w, h = size
    fr = []
    for t in (d * 0.1, d * 0.9):
        f = gray_stream(src, start=t, dur=0.05, fps=30, size=size)
        if len(f) == 0:
            return "real", 0.0, 0.0   # unreadable — don't false-flag
        fr.append(f[0].astype(float))
    f0, f1 = fr
    raw = float(np.abs(f1 - f0).mean())
    if raw < 0.5:
        return "static", raw, raw
    best = raw
    im0 = _Im.fromarray(f0.astype(np.uint8))
    for z in np.linspace(1.0, 1.16, 17):
        zw, zh = int(round(w * z)), int(round(h * z))
        big = np.asarray(im0.resize((zw, zh), _Im.BILINEAR), float)
        x0, y0 = (zw - w) // 2, (zh - h) // 2
        for dx in (-3, 0, 3):
            for dy in (-2, 0, 2):
                xx, yy = x0 + dx, y0 + dy
                if xx < 0 or yy < 0 or xx + w > zw or yy + h > zh:
                    continue
                res = float(np.abs(f1 - big[yy:yy + h, xx:xx + w]).mean())
                if res < best:
                    best = res
    if best < 0.38 * raw and raw > 1.2:
        return "zoom-still", raw, best
    return "real", raw, best


def freeze_tail(src: str | Path, slot_end: float | None = None,
                window_s: float = 8.0, still_thresh: float = 0.35,
                min_freeze_s: float = 1.5, fps: int = 6):
    """Return (freeze_onset_s, freeze_len_s) if the clip goes static before its
    end and stays static for >= min_freeze_s, else None.

    A deliberate hold on a CARD (title/stat frames) is also "static" — callers
    should skip card/diagram scenes or treat findings there as advisory.
    """
    end = slot_end or probe_duration(src)
    start = max(0.0, end - window_s)
    g = gray_stream(src, start, end - start, fps=fps).astype(np.int16)
    if len(g) < fps:
        return None
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    still = d < still_thresh
    # longest static run that reaches the end
    i = len(still)
    while i > 0 and still[i - 1]:
        i -= 1
    # k still-diffs span k+1 identical frames
    run = (len(still) - i + 1) / fps if len(still) > i else 0.0
    if run >= min_freeze_s:
        return (round(start + i / fps, 2), round(run, 2))
    return None


def static_runs(src: str | Path, start: float = 0.0, dur: float | None = None,
                still_thresh: float = 0.35, min_run_s: float = 3.0,
                fps: int = 6) -> list:
    """ALL static stretches >= min_run_s in a window: [(t_start, run_s)].

    Advisory — designed card holds are static too. But every freeze the
    operator flagged was 3s+ of dead video mid-scene, so surfacing long holds
    with timestamps is exactly the watch-through checklist.
    """
    g = gray_stream(src, start, dur, fps=fps).astype(np.int16)
    if len(g) < fps:
        return []
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    still = d < still_thresh
    runs, i = [], 0
    while i < len(still):
        if still[i]:
            j = i
            while j < len(still) and still[j]:
                j += 1
            # j - i still-diffs span j - i + 1 identical frames
            if (j - i + 1) / fps >= min_run_s:
                runs.append((round(start + i / fps, 2),
                             round((j - i + 1) / fps, 2)))
            i = j
        else:
            i += 1
    return runs


def flash_frames(src: str | Path, start: float = 0.0, dur: float | None = None,
                 fps: int = 30, spike: float = 18.0, max_len: int = 20,
                 similar: float = 6.0) -> list:
    """Find stray-shot fragments up to max_len frames: an A->X cut followed
    by X->B where B is nearly identical to A (the A-X-A signature of a foreign
    fragment left in a take). Returns [(t_seconds, len_frames)].

    Real-world fragments ran 6-15 frames on the therac takes — max_len must
    cover that, not just single frames.
    """
    g = gray_stream(src, start, dur, fps=fps).astype(np.int16)
    if len(g) < 4:
        return []
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    hits = []
    i = 0
    while i < len(d):
        if d[i] >= spike:
            for k in range(1, max_len + 1):
                j = i + k
                if j >= len(d):
                    break
                if d[j] >= spike:
                    a, b = g[i], g[j + 1]
                    if np.abs(a - b).mean() <= similar:
                        hits.append((round(start + (i + 1) / fps, 3), k))
                    break
        i += 1
    return hits


def _ncorr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else 0.0


def seam_jumps(src: str | Path, start: float = 0.0, dur: float | None = None,
               fps: int = 30, spike: float = 16.0, sharp: float = 2.5,
               min_corr: float = 0.55) -> list:
    """Cuts where BOTH sides show the same staging redrawn — chain seams and
    skip-jumps: [(t_seconds, corr)].

    Metric (ground-truthed on the seg_009 seams): a cut is a SHARP single-frame
    discontinuity (diff >= spike AND >= sharp x the surrounding median — fast
    motion elevates diffs across a run, a cut doesn't); across a chain seam the
    LAYOUT survives, so zero-mean correlation of the frames flanking the cut
    stays high (0.64 on the real seam) while a legit scene cut lands near zero
    (0.07-0.17). Absolute pixel difference can NOT separate these — redraws
    shift shading everywhere (cross-diff 31 on the seam vs 75 on a scene cut).
    """
    g = gray_stream(src, start, dur, fps=fps).astype(np.int16)
    if len(g) < 10:
        return []
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    hits = []
    for i in range(3, len(d) - 4):
        lo, hi = max(0, i - 15), min(len(d), i + 16)
        ctx = float(np.median(np.delete(d[lo:hi], i - lo)))
        if d[i] >= spike and d[i] >= sharp * max(ctx, 0.5):
            c = _ncorr(g[i - 2], g[i + 3])
            if c >= min_corr:
                hits.append((round(start + (i + 1) / fps, 3), round(c, 2)))
    return hits


def double_cuts(src: str | Path, start: float = 0.0, dur: float | None = None,
                fps: int = 30, spike: float = 18.0,
                max_gap_s: float = 0.8) -> list:
    """Two hard cuts within max_gap_s: [(t_first_cut, gap_frames)].

    The transition-point stray fragment (A -> foreign X -> B where B differs
    from A) has NO A-X-A signature — both therac flashes (male operator @1.8,
    vintage room @9.2) were exactly this. Legit edits rarely cut twice inside
    0.8s outside deliberate montage, so this is an ADVISORY signal: montage
    scenes will list their own fast cuts.
    """
    g = gray_stream(src, start, dur, fps=fps).astype(np.int16)
    if len(g) < 4:
        return []
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    cuts = [i for i in range(len(d)) if d[i] >= spike]
    max_gap = int(max_gap_s * fps)
    hits = []
    for a, b in zip(cuts, cuts[1:]):
        if 0 < b - a <= max_gap:
            hits.append((round(start + (a + 1) / fps, 3), b - a))
    return hits


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Frame hygiene checks on one clip "
                                             "or still image")
    ap.add_argument("clip", help="video clip OR still image (.png/.jpg)")
    ap.add_argument("--slot", type=float, default=None,
                    help="slot length the clip must fill (freeze check vs this)")
    args = ap.parse_args()
    clip = args.clip
    if str(clip).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        # still-intake hygiene: check BEFORE a still becomes a master/gold ref
        box = find_border_box(Image.open(clip).convert("RGB"))
        print(f"{os.path.basename(clip)}: "
              + (f"BORDER {box} — crop before deriving anything from this "
                 f"(or overscan_vf)" if box else "clean"))
        return
    dur = probe_duration(clip)
    print(f"{os.path.basename(clip)}: {dur:.2f}s")
    borders = video_border_hits(clip)
    print(f"  borders: {borders if borders else 'clean'}")
    fz = freeze_tail(clip, slot_end=args.slot or dur)
    print(f"  freeze tail: {fz if fz else 'none'}")
    fl = flash_frames(clip)
    print(f"  flash frames: {fl if fl else 'none'}")
    if args.slot and dur < args.slot - 0.05:
        print(f"  SHORT of slot by {args.slot - dur:.2f}s (will freeze-pad)")


if __name__ == "__main__":
    _main()
