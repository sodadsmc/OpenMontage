"""Hand-drawn (graphic-novel) info diagrams in the channel palette.

Replaces the clean Manim animations with wobbly, hand-inked diagrams rendered via
matplotlib's xkcd() sketch mode + the Ink Free handwriting font, in navy+amber, then
run through the channel finishing pass (lib.finishing) so they sit in the exact same
grade as the AI footage. The goal is "a page torn out of the graphic novel", not a
clean slide dropped into it.

Each diagram is a `draw(ax, t, dur)` callback that paints the frame at time t in
[0, dur]; `render_template()` rasterizes frames -> ffmpeg (CFR) -> finishing -> mp4 at
the exact slot length. No LaTeX, no node, no Manim — pure matplotlib + ffmpeg.

Output contract: 1920x1080 @ 30fps CFR (the timeline format). Frames are still
DRAWN at 15fps and duplicated to 30 by ffmpeg — see render_template for why.

Scenes come from two places: the hand-coded SCENES below, and model-generated
callbacks from lib.diagram_codegen (registered via register_scene, or passed to
render_template directly as a callable).

CLI:
  python -m lib.sketch_diagrams byte_overflow 29.7 out.mp4 [--fps 15] [--raw]
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib import patheffects as _pe
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

_log = logging.getLogger(__name__)

# ---- palette (channel: navy + amber duotone) -------------------------------
NAVY = "#0a1428"
NAVY2 = "#13233d"
AMBER = "#e8a44c"
AMBER_HOT = "#f6c06a"   # emphasis / danger (stays amber after duotone)
AMBER_D = "#a8742c"
CREAM = "#e6dcc6"
MUTE = "#8a93a3"        # source footer / de-emphasized

# ---- handwriting font (much better than the Comic Sans xkcd default) -------
_HAND = "DejaVu Sans"
for _cand, _fam in (("C:/Windows/Fonts/Inkfree.ttf", "Ink Free"),
                    ("C:/Windows/Fonts/segoesc.ttf", "Segoe Script"),
                    ("C:/Windows/Fonts/comic.ttf", "Comic Sans MS")):
    if Path(_cand).exists():
        try:
            font_manager.fontManager.addfont(_cand)
            _HAND = _fam
            break
        except Exception:  # noqa: BLE001
            pass


# ---- small animation helpers ----------------------------------------------
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def reveal(t: float, t0: float, d: float = 0.5) -> float:
    """0 before t0, eased 0->1 over d seconds."""
    x = clamp((t - t0) / max(1e-6, d))
    return x * x * (3 - 2 * x)  # smoothstep


def pulse(t: float, t0: float, d: float = 0.6) -> float:
    """A 0->1->0 hump centred just after t0 (for pops/flashes)."""
    if t < t0 or t > t0 + d:
        return 0.0
    x = (t - t0) / d
    return float(np.sin(np.pi * x))


def text(ax, x, y, s, size, color=CREAM, alpha=1.0, ha="center", va="center",
         stroke=0.0):
    """Hand-drawn text. By default CRISP (no stroke) so small lines stay thin and
    legible — xkcd mode's global white-halo stroke is overridden per-artist. Pass
    stroke>0 only for big display text that wants a subtle navy outline."""
    if alpha <= 0.01 or not s:
        return
    txt = ax.text(x, y, s, ha=ha, va=va, color=color, fontsize=size,
                  alpha=clamp(alpha), family=_HAND, zorder=5)
    if stroke and stroke > 0:
        txt.set_path_effects([_pe.withStroke(linewidth=stroke, foreground=NAVY),
                              _pe.Normal()])
    else:
        txt.set_path_effects([_pe.Normal()])  # crisp/thin, no inherited white halo


def footer(ax, t, src="Source: Leveson & Turner, IEEE Computer, 1993"):
    # cream (not muted grey) at moderate alpha so the citation is actually legible
    # against the indigo, while still reading as a subordinate footnote.
    text(ax, 5, 0.34, src, 19, CREAM, alpha=reveal(t, 0.8, 0.8) * 0.62)


def box(ax, cx, cy, w, h, edge=AMBER, fill=NAVY2, fill_alpha=0.55, lw=3,
        alpha=1.0, round_pad=0.06):
    if alpha <= 0.01:
        return
    p = FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                       boxstyle=f"round,pad={round_pad}",
                       linewidth=lw, edgecolor=edge,
                       facecolor=fill, alpha=clamp(alpha), zorder=3)
    p.set_mutation_aspect(0.5)
    ax.add_patch(p)


def arrow(ax, x0, y0, x1, y1, color=AMBER, lw=3, alpha=1.0):
    if alpha <= 0.01:
        return
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                alpha=clamp(alpha), mutation_scale=22), zorder=4)


def flash(ax, cx, cy, t, t0, r0=0.9, r1=1.7, n=12, color=AMBER_HOT, d=0.6):
    a = pulse(t, t0, d)
    if a <= 0.01:
        return
    for k in range(n):
        ang = k * 2 * np.pi / n
        ax.plot([cx + r0 * np.cos(ang), cx + r1 * np.cos(ang)],
                [cy + r0 * np.sin(ang), cy + r1 * np.sin(ang)],
                color=color, lw=3, alpha=a, zorder=6, solid_capstyle="round")


# ---- motion primitives (fired beams, energy flow, scatter, pulse rings) -----
# These add purposeful MOTION to a diagram beat: a beam that draws out like it
# was fired, energy flowing along it, rays scattering, and a ring pulse on the
# object that matters. They keep a static schematic feeling alive and let a beat
# land its emphasis on the narrated word.

def fired_beam(ax, x0, x1, y, t, t0, color=AMBER, lw=5, dur=0.45, alpha=1.0):
    """A beam DRAWN from x0 to x1 over `dur` (fired), bright head leading, an
    arrowhead landing on arrival — far more alive than a fade-in."""
    if t < t0 or alpha <= 0.01:
        return
    prog = clamp((t - t0) / dur)
    xt = x0 + (x1 - x0) * prog
    ax.plot([x0, xt], [y, y], color=color, lw=lw, alpha=alpha,
            solid_capstyle="round", zorder=4)
    if prog < 1.0:
        ax.scatter([xt], [y], s=110, c=AMBER_HOT, alpha=alpha, zorder=6, linewidths=0)
    else:
        ax.annotate("", xy=(x1, y), xytext=(x1 - 0.22, y),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                    mutation_scale=20, alpha=alpha), zorder=4)


def beam_flow(ax, x0, x1, y, t, t0, color=AMBER_HOT, period=0.7, alpha=1.0):
    """A bright COMET pulse looping along an already-fired beam — reads as energy
    flow. Drawn as a few trailing dots so it registers (a single dot got lost)."""
    if alpha <= 0.01 or t < t0:
        return
    for i, lag in enumerate((0.0, 0.07, 0.14)):
        ph = (((t - t0) - lag) % period) / period
        xt = x0 + (x1 - x0) * ph
        ax.scatter([xt], [y], s=150 - i * 45, c=color,
                   alpha=alpha * (0.95 - i * 0.28), zorder=6, linewidths=0)


def scatter_rays(ax, x0, y0, t, t0, color=AMBER, alpha=1.0, n=9, dur=0.55,
                 reach=3.0, spread=1.15):
    """Shimmering rays fanning out from (x0,y0) — e.g. x-rays scattering off a
    target. The rays flicker over time so the scatter feels live."""
    if t < t0 or alpha <= 0.01:
        return
    prog = clamp((t - t0) / dur)
    rs = np.random.RandomState(7)
    for k in range(n):
        a = (k / (n - 1) - 0.5) * spread + (rs.rand() - 0.5) * 0.18
        r = reach * (0.62 + 0.38 * rs.rand()) * prog
        flick = 0.65 + 0.35 * np.sin(t * 13 + k * 1.7)
        ax.plot([x0, x0 + r * np.cos(a)], [y0, y0 + r * np.sin(a)],
                color=color, lw=2, alpha=alpha * 0.9 * flick,
                solid_capstyle="round", zorder=4)


def pulse_glow(ax, cx, cy, t, t0, color=AMBER_HOT, d=1.3, rmax=1.5, alpha=1.0):
    """A clearly-visible expanding RING (+ soft fill) at (cx,cy) — a 'this
    matters' beat (danger, impact, emphasis). The ring reads where a faint glow
    did not."""
    a = pulse(t, t0, d)
    if a <= 0.01:
        return
    r = rmax * (0.25 + 0.75 * a)
    ax.add_patch(Circle((cx, cy), r, fill=False, edgecolor=color, lw=4.5 * a,
                        alpha=0.85 * a * alpha, zorder=6))
    ax.scatter([cx], [cy], s=2400 * a, c=color, alpha=0.16 * a * alpha,
               zorder=2, linewidths=0)


def _new_ax(fig):
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_facecolor(NAVY)
    return ax


def _hex(s):
    s = s.lstrip("#")
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0


def paint_background(ax, style="inked"):
    """Paint the inked-page backdrop so the diagram matches the footage rather than
    sitting on a flat digital field. 'inked' = lifted-indigo radial gradient (vignette
    to near-black at the corners) + a faint halftone dot field, echoing the
    graphic-novel canonical art. 'flat' = the old single-colour navy."""
    if style == "flat":
        return
    n = 220
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.sqrt((xx - n / 2) ** 2 + (yy - n / 2) ** 2) / (n * 0.62)
    r = np.clip(r, 0, 1) ** 1.3
    c_mid = _hex("1b2848")   # lifted indigo centre (matches footage mid-navy)
    c_edge = _hex("070d1c")  # near-black corners (vignette)
    img = c_mid[None, None, :] * (1 - r[..., None]) + c_edge[None, None, :] * r[..., None]
    ax.imshow(img, extent=[0, 10, 0, 10], origin="lower", zorder=0,
              aspect="auto", interpolation="bilinear")
    if style == "inked":
        gx, gy = np.meshgrid(np.linspace(0.25, 9.75, 46), np.linspace(0.2, 9.8, 26))
        jit = (np.sin(gx * 7.1) + np.cos(gy * 5.3)) * 0.02
        ax.scatter((gx + jit).ravel(), (gy + jit).ravel(), s=7, c="#2b3c60",
                   alpha=0.16, marker="o", linewidths=0, zorder=0.5)


# ===========================================================================
# SCENE: byte_overflow (seg_028, ~29.7s) — the 252->255->0 rollover
# ===========================================================================
def _bits(val: int):
    return [(val >> (7 - i)) & 1 for i in range(8)]


def draw_byte_overflow(ax, t, dur):
    """Timed to seg_028's narration (assets/audio_v6/seg_028.alignment.json,
    slot 30.05s). The bug is TOLD in order — so the diagram builds in order:
      0.2 second bug / 1.6 arithmetic overflow | 4.3 setup routine (loop)
      7.5 tracks one safety check | 10.0 counter called Class3 (252 appears)
      12.8 single byte (bit row) | 14.2 ONE byte (punch) | 16.8 max 255
      19.0-22.1 every pass +1 (252->253->254) | 22.7 hits 255 (hot)
      24.8 the next push | 25.9 didn't raise an error | 27.7 carry ripples
      the byte to zero | 29.1 "zero" lands (hot 0 pops).
    The false-'safe' consequence is seg_029 (false_safe) — not preempted here."""
    T_LOOP, T_CHECK, T_CLASS3 = 4.28, 7.51, 9.97
    T_BYTE, T_ONE, T_MAX = 12.83, 14.15, 16.75
    TICKS = [20.8, 21.7, 22.9]            # 253, 254, 255 ("pushed it up by one")
    T_PUSH, T_NOERR, T_ROLL, T_ZERO = 24.76, 25.94, 27.66, 29.13

    # ---- title + subtitle, on "the second bug" / "arithmetic overflow" ----
    text(ax, 5, 9.25, "THE SECOND BUG", 48, AMBER, alpha=reveal(t, 0.3, 0.6),
         stroke=1.6)
    text(ax, 5, 8.45, "arithmetic overflow", 26, CREAM,
         alpha=reveal(t, 1.63, 0.6))

    # ---- 4.3 "deep in the setup routine": a loop, running the whole time ----
    lp = reveal(t, T_LOOP, 0.7)
    if lp > 0.01:
        # canvas is 10x10 units on a 16:9 frame — shrink rx so the loop reads
        # as a CIRCLE on screen, not a squashed C
        lx, ly, ry = 1.85, 5.05, 0.62
        rx = ry * 0.5625
        spin = max((pulse(t, tk - 0.35, 0.7) for tk in TICKS), default=0.0)
        spin = max(spin, pulse(t, T_PUSH, 0.7))
        th = np.linspace(0.6, 2 * np.pi - 0.6, 40)
        ax.plot(lx + rx * np.cos(th), ly + ry * np.sin(th), color=AMBER,
                lw=3.5 + 2.5 * spin, alpha=lp, zorder=4,
                solid_capstyle="round")
        # arrowhead at the arc's end, pointing along the direction of travel
        ae = 2 * np.pi - 0.6
        ax.annotate("", xy=(lx + rx * np.cos(ae), ly + ry * np.sin(ae)),
                    xytext=(lx + rx * np.cos(ae - 0.45), ly + ry * np.sin(ae - 0.45)),
                    arrowprops=dict(arrowstyle="-|>", color=AMBER, lw=3.5,
                                    mutation_scale=26, alpha=lp), zorder=4)
        text(ax, lx, ly + ry + 0.55, "SETUP ROUTINE", 20, CREAM, alpha=lp)
        # 7.5 "tracked one safety check"
        chk = reveal(t, T_CHECK, 0.7)
        text(ax, lx, ly - ry - 0.42, "tracks one", 18, CREAM, alpha=chk * 0.9)
        text(ax, lx, ly - ry - 0.82, "safety check", 18, CREAM, alpha=chk * 0.9)

    # ---- 10.0 "a counter called Class Three": the big value appears --------
    # counter value over time: 252 until the narrated +1 passes tick it up
    if t < TICKS[0]:
        val = 252
    elif t < TICKS[1]:
        val = 253
    elif t < TICKS[2]:
        val = 254
    elif t < T_ROLL + 0.85:
        val = 255
    else:
        val = 0
    hot = val in (255, 0) and t >= TICKS[2]
    vcol = AMBER_HOT if hot else AMBER

    appear = reveal(t, T_CLASS3, 0.6)
    cx, cy = 5.55, 5.05
    text(ax, cx, cy + 1.72, "a counter called  Class3", 24, CREAM,
         alpha=appear * 0.9)
    pop = max((pulse(t, tk, 0.35) for tk in TICKS), default=0.0)
    pop = max(pop, 1.3 * pulse(t, T_ZERO, 0.6))
    # crossfade 255 -> 0 across the rollover instead of a hard swap
    roll_f = reveal(t, T_ROLL + 0.55, 0.45)
    if 0.01 < roll_f < 0.99:
        text(ax, cx, cy, "255", 150, AMBER_HOT, alpha=appear * (1 - roll_f),
             stroke=2.0)
        text(ax, cx, cy, "0", 150 + 26 * pop, AMBER_HOT, alpha=appear * roll_f,
             stroke=2.0)
    else:
        text(ax, cx, cy, str(val), 150 + 26 * pop, vcol, alpha=appear,
             stroke=2.0)

    # quiver at the ceiling: 255 is a wall, not a resting value
    if t >= TICKS[2] + 0.4 and t < T_ROLL and appear > 0.5:
        qx = 0.03 * np.sin(t * 15.0)
        text(ax, cx + qx, cy, "255", 150, AMBER_HOT, alpha=0.25, stroke=0.0)

    # "+1" pips: one per narrated pass, and the fatal push that overflows
    for tk in TICKS:
        pa = pulse(t, tk, 0.9)
        if pa > 0.01:
            text(ax, cx + 1.95, cy + 1.05 + 0.35 * pa, "+1", 26, AMBER,
                 alpha=pa)
    fp = reveal(t, T_PUSH, 0.4) * (1.0 - reveal(t, T_ROLL + 0.9, 0.5))
    if fp > 0.01:
        text(ax, cx + 1.95, cy + 1.05, "+1", 32 + 6 * pulse(t, T_PUSH, 0.5),
             AMBER_HOT, alpha=fp, stroke=1.2)
    # 25.9 "didn't raise an error"
    noerr = reveal(t, T_NOERR, 0.6) * (1.0 - reveal(t, T_ROLL + 0.9, 0.6))
    text(ax, cx, cy - 1.55, "no error raised", 22, CREAM, alpha=noerr * 0.95)

    # rollover flash on "it rolled over"
    flash(ax, cx, cy, t, T_ROLL + 0.3, r0=1.1, r1=2.3, n=14, d=0.9)

    # ---- 12.8 "a single byte": the 8-bit row --------------------------------
    bw, gap = 0.52, 0.16
    total = 8 * bw + 7 * gap
    x0 = 5 - total / 2 + bw / 2
    brow = reveal(t, T_BYTE, 0.6)
    if brow > 0.01:
        # 14.2 "One byte." — the row itself punches
        bpop = pulse(t, T_ONE, 0.6)
        bits = _bits(val)
        # 27.7 carry ripple: 11111111 + 1 flips every bit to 0, right to left
        ripple = clamp((t - T_ROLL) / 0.85) if t >= T_ROLL else 0.0
        nflip = int(ripple * 8 + 0.999) if ripple > 0 else 0
        for i, b in enumerate(bits):
            if nflip > 0 and i >= 8 - nflip:
                b = 0
            bx = x0 + i * (bw + gap)
            flipping = (nflip > 0 and i == 8 - nflip)
            edge = AMBER_HOT if (flipping or bpop > 0.1) else AMBER
            box(ax, bx, 2.95, bw + 0.06 * bpop, bw + 0.06 * bpop, edge=edge,
                fill=AMBER if b else NAVY2,
                fill_alpha=0.9 if b else 0.4, lw=2.5 + 1.5 * bpop, alpha=brow)
        text(ax, x0 - bw - 0.1, 2.95, "1 byte:", 20, CREAM, alpha=brow * 0.85,
             ha="right")
        # 16.8 "a maximum value of 255": ghost-light the ceiling
        mx = reveal(t, T_MAX, 0.6) * (1.0 - reveal(t, TICKS[2], 0.6))
        if mx > 0.01:
            for i in range(8):
                bx = x0 + i * (bw + gap)
                box(ax, bx, 2.95, bw + 0.10, bw + 0.10, edge=AMBER_HOT,
                    fill="none", lw=1.8, alpha=mx * 0.55)
        text(ax, 5, 1.95, "maximum:  11111111  =  255", 22,
             AMBER_HOT if t < TICKS[2] else CREAM,
             alpha=reveal(t, T_MAX, 0.6) * (0.95 if t < TICKS[2] else 0.55))

    footer(ax, t)


def draw_linac(ax, t, dur):
    # ---- spine geometry (one horizontal beam-line) ----
    SPINE = 5.6
    GUN_X = 1.9
    TGT_X = 5.0
    PAT_X = 8.3

    # ===== TITLE =====
    text(ax, 5, 9.25, "THE LINEAR ACCELERATOR", 46, AMBER,
         alpha=reveal(t, 0.2, 0.7), stroke=1.6)
    text(ax, 5, 8.45, "one beam, two modes  -  and what removes the safety net",
         24, CREAM, alpha=reveal(t, 1.0, 0.6))

    # ===== PHASE 1 (~0-10s): build the machine left -> right =====
    # electron gun
    g = reveal(t, 2.2, 0.7)
    box(ax, GUN_X, SPINE, 2.0, 1.25, edge=AMBER, alpha=g)
    text(ax, GUN_X, SPINE + 0.18, "ELECTRON", 24, CREAM, alpha=g)
    text(ax, GUN_X, SPINE - 0.30, "GUN", 24, CREAM, alpha=g)

    # patient position (right) - turns hot in the danger phase
    p = reveal(t, 3.4, 0.7)
    danger = t >= 34.0
    pat_edge = AMBER_HOT if danger else CREAM
    box(ax, PAT_X, SPINE, 1.7, 1.25, edge=pat_edge, fill=NAVY2, alpha=p)
    text(ax, PAT_X, SPINE + 0.18, "PATIENT", 24, CREAM, alpha=p)
    text(ax, PAT_X, SPINE - 0.32, "(target area)", 18, MUTE, alpha=p)

    # the electron beam (gun -> patient) appears as the spine.
    # the phase-1 "what it is" label only lives during phases 1-2, then fades so it
    # never collides with the per-mode labels below.
    b = reveal(t, 4.8, 0.8)
    if t < 10.0:
        # phase-1 only: show the beam reaching toward the patient + its label,
        # then hand off to the per-mode beams below so labels never stack.
        arrow(ax, GUN_X + 1.1, SPINE, PAT_X - 1.0, SPINE, color=AMBER, lw=4,
              alpha=b * (1 - reveal(t, 9.5, 0.5)))
        text(ax, (GUN_X + PAT_X) / 2, SPINE + 0.95, "high-energy electron beam",
             22, AMBER, alpha=reveal(t, 6.0, 0.7) * (1 - reveal(t, 9.0, 0.8)))

    # which phase are we in (drives mid-section)
    # PHASE 2: electron (direct) mode  ~10-21
    # PHASE 3: x-ray (target in path)  ~21-34
    # PHASE 4: danger (no target)      ~34-45

    # ===== shared label line positions =====
    LBL_Y = 3.55
    SUB_Y = 2.75

    # ----- PHASE 2: ELECTRON MODE -----
    e2 = reveal(t, 10.5, 0.7)
    if t < 21.0:
        # beam goes straight, no target
        arrow(ax, GUN_X + 1.1, SPINE, PAT_X - 0.95, SPINE,
              color=AMBER, lw=5, alpha=e2)
        text(ax, 5, 7.15, "MODE 1  -  ELECTRON (direct)", 30, AMBER,
             alpha=e2, stroke=1.2)
        text(ax, 5, LBL_Y, "electrons strike the patient directly", 24, CREAM,
             alpha=reveal(t, 12.0, 0.7))
        text(ax, 5, SUB_Y, "low-energy  -  this is SAFE", 26, AMBER,
             alpha=reveal(t, 14.0, 0.7), stroke=1.0)

    # ----- PHASE 3: X-RAY MODE (target drops in) -----
    elif t < 34.0:
        # reveal t0s sit just BEFORE the 21.0 branch cut so the X-ray content is
        # already partly faded-in the instant phase 2 hands off (no empty frame).
        e3 = reveal(t, 20.6, 0.6)
        # target slides DOWN into the beam path on a turntable
        drop = reveal(t, 20.9, 1.1)  # 0 -> 1 drop animation
        tgt_y = SPINE + 0.9 * (1 - drop)   # starts just above spine (clears title), settles on spine
        # beam from gun to target
        arrow(ax, GUN_X + 1.1, SPINE, TGT_X - 0.7, SPINE,
              color=AMBER, lw=5, alpha=e3)
        # the metal target (a small heavy block) on its turntable
        box(ax, TGT_X, tgt_y, 0.95, 1.0, edge=AMBER_HOT, fill=AMBER_D,
            fill_alpha=0.85, lw=3.5, alpha=e3)
        text(ax, TGT_X, tgt_y, "TARGET", 18, NAVY, alpha=e3 * drop)
        # turntable label below the seated target (only once it has landed)
        text(ax, TGT_X, SPINE - 0.95, "turntable", 18, MUTE, alpha=e3 * drop * 0.9)
        # x-rays from target to patient (after it lands)
        xr = reveal(t, 23.0, 0.7)
        arrow(ax, TGT_X + 0.7, SPINE, PAT_X - 0.95, SPINE,
              color=AMBER, lw=4, alpha=xr)
        text(ax, (TGT_X + PAT_X) / 2, SPINE + 0.85, "X-rays", 22, AMBER,
             alpha=xr)
        text(ax, 5, 7.15, "MODE 2  -  X-RAY", 30, AMBER, alpha=e3, stroke=1.2)
        text(ax, 5, LBL_Y, "beam hits metal target  ->  converts to X-rays",
             24, CREAM, alpha=reveal(t, 24.0, 0.7))
        text(ax, 5, SUB_Y, "target IN the path  -  high-energy, SAFE", 26, AMBER,
             alpha=reveal(t, 26.5, 0.7), stroke=1.0)

    # ----- PHASE 4: THE DANGER (no target, full power) -----
    else:
        e4 = reveal(t, 33.7, 0.6)
        # the target slides AWAY off the beam path: it lifts a short way and fades,
        # so the beam reaches the patient with nothing to absorb it.
        gone = reveal(t, 34.0, 1.2)
        ghost_y = SPINE + 1.15 * gone           # short lift, well clear of the title
        box(ax, TGT_X, ghost_y, 0.95, 1.0, edge=AMBER_D, fill=NAVY2,
            fill_alpha=0.3, lw=2, alpha=(1 - gone) * 0.55)
        # caption sits BELOW the (empty) beam path and fades out - never near the title
        text(ax, TGT_X, SPINE - 0.95, "NO target", 19, AMBER_HOT,
             alpha=e4 * (1 - reveal(t, 39.0, 1.2)))
        # full electron beam now reaches patient unimpeded - AMBER_HOT
        arrow(ax, GUN_X + 1.1, SPINE, PAT_X - 0.85, SPINE,
              color=AMBER_HOT, lw=6, alpha=e4)
        # the catastrophe: repeating impact flashes on the patient so every frame
        # in the danger window lands on a live burst (pulse is 0 at its own t0).
        for t0 in (36.2, 37.6, 39.0, 40.4, 41.8, 43.2, 44.6):
            flash(ax, PAT_X, SPINE, t, t0, r0=1.0, r1=2.05, d=0.95)
        text(ax, 5, 7.15, "MODE 2 POWER  +  NO TARGET", 30, AMBER_HOT,
             alpha=e4, stroke=1.4)
        text(ax, 5, LBL_Y, "full X-ray-power electron beam, nothing to absorb it",
             24, CREAM, alpha=reveal(t, 37.0, 0.7))
        text(ax, 5, SUB_Y, "DOSE: CATASTROPHICALLY WRONG", 30, AMBER_HOT,
             alpha=reveal(t, 39.0, 0.7), stroke=1.4)

    footer(ax, t)


# ===========================================================================
# SCENE: linac_synced — the two-mode linear accelerator, NARRATION-SYNCED.
# Unlike draw_linac (hardcoded phase times), every reveal time comes from the
# segment's word alignment, so beams fire / the target pops / x-rays scatter /
# danger pulses land on the spoken words. This is the locked-in seg_007 diagram.
# Build with: synced_linac(resolve_cues(alignment, LINAC_SYNC_CUES)).
# ===========================================================================
LINAC_SYNC_CUES = {
    "title": "Therac-25", "machine": "two ways to fire", "kill": "could kill",
    "electron": "electron mode", "xray": "X-ray mode", "target": "metal target",
    "convert": "turns that force", "punishment": "takes the punishment",
    "treatment": "gets the treatment", "rotate": "Two modes", "depends": "exactly where",
}


def resolve_cues(alignment, cue_phrases: dict) -> dict:
    """Map each cue phrase to its spoken start time (0.0 when not found)."""
    from lib.word_timing import words, find_phrase_start
    ws = words(alignment)
    return {k: (find_phrase_start(ws, p) or 0.0) for k, p in cue_phrases.items()}


def synced_linac(C: dict) -> Callable:
    """Return a draw(ax, t, dur) for the narration-synced two-mode linac diagram.

    ``C`` is a {cue: start_seconds} map (resolve_cues(align, LINAC_SYNC_CUES)).
    """
    SPINE, GUN_X, TT_X, PAT_X = 5.3, 1.8, 5.0, 8.3

    def draw(ax, t, _dur):
        text(ax, 5, 9.25, "THE LINEAR ACCELERATOR", 46, AMBER,
             alpha=reveal(t, C["title"], 0.6), stroke=1.6)
        text(ax, 5, 8.45, "one machine  -  two ways to fire a beam", 24, CREAM,
             alpha=reveal(t, C["machine"], 0.6))

        # machine: staggered build — source, then turntable, then patient
        g = reveal(t, C["machine"], 0.6)
        gt = reveal(t, C["machine"] + 0.3, 0.6)
        gp = reveal(t, C["machine"] + 0.6, 0.6)
        box(ax, GUN_X, SPINE, 2.0, 1.2, edge=AMBER, alpha=g)
        text(ax, GUN_X, SPINE + 0.16, "BEAM", 22, CREAM, alpha=g)
        text(ax, GUN_X, SPINE - 0.30, "SOURCE", 22, CREAM, alpha=g)
        box(ax, PAT_X, SPINE, 1.7, 1.2, edge=CREAM, alpha=gp)
        text(ax, PAT_X, SPINE + 0.16, "PATIENT", 22, CREAM, alpha=gp)
        text(ax, PAT_X, SPINE - 0.32, "(target area)", 17, MUTE, alpha=gp)
        if gt > 0.01:
            ax.add_patch(Circle((TT_X, SPINE), 0.92, fill=False, edgecolor=AMBER,
                                lw=2.5, alpha=gt * 0.8, zorder=2))
            ax.scatter([TT_X], [SPINE], s=26, c=AMBER, alpha=gt, zorder=3)
            text(ax, TT_X, SPINE - 1.32, "turntable", 17, MUTE, alpha=gt * 0.9)

        # "One of them could kill." — warning ring + ! on the patient
        pulse_glow(ax, PAT_X, SPINE, t, C["kill"], color=AMBER_HOT, d=1.6, rmax=1.5, alpha=gp)
        text(ax, PAT_X, SPINE + 1.15, "!", 40, AMBER_HOT, alpha=pulse(t, C["kill"], 1.6) * gp)

        # target rides the turntable rim; pops in at its cue, rotates at "rotate"
        rot = reveal(t, C["rotate"], 1.2)
        ang = rot * np.pi
        tx, ty = TT_X + 0.92 * np.cos(ang), SPINE + 0.92 * np.sin(ang)
        tgt = reveal(t, C["target"], 0.45)
        if tgt > 0.01:
            sc = 1.0 + 0.3 * pulse(t, C["target"], 0.5)
            box(ax, tx, ty, 0.6 * sc, 0.6 * sc, edge=AMBER_HOT, fill=AMBER_D,
                fill_alpha=0.85, lw=3, alpha=tgt)
            text(ax, tx, ty, "TARGET", 13, NAVY, alpha=tgt)

        # ELECTRON MODE [electron, xray) — first beam fires here
        e_on = reveal(t, C["electron"], 0.6) * (1 - reveal(t, C["xray"] - 0.3, 0.4))
        if e_on > 0.01:
            fired_beam(ax, GUN_X + 1.05, PAT_X - 0.9, SPINE, t, C["electron"],
                       color=AMBER, lw=5, dur=0.6, alpha=e_on)
            beam_flow(ax, GUN_X + 1.05, PAT_X - 0.9, SPINE, t, C["electron"] + 0.6,
                      color=AMBER_HOT, period=0.85, alpha=e_on)
            text(ax, 5, 7.0, "MODE 1  -  ELECTRON (direct)", 30, AMBER, alpha=e_on, stroke=1.2)
            text(ax, 5, 2.2, "low power  -  straight to the patient", 25, CREAM, alpha=e_on)

        # X-RAY MODE [xray, rotate) — thick beam fires INTO target, then scatters
        x_on = reveal(t, C["xray"], 0.6) * (1 - reveal(t, C["rotate"] - 0.7, 0.8))
        if x_on > 0.01:
            text(ax, 5, 7.0, "MODE 2  -  X-RAY  (about 100x stronger)", 30, AMBER,
                 alpha=x_on, stroke=1.2)
            fired_beam(ax, GUN_X + 1.05, TT_X + 0.62, SPINE, t, C["xray"],
                       color=AMBER_HOT, lw=11, dur=0.7, alpha=x_on)
            beam_flow(ax, GUN_X + 1.05, TT_X + 0.62, SPINE, t, C["xray"] + 0.7,
                      color=CREAM, period=0.5, alpha=x_on)
            scatter_rays(ax, TT_X + 1.2, SPINE, t, C["convert"], alpha=x_on, reach=2.6)
            text(ax, 5, 2.2, "beam hits the metal target  ->  scatters into X-rays", 24,
                 CREAM, alpha=reveal(t, C["convert"], 0.6) * (1 - reveal(t, C["rotate"] - 0.7, 0.8)))
            flash(ax, tx, ty, t, C["punishment"], r0=0.5, r1=1.7, n=16, color=AMBER_HOT, d=1.1)
            pulse_glow(ax, tx, ty, t, C["punishment"], color=AMBER_HOT, d=1.1, rmax=1.0, alpha=x_on)
            pulse_glow(ax, PAT_X, SPINE, t, C["treatment"], color=AMBER, d=1.3, rmax=1.3, alpha=x_on)

        # TWO MODES, ONE MACHINE [rotate, depends) — the turntable rotates
        tw = reveal(t, C["rotate"], 0.6) * (1 - reveal(t, C["depends"] - 0.2, 0.3))
        if tw > 0.01:
            text(ax, 5, 7.0, "TWO MODES  -  ONE MACHINE", 30, AMBER, alpha=tw, stroke=1.2)

        # THE FOREBODING [depends, end]
        dp = reveal(t, C["depends"], 0.5)
        if dp > 0.01:
            flash(ax, tx, ty, t, C["depends"] + 0.1, r0=0.7, r1=1.8, n=14, color=AMBER_HOT, d=1.3)
            pulse_glow(ax, tx, ty, t, C["depends"] + 0.1, color=AMBER_HOT, d=1.5, rmax=1.4)
            text(ax, 5, 7.0, "...everything depends on the target", 28, AMBER_HOT,
                 alpha=dp, stroke=1.3)
            text(ax, 5, 2.2, "being EXACTLY where it's supposed to be", 28, AMBER_HOT,
                 alpha=dp, stroke=1.3)

        footer(ax, t)

    return draw


def draw_race_condition(ax, t, dur):
    """Timed to the REAL narration (assets/audio_v6/seg_026.alignment.json sentence
    spans, slot 35.16s) — every visual event lands ON its narration line, and the
    lanes match the words: the narrator says the SCREEN takes the correction, the
    TURNTABLE follows it, and only the BEAM SETTING never hears — the old 2-lane
    version wrongly showed the turntable stuck. Three lanes now.
      0.0 title | 2.0 ~8s setup | 6.1 operator keeps typing | 10.7 X-RAY by mistake
      13.1 catch it | 14.1 cursor-up fix to ELECTRON | 19.6 screen corrected
      22.1 turntable follows | 24.1 beam setting never hears | 27.6 LOCKED X-RAY
      30.9 100x the current"""
    # ---- title + subtitle -------------------------------------------------
    text(ax, 5, 9.3, "THE RACE CONDITION", 46, AMBER,
         alpha=reveal(t, 0.2, 0.7), stroke=1.6)
    text(ax, 5, 8.62, "beam setup takes ~8 seconds - the screen keeps listening",
         22, CREAM, alpha=reveal(t, 2.2, 0.7))

    # ---- lane geometry ----------------------------------------------------
    lx0, lx1 = 3.0, 8.8          # lane left/right (track span)
    lbl_x = 1.45                 # x for lane labels (left gutter)
    sc_y, tt_y, bm_y = 7.0, 5.2, 3.4   # SCREEN / TURNTABLE / BEAM SETTING lanes
    bw, bh = 1.95, 0.85          # state-box size
    bx_a = lx0 + bw / 2          # left state box centre  (the typed mistake)
    bx_b = lx1 - bw / 2          # right state box centre (the correction)

    # narration times (sentence starts from the alignment)
    T_TYPING, T_XRAY, T_CATCH, T_FIX = 6.3, 10.9, 13.2, 14.3
    T_SCREEN, T_TABLE, T_NEVER, T_LOCK, T_100X = 19.8, 22.3, 24.3, 27.8, 31.1

    # ---- the 8-second window bracket (2.0 "takes about eight seconds") ----
    win = reveal(t, 2.4, 0.8)
    if win > 0.01:
        wy = 8.05
        bxl, bxr = bx_a - bw / 2, bx_b + bw / 2
        ax.plot([bxl, bxr], [wy, wy], color=AMBER, lw=2.5, alpha=win, zorder=4)
        ax.plot([bxl, bxl], [wy, wy - 0.2], color=AMBER, lw=2.5, alpha=win, zorder=4)
        ax.plot([bxr, bxr], [wy, wy - 0.2], color=AMBER, lw=2.5, alpha=win, zorder=4)
        # "all inside those eight seconds" (16-19.6) re-pulses the bracket label
        wpop = pulse(t, 16.2, 1.2)
        text(ax, (bxl + bxr) / 2, wy + 0.34, "the 8 second window",
             20 + 3 * wpop, AMBER, alpha=win)

    # ---- lanes appear as the screen-keeps-listening line lands (6.1) ------
    lanes = ((sc_y, "SCREEN", "(display)", reveal(t, T_TYPING, 0.7)),
             (tt_y, "TURNTABLE", "(target)", reveal(t, T_TYPING + 0.5, 0.7)),
             (bm_y, "BEAM SETTING", "(current)", reveal(t, T_TYPING + 1.0, 0.7)))
    for ly, name, sub, app in lanes:
        if app > 0.01:
            text(ax, lbl_x, ly + 0.16, name, 21, AMBER, alpha=app,
                 ha="center", stroke=1.1)
            text(ax, lbl_x, ly - 0.42, sub, 15, MUTE, alpha=app, ha="center")
            ax.plot([lx0 - 0.35, lx1], [ly, ly], color=AMBER_D, lw=2,
                    alpha=app * 0.55, zorder=1)

    # operator-typing marker, alive until the correction lands on the screen
    type_app = reveal(t, T_TYPING + 0.3, 0.7) * (1.0 - reveal(t, T_SCREEN, 1.0))
    text(ax, (bx_a + bx_b) / 2, sc_y + 0.75, "operator keeps typing...",
         20, AMBER, alpha=type_app, stroke=0.8)

    # ONE state box per lane; the TEXT INSIDE morphs X-RAY -> ELECTRON in place
    # when the narration corrects that lane (operator note: the boxes themselves
    # change; the third keeps saying X-RAY because it never updated).
    bx = (bx_a + bx_b) / 2       # single state box, centred on the track
    bws = bw + 0.5               # a little wider since it is the only box

    def state_box(ly, t_appear, t_flip):
        """X-RAY box that crossfades to ELECTRON at t_flip (None = never)."""
        app = reveal(t, t_appear, 0.6)
        if app <= 0.01:
            return
        f = reveal(t, t_flip, 0.7) if t_flip is not None else 0.0
        pop = pulse(t, t_appear + 0.1, 0.5) + (pulse(t, t_flip + 0.1, 0.6)
                                               if t_flip is not None else 0.0)
        edge = AMBER_HOT if f > 0.5 else AMBER
        box(ax, bx, ly, bws, bh, edge=edge, fill=NAVY2,
            fill_alpha=0.45 + 0.2 * f, lw=3 + 1.2 * pop, alpha=app)
        # crossfade the state text in place
        text(ax, bx, ly, "X-RAY", 26 + 3 * pop, CREAM,
             alpha=app * (1.0 - f), stroke=1.0)
        text(ax, bx, ly, "ELECTRON", 24 + 4 * pop, AMBER_HOT,
             alpha=app * f, stroke=1.0)

    # ---- 10.7 "Type X-RAY by mistake": all three lanes take the mistake ---
    state_box(sc_y, T_XRAY, T_SCREEN)        # screen corrects at 19.8
    state_box(tt_y, T_XRAY + 0.4, T_TABLE)   # turntable follows at 22.3
    state_box(bm_y, T_XRAY + 0.7, None)      # beam setting NEVER updates
    # below the box — the cursor-up label takes this exact slot as it fades out
    text(ax, bx, sc_y - bh / 2 - 0.38, "typed by mistake", 17, MUTE,
         alpha=reveal(t, T_XRAY + 0.5, 0.7) * (1.0 - reveal(t, T_FIX, 0.6)))

    # ---- 13.1 "Catch it." — a ring around the typed mistake ---------------
    flash(ax, bx, sc_y, t, T_CATCH, r0=0.85, r1=1.5, n=12, d=0.9)

    # ---- 14.1 "Cursor up, fix it to electron" -----------------------------
    fix = reveal(t, T_FIX, 0.7) * (1.0 - reveal(t, T_SCREEN + 0.6, 1.0))
    if fix > 0.01:
        text(ax, bx, sc_y - bh / 2 - 0.38,
             "cursor up - fix it to ELECTRON", 19, AMBER_HOT, alpha=fix)

    # ---- 19.6 / 22.1: the correction lands, lane by lane ------------------
    text(ax, bx, sc_y + bh / 2 + 0.34, "corrected", 16, AMBER,
         alpha=reveal(t, T_SCREEN + 0.4, 0.7))
    text(ax, bx, tt_y + bh / 2 + 0.34, "follows", 16, AMBER,
         alpha=reveal(t, T_TABLE + 0.4, 0.7))

    # ---- 24.1 "But the beam setting never hears about the change..." ------
    stay = reveal(t, T_NEVER, 0.9)
    if stay > 0.01:
        text(ax, bx, bm_y - bh / 2 - 0.36, "never hears about the change",
             18, MUTE, alpha=stay * (1.0 - reveal(t, T_LOCK + 0.2, 1.0)))

    # ---- 27.6 "It stays locked at X-ray strength." ------------------------
    lock = reveal(t, T_LOCK, 0.7)
    if lock > 0.01:
        pop = pulse(t, T_LOCK + 0.15, 0.6)
        # re-stroke the beam box hot: still X-RAY, now dangerous
        box(ax, bx, bm_y, bws, bh, edge=AMBER_HOT, fill=NAVY2,
            fill_alpha=0.0, lw=4 + 1.5 * pop, alpha=lock)
        text(ax, bx, bm_y - bh / 2 - 0.36, "LOCKED at x-ray strength",
             18, AMBER_HOT, alpha=reveal(t, T_LOCK + 0.3, 0.7))
        # divergence connector: the corrected turntable above, the locked beam
        arrow(ax, bx + bws / 2 + 0.45, tt_y - bh / 2 - 0.05,
              bx + bws / 2 + 0.45, bm_y + bh / 2 + 0.05,
              color=AMBER_HOT, lw=4, alpha=lock)
        flash(ax, bx + bws / 2 + 0.45, (tt_y + bm_y) / 2, t, T_LOCK + 0.4,
              r0=0.6, r1=1.3, d=0.8)

    # ---- 30.9 "A hundred times the current of an electron treatment." -----
    x100 = reveal(t, T_100X, 0.8)
    if x100 > 0.01:
        pop = pulse(t, T_100X + 0.2, 0.7)
        text(ax, 5, 1.9, "100x the current of an electron treatment",
             27 + 4 * pop, AMBER_HOT, alpha=x100, stroke=1.3)
        text(ax, 5, 1.28, "screen and turntable corrected - the beam never was",
             20, CREAM, alpha=reveal(t, T_100X + 0.9, 0.8))

    footer(ax, t)


def draw_beam_fires(ax, t, dur):
    """Timed to seg_027's narration (assets/audio_v6/seg_027.alignment.json,
    slot 20.3s) — and the second HALF of the narration (the dose chambers that
    saturate and report almost nothing) is now on screen; the old version ended
    at the impact and never visualized it.
      0.0 full-power beam, no target, no filter | 5.7 directly into the patient
      7.9 chambers can't keep up | 11.7 saturate, report almost nothing
      15.2 the machine doesn't even know what it's done"""
    # ---- title ----
    text(ax, 5, 9.2, "THE BEAM FIRES", 46, AMBER, alpha=reveal(t, 0.1, 0.5),
         stroke=1.6)

    # ---- 0-5.7: emitter + patient + the two ABSENT safeguards -------------
    em = reveal(t, 0.5, 0.6)          # emitter appears
    box(ax, 5, 8.05, 3.0, 0.95, edge=AMBER, fill=NAVY2, fill_alpha=0.6,
        lw=3.5, alpha=em)
    text(ax, 5, 8.05, "EMITTER", 26, CREAM, alpha=em)

    pat = reveal(t, 1.0, 0.6)
    box(ax, 5, 1.55, 3.4, 0.95, edge=AMBER, fill=NAVY2, fill_alpha=0.6,
        lw=3.5, alpha=pat)
    text(ax, 5, 1.55, "PATIENT", 26, CREAM, alpha=pat)

    # struck-through safeguards hold the whole no-target/no-filter line (to 5.7)
    lbl = reveal(t, 1.9, 0.6) * (1.0 - reveal(t, 5.7, 0.6))
    text(ax, 2.45, 5.85, "TARGET", 24, AMBER_HOT, alpha=lbl)
    if lbl > 0.01:
        ax.plot([1.35, 3.55], [5.85, 5.85], color=AMBER_HOT, lw=3, alpha=lbl,
                zorder=5, solid_capstyle="round")
    text(ax, 2.45, 5.25, "(absent)", 18, MUTE, alpha=lbl)
    text(ax, 7.55, 5.85, "FILTER", 24, AMBER_HOT, alpha=lbl)
    if lbl > 0.01:
        ax.plot([6.45, 8.65], [5.85, 5.85], color=AMBER_HOT, lw=3, alpha=lbl,
                zorder=5, solid_capstyle="round")
    text(ax, 7.55, 5.25, "(absent)", 18, MUTE, alpha=lbl)
    text(ax, 5, 6.7, "NO target   -   NO filter", 22, CREAM, alpha=lbl * 0.9)

    # ---- 5.7 "Directly into the patient.": the beam FIRES -----------------
    T_FIRE, T_CHAM, T_SAT, T_KNOW = 5.9, 8.1, 11.9, 15.4
    fire = reveal(t, T_FIRE, 0.8)
    if fire > 0.01:
        y_top, y_bot = 7.55, 2.05
        y_now = y_top - (y_top - y_bot) * fire
        flick = 0.0
        if t >= T_FIRE + 0.8:
            flick = 0.5 + 0.5 * np.sin((t - T_FIRE - 0.8) * 11.0)
        lwbeam = 16 + 8 * pulse(t, T_FIRE, 1.2) + 3 * flick
        ax.plot([5, 5], [y_top, y_now], color=AMBER, lw=lwbeam + 16,
                alpha=fire * 0.22, zorder=3, solid_capstyle="round")
        ax.plot([5, 5], [y_top, y_now], color=AMBER_HOT, lw=lwbeam + 7,
                alpha=fire * 0.35, zorder=3.5, solid_capstyle="round")
        ax.plot([5, 5], [y_top, y_now], color=AMBER_HOT, lw=lwbeam, alpha=fire,
                zorder=4, solid_capstyle="round")
        ax.plot([5, 5], [y_top, y_now], color=CREAM, lw=max(2, lwbeam * 0.28),
                alpha=fire * 0.85, zorder=4.5, solid_capstyle="round")

    # impact glow + periodic bursts from touchdown to the end of the slot
    imp = reveal(t, T_FIRE + 0.7, 0.5)
    if imp > 0.01:
        gl = 0.55 + 0.45 * abs(np.sin(t * 9.0))
        ax.scatter([5], [2.05], s=2600 * imp, c=AMBER_HOT, alpha=0.30 * imp * gl,
                   zorder=3, linewidths=0)
        ax.scatter([5], [2.05], s=1300 * imp, c=CREAM, alpha=0.35 * imp * gl,
                   zorder=3.5, linewidths=0)
    for k in range(15):
        flash(ax, 5, 2.05, t, T_FIRE + 0.8 + 0.9 * k, r0=1.0, r1=2.4, n=14,
              color=AMBER_HOT, d=1.1)

    # hard caption flanking the beam, on the narration line
    yA, yB = 4.55, 3.55
    cap = reveal(t, T_FIRE + 0.9, 0.55) * (1.0 - reveal(t, T_CHAM + 0.6, 0.8))
    text(ax, 5.75, yA, "FULL", 44, AMBER_HOT, alpha=cap, ha="left", stroke=1.8)
    text(ax, 5.75, yB, "POWER.", 44, AMBER_HOT, alpha=cap, ha="left", stroke=1.8)
    text(ax, 4.25, yA, "NO", 44, CREAM, alpha=cap, ha="right", stroke=1.8)
    text(ax, 4.25, yB, "TARGET.", 44, CREAM, alpha=cap, ha="right", stroke=1.8)

    # ---- 7.9 "the chambers that measure dose can't keep up" ---------------
    # a dose-chamber gauge right of the beam: climbs frantically, pegs, then
    # the READOUT collapses to almost nothing (11.7), because saturation lies.
    ch = reveal(t, T_CHAM, 0.7)
    if ch > 0.01:
        gx, gy, gw, gh = 8.25, 4.9, 1.15, 2.6      # gauge geometry
        box(ax, gx, gy, gw, gh, edge=AMBER, fill=NAVY2, fill_alpha=0.5,
            lw=3, alpha=ch)
        text(ax, gx, gy + gh / 2 + 0.36, "DOSE CHAMBER", 17, CREAM, alpha=ch)
        # fill level: frantic climb 8.1->10.5, pegged to 11.9, collapse by 13
        if t < 10.5:
            lvl = clamp((t - T_CHAM) / 2.2) * (0.85 + 0.1 * np.sin(t * 13.0))
        elif t < T_SAT:
            lvl = 0.97 + 0.03 * np.sin(t * 17.0)   # pegged, quivering
        else:
            lvl = max(0.04, 0.97 * (1.0 - reveal(t, T_SAT, 1.1)))  # collapse
        y0 = gy - gh / 2 + 0.08
        ax.plot([gx, gx], [y0, y0 + (gh - 0.16) * clamp(lvl)],
                color=(AMBER_HOT if t < T_SAT + 0.4 else MUTE),
                lw=26, alpha=ch * 0.9, solid_capstyle="butt", zorder=4)
        text(ax, gx, gy - gh / 2 - 0.34, "can't keep up", 17, AMBER,
             alpha=ch * (1.0 - reveal(t, T_SAT, 0.8)))
        # 11.7 "They saturate — and report almost nothing."
        sat = reveal(t, T_SAT, 0.6)
        if sat > 0.01:
            flash(ax, gx, gy, t, T_SAT + 0.1, r0=0.8, r1=1.6, n=12, d=0.9)
            text(ax, gx, gy - gh / 2 - 0.34, "SATURATED", 18, AMBER_HOT,
                 alpha=sat * (1.0 - reveal(t, T_SAT + 2.2, 0.7)))
            text(ax, gx, gy - gh / 2 - 0.34, "reads: almost nothing", 16, MUTE,
                 alpha=reveal(t, T_SAT + 2.4, 0.7))

    # ---- 15.2 "The machine doesn't even know what it's done." -------------
    know = reveal(t, T_KNOW, 0.8)
    if know > 0.01:
        pop = pulse(t, T_KNOW + 0.2, 0.7)
        text(ax, 5, 0.72, "the machine doesn't even know what it's done",
             24 + 3 * pop, CREAM, alpha=know, stroke=1.2)

    # footer yields the bottom strip to the closing line
    text(ax, 5, 0.34, "Source: Leveson & Turner, IEEE Computer, 1993", 19,
         CREAM, alpha=reveal(t, 0.8, 0.8) * 0.62 * (1.0 - reveal(t, T_KNOW, 0.8)))


def draw_false_safe(ax, t, dur):
    """Timed to seg_029's narration (assets/audio_v6/seg_029.alignment.json,
    slot 39.24s). Picks up seg_028's hot 0 and plays the consequence:
      0.0 "Zero." (the 0 IS the opening frame) | 3.3 nothing needs checking
      5.5 one pass in every / 6.7 "1 / 256" | 8.6 turntable check box
      10.3 ...simply VANISHES | 12.9 SET button pressed | 14.0 exact moment
      16.5 software: no reason to stop | 18.6 SAFE lamp (interlocks satisfied)
      21.1 BEAM FIRES | 22.3 turntable out of position | 24.3 target out of
      the path | 28.5 Yakima / 29.7 dead of winter | 31.5 For Glen Dodd.
      33.5 hardware interlocks | 35.5 nothing left to catch it
      36.8 AECL had removed them / 38.3 X X X."""
    T_NOCHK, T_ONEPASS, T_256 = 3.29, 5.53, 6.71
    T_TTBOX, T_VANISH = 8.57, 10.33
    T_SET, T_MOMENT, T_NOSTOP, T_SATISF = 12.90, 14.03, 16.50, 18.59
    T_FIRE, T_TTOUT, T_TGTOUT = 21.13, 22.35, 24.33
    T_YAK, T_WINTER, T_DODD = 28.50, 29.72, 31.52
    T_HW, T_CATCH, T_AECL, T_XOUT = 33.47, 35.49, 36.82, 38.28

    # ---- Title (persists whole scene) ----
    text(ax, 5, 9.25, "ZERO MEANS 'SAFE'", 46, AMBER, alpha=reveal(t, 1.0, 0.7),
         stroke=1.6)

    def seg(t_in, t_out, d=0.6):
        """Alpha for a phase that fades IN at t_in and OUT ending at t_out."""
        return reveal(t, t_in, d) * (1.0 - reveal(t, t_out - d, d))

    # ===================================================================
    # PHASE A [0-4.7] "Zero." — the 0 seg_028 ended on, front and centre
    # ===================================================================
    pA = seg(0.0, 4.9, 0.35)
    if pA > 0.01:
        pop = pulse(t, 0.15, 0.6)
        text(ax, 5, 5.9, "0", 165 + 26 * pop, AMBER_HOT, alpha=pA, stroke=2.0)
        text(ax, 5, 3.55, "the value that says:  nothing needs checking", 26,
             CREAM, alpha=reveal(t, T_NOCHK, 0.6) * pA)

    # ===================================================================
    # PHASE B [4.7-11.0] one pass in 256 — the turntable check VANISHES
    # ===================================================================
    pB = seg(4.9, 11.3, 0.5)
    if pB > 0.01:
        text(ax, 5, 7.45, "one pass in every", 24, CREAM,
             alpha=reveal(t, T_ONEPASS, 0.5) * pB * 0.9)
        text(ax, 5, 6.35, "1 / 256", 54, AMBER,
             alpha=reveal(t, T_256, 0.5) * pB, stroke=1.6)
        # the turntable check appears... then simply is not there any more
        bx_a = reveal(t, T_TTBOX, 0.6) * pB
        gone = reveal(t, T_VANISH, 0.7)
        if bx_a > 0.01:
            box(ax, 5, 4.35, 4.3, 1.3, edge=AMBER, fill=NAVY2, fill_alpha=0.55,
                lw=3.5, alpha=bx_a * (1.0 - gone * 0.85))
            text(ax, 5, 4.35, "TURNTABLE CHECK", 26, CREAM,
                 alpha=bx_a * (1.0 - gone))
            text(ax, 5, 2.95, "simply vanished", 24, MUTE,
                 alpha=reveal(t, T_VANISH + 0.15, 0.6) * pB)

    # ===================================================================
    # PHASE C [11.0-20.4] the coincidence: SET at that exact moment
    # ===================================================================
    pC = seg(11.3, 20.9, 0.5)
    if pC > 0.01:
        cy = 5.5
        # the operator's SET button — pressed on the word
        sa = reveal(t, 11.6, 0.5) * pC
        press = pulse(t, T_SET, 0.55)
        box(ax, 2.3, cy, 1.9, 1.25, edge=AMBER, fill=AMBER,
            fill_alpha=0.55 + 0.35 * press, lw=4, alpha=sa)
        text(ax, 2.3, cy, "SET", 30, NAVY, alpha=sa, stroke=0.0)
        pulse_glow(ax, 2.3, cy, t, T_SET, rmax=1.3, alpha=pC)
        text(ax, 2.3, cy - 1.15, "operator", 18, MUTE, alpha=sa * 0.9)
        # 14.0 "at that exact moment..."
        text(ax, 5, 2.9, "at that exact moment...", 26, AMBER_HOT,
             alpha=reveal(t, T_MOMENT, 0.6) * pC, stroke=1.2)
        # 16.5 "the software saw no reason to stop"
        a2 = reveal(t, T_NOSTOP - 0.5, 0.5) * pC
        arrow(ax, 3.45, cy, 4.15, cy, color=AMBER, lw=3, alpha=a2)
        text(ax, 5.35, cy + 0.33, "software:", 22, CREAM,
             alpha=reveal(t, T_NOSTOP, 0.5) * pC * 0.9)
        text(ax, 5.35, cy - 0.35, "no reason to stop", 22, CREAM,
             alpha=reveal(t, T_NOSTOP, 0.5) * pC)
        # 18.6 "the interlocks were satisfied" — the lying SAFE lamp
        a3 = reveal(t, T_SATISF - 0.4, 0.5) * pC
        arrow(ax, 6.75, cy, 7.35, cy, color=AMBER, lw=3, alpha=a3)
        la = reveal(t, T_SATISF, 0.5) * pC
        glow = 0.30 + 0.20 * (0.5 + 0.5 * np.sin((t - T_SATISF) * 2.4))
        box(ax, 8.45, cy, 1.95, 1.35, edge=AMBER, fill=AMBER,
            fill_alpha=glow * la, lw=4, alpha=la)
        text(ax, 8.45, cy, "SAFE", 38, NAVY, alpha=la, stroke=0.0)
        text(ax, 8.45, cy - 1.15, "interlocks satisfied", 18, MUTE,
             alpha=la * 0.9)

    # ===================================================================
    # PHASE D [20.4-26.4] the beam fires — out of position, out of the path
    # ===================================================================
    pD = seg(20.9, 26.6, 0.4)
    if pD > 0.01:
        fire = reveal(t, T_FIRE, 0.4) * pD
        fpop = pulse(t, T_FIRE + 0.1, 0.8)
        text(ax, 5, 5.95, "THE BEAM FIRED", 52 + 6 * fpop, AMBER_HOT,
             alpha=fire, stroke=1.8)
        flash(ax, 5, 5.95, t, T_FIRE + 0.1, r0=1.4, r1=2.8, n=16, d=0.9)
        pulse_glow(ax, 5, 5.95, t, T_FIRE, rmax=2.2, alpha=pD)
        text(ax, 5, 4.25, "turntable out of position", 27, CREAM,
             alpha=reveal(t, T_TTOUT, 0.6) * pD)
        text(ax, 5, 3.35, "target out of the path", 27, CREAM,
             alpha=reveal(t, T_TGTOUT, 0.6) * pD)

    # ===================================================================
    # PHASE E [26.4-32.6] Yakima, dead of winter — for Glen Dodd (quiet)
    # ===================================================================
    pE = seg(26.6, 32.9, 0.5)
    if pE > 0.01:
        text(ax, 5, 6.6, "the bug came back", 24, MUTE,
             alpha=reveal(t, 27.3, 0.7) * pE * 0.9)
        text(ax, 5, 5.55, "Yakima, Washington", 36, AMBER,
             alpha=reveal(t, T_YAK, 0.6) * pE, stroke=1.4)
        text(ax, 5, 4.6, "in the dead of winter", 24, MUTE,
             alpha=reveal(t, T_WINTER, 0.6) * pE * 0.95)
        text(ax, 5, 3.15, "For Glen Dodd.", 32, CREAM,
             alpha=reveal(t, T_DODD, 0.7) * pE, stroke=1.0)

    # ===================================================================
    # PHASE F [32.6-end] no hardware interlock left — AECL had removed them
    # ===================================================================
    pF = reveal(t, 32.9, 0.5)
    if pF > 0.01:
        text(ax, 5, 7.0, "HARDWARE INTERLOCKS", 30, MUTE,
             alpha=reveal(t, T_HW, 0.6) * pF * 0.9)
        labels = ["MECH STOP", "FUSE", "BACKUP"]
        bw, gap = 2.4, 0.5
        total = 3 * bw + 2 * gap
        x0 = 5 - total / 2 + bw / 2
        for i, lb in enumerate(labels):
            bx = x0 + i * (bw + gap)
            ba = reveal(t, T_HW + 0.25 + 0.3 * i, 0.5) * pF
            box(ax, bx, 5.15, bw, 1.25, edge=MUTE, fill=NAVY2, fill_alpha=0.30,
                lw=2.5, alpha=ba * 0.7)
            # label ABOVE the box so the amber X can cross the box out
            # without obscuring the text
            text(ax, bx, 6.17, lb, 22, CREAM, alpha=ba * 0.95)
            xa = reveal(t, T_XOUT + 0.18 * i, 0.35)
            if xa > 0.01:
                hw, hh = bw / 2 - 0.12, 0.62
                ax.plot([bx - hw, bx + hw], [5.15 - hh, 5.15 + hh],
                        color=AMBER_HOT, lw=5, alpha=xa, solid_capstyle="round",
                        zorder=6)
                ax.plot([bx - hw, bx + hw], [5.15 + hh, 5.15 - hh],
                        color=AMBER_HOT, lw=5, alpha=xa, solid_capstyle="round",
                        zorder=6)
        text(ax, 5, 3.35, "nothing left to catch it", 26, CREAM,
             alpha=reveal(t, T_CATCH, 0.6) * pF)
        text(ax, 5, 2.25, "AECL had removed them", 30, AMBER_HOT,
             alpha=reveal(t, T_AECL, 0.6) * pF, stroke=1.4)

    footer(ax, t)


# ---- scene registry --------------------------------------------------------
SCENES: dict[str, Callable] = {
    "byte_overflow": draw_byte_overflow,
    "linac": draw_linac,
    "race_condition": draw_race_condition,
    "beam_fires": draw_beam_fires,
    "false_safe": draw_false_safe,
}


def register_scene(name: str, draw: Callable) -> None:
    """Register a draw(ax, t, dur) callback under `name`.

    Exists so model-generated scenes (lib.diagram_codegen) flow through the SAME
    pipeline as the hand-coded ones — render_template, the CLI, and any caller
    that addresses scenes by template name never needs to know whether a scene
    was hand-authored or generated.
    """
    if name in SCENES:
        _log.warning("register_scene: overwriting existing scene %r", name)
    SCENES[name] = draw


# ===========================================================================
# render harness:  frames -> ffmpeg (CFR 30) -> finishing -> mp4
# ===========================================================================
@contextmanager
def sketch_style():
    """The one place the hand-drawn look is configured, so single-frame previews
    and full video render are byte-for-byte identical in style."""
    with plt.xkcd(scale=1.0, length=110, randomness=2.6):
        plt.rcParams["font.family"] = _HAND
        # Override xkcd's heavy white-halo stroke (linewidth 4, white) with a thin
        # navy one: keeps lines/boxes readable on navy without the bold glow that
        # made small text fuzzy. Text artists override this to Normal() (crisp).
        plt.rcParams["path.effects"] = [_pe.withStroke(linewidth=1.4, foreground=NAVY)]
        plt.rcParams["lines.linewidth"] = 2.0
        yield


def render_frame(draw, t, dur, out_png, bg="inked", seed=1000):
    """Render ONE frame at time t to a PNG — fast preview for authoring a scene
    without invoking ffmpeg. `draw` is a draw(ax, t, dur) callable. Returns out_png."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with sketch_style():
        np.random.seed(seed)
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(NAVY)
        ax = _new_ax(fig)
        paint_background(ax, bg)
        draw(ax, t, dur)
        fig.savefig(out_png, facecolor=NAVY)
        plt.close(fig)
    return str(out_png)


def render_template(template: str | Callable, duration_s: float,
                    out_path: str | Path, fps: int = 15, boil: int = 5,
                    finish: bool = True, out_fps: int = 30) -> str | None:
    """Render a scene to a 1920x1080 @ `out_fps` (default 30) CFR mp4.

    `template` is a registered scene name, or a draw(ax, t, dur) callable
    directly (how diagram_codegen renders not-yet-registered generated scenes).

    Frames are deliberately DRAWN at `fps` (default 15) and duplicated up to
    `out_fps` by ffmpeg, rather than drawn natively at 30: rasterization is the
    entire render cost, and the low draw rate IS the look — the xkcd wobble is
    re-seeded every `boil` drawn frames (5 @ 15fps = 3 ink "boils"/sec, the
    hand-drawn-animation cadence), so native 30fps drawing would double render
    time for zero visual change. The encoded stream is still true 1080p30 CFR,
    so it drops into the 1080p30 timeline without any conform pass.
    """
    draw = template if callable(template) else SCENES.get(template)
    if draw is None:
        _log.warning("no scene for template %r; have %s", template, list(SCENES))
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(round(duration_s * fps)))

    with sketch_style():
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(NAVY)
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            for i in range(n):
                # Re-seed every `boil` frames so the ink wobble gently "boils"
                # like real hand-drawn animation, instead of either dead-static
                # or jittering every single frame.
                np.random.seed(1000 + i // max(1, boil))
                fig.clf()
                fig.patch.set_facecolor(NAVY)
                ax = _new_ax(fig)
                paint_background(ax, os.environ.get("SKETCH_BG", "inked"))
                draw(ax, i / fps, duration_s)
                fig.savefig(tdp / f"f{i:05d}.png", facecolor=NAVY)
            plt.close(fig)

            raw = out_path.with_name(out_path.stem + "_raw.mp4")
            # Frames are already 1920x1080 (19.2x10.8in @ 100dpi) — encode them
            # at full size; -r duplicates the 15fps frames up to out_fps CFR.
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-framerate", str(fps), "-i", str(tdp / "f%05d.png"),
                   "-r", str(out_fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-an", str(raw)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=900)

    if not finish:
        return str(raw)
    from lib.finishing import apply_finish
    finished = apply_finish(str(raw), str(out_path))
    if finished and Path(finished).exists() and finished != str(raw):
        try:
            raw.unlink()
        except OSError:
            pass
    return finished or str(raw)


def _main():
    ap = argparse.ArgumentParser(description="Render a hand-drawn sketch diagram")
    ap.add_argument("template")
    ap.add_argument("duration", type=float)
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--raw", action="store_true", help="skip finishing pass")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    out = render_template(a.template, a.duration, a.out, fps=a.fps, finish=not a.raw)
    print("WROTE", out)


if __name__ == "__main__":
    _main()
