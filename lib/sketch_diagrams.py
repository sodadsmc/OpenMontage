"""Hand-drawn (graphic-novel) info diagrams in the channel palette.

Replaces the clean Manim animations with wobbly, hand-inked diagrams rendered via
matplotlib's xkcd() sketch mode + the Ink Free handwriting font, in navy+amber, then
run through the channel finishing pass (lib.finishing) so they sit in the exact same
grade as the AI footage. The goal is "a page torn out of the graphic novel", not a
clean slide dropped into it.

Each diagram is a `draw(ax, t, dur)` callback that paints the frame at time t in
[0, dur]; `render_template()` rasterizes frames -> ffmpeg (CFR) -> finishing -> mp4 at
the exact slot length. No LaTeX, no node, no Manim — pure matplotlib + ffmpeg.

CLI:
  python -m lib.sketch_diagrams byte_overflow 29.7 out.mp4 [--fps 15] [--raw]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib import patheffects as _pe
from matplotlib.patches import FancyBboxPatch, Rectangle

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
    # Title + subtitle (title keeps a faint navy outline; everything else crisp)
    text(ax, 5, 9.25, "THE 1-BYTE COUNTER", 48, AMBER, alpha=reveal(t, 0.2, 0.7),
         stroke=1.6)
    text(ax, 5, 8.45, "Class3  —  one byte holds 0 to 255", 26, CREAM,
         alpha=reveal(t, 0.9, 0.6))

    # Counter value over time
    if t < 6.0:
        val, vcol = 252, AMBER
    elif t < 9.0:
        val, vcol = 253, AMBER
    elif t < 12.0:
        val, vcol = 254, AMBER
    elif t < 16.0:
        val, vcol = 255, AMBER_HOT
    else:
        val, vcol = 0, AMBER_HOT

    appear = reveal(t, 2.2, 0.6)
    text(ax, 5, 6.7, "Class3 value", 24, CREAM, alpha=appear * 0.9)

    # pop on each change
    change_times = [6.0, 9.0, 12.0, 16.0]
    pop = max((pulse(t, ct - 0.0, 0.35) for ct in change_times), default=0.0)
    cy = 5.05
    text(ax, 5, cy, str(val), 150 + 26 * pop, vcol, alpha=appear, stroke=2.0)

    # overflow flash at 16.0
    flash(ax, 5, cy, t, 16.0, d=0.7)

    # 8-bit row
    bits = _bits(val)
    bw, gap = 0.52, 0.16
    total = 8 * bw + 7 * gap
    x0 = 5 - total / 2 + bw / 2
    brow = reveal(t, 3.0, 0.6)
    if brow > 0.01:
        for i, b in enumerate(bits):
            bx = x0 + i * (bw + gap)
            box(ax, bx, 2.95, bw, bw, edge=AMBER,
                fill=AMBER if b else NAVY2,
                fill_alpha=0.9 if b else 0.4, lw=2.5, alpha=brow)
        text(ax, x0 - bw - 0.1, 2.95, "1 byte:", 20, CREAM, alpha=brow * 0.85,
             ha="right")

    # status line
    bypassed = t >= 16.0
    scol = AMBER_HOT if bypassed else AMBER
    stxt = "SAFETY CHECK: BYPASSED" if bypassed else "SAFETY CHECK: PASS"
    text(ax, 5, 1.7, stxt, 32, scol, alpha=appear, stroke=1.4)

    # explanation after overflow
    text(ax, 5, 0.95, "0 reads as 'safe to fire'  —  incorrectly", 24, CREAM,
         alpha=reveal(t, 19.0, 0.8))

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


def draw_race_condition(ax, t, dur):
    # ---- title + subtitle -------------------------------------------------
    text(ax, 5, 9.25, "THE RACE CONDITION", 46, AMBER,
         alpha=reveal(t, 0.2, 0.7), stroke=1.6)
    text(ax, 5, 8.5, "operator has an ~8s window to change beam mode",
         24, CREAM, alpha=reveal(t, 1.0, 0.7))

    # ---- lane geometry ----------------------------------------------------
    lx0, lx1 = 3.2, 8.7          # lane left/right (track span)
    lbl_x = 1.55                 # x for lane labels (left gutter)
    sw_y = 6.55                  # SOFTWARE lane y
    hw_y = 3.55                  # HARDWARE lane y
    bw, bh = 2.05, 1.0           # state-box size

    sw_app = reveal(t, 1.8, 0.7)
    hw_app = reveal(t, 2.4, 0.7)

    # lane labels
    text(ax, lbl_x, sw_y, "SOFTWARE", 26, AMBER, alpha=sw_app, ha="center", stroke=1.2)
    text(ax, lbl_x, hw_y, "HARDWARE", 26, AMBER, alpha=hw_app, ha="center", stroke=1.2)
    text(ax, lbl_x, sw_y - 0.55, "(display)", 18, MUTE, alpha=sw_app, ha="center")
    text(ax, lbl_x, hw_y - 0.55, "(turntable)", 18, MUTE, alpha=hw_app, ha="center")

    # the two lane tracks (left to right)
    if sw_app > 0.01:
        ax.plot([lx0 - 0.4, lx1], [sw_y, sw_y], color=AMBER_D, lw=2,
                alpha=sw_app * 0.55, zorder=1)
    if hw_app > 0.01:
        ax.plot([lx0 - 0.4, lx1], [hw_y, hw_y], color=AMBER_D, lw=2,
                alpha=hw_app * 0.55, zorder=1)

    # ---- phase logic ------------------------------------------------------
    # PHASE 1 (0-8): both X-RAY, operator types, 8s window bracket
    # PHASE 2 (8-18): SOFTWARE flips to ELECTRON (display updated)
    # PHASE 3 (18-26): HARDWARE stays X-RAY, target NOT moved
    # PHASE 4 (26-31.9): DIVERGENCE highlighted, AMBER_HOT
    sw_flipped = t >= 8.0
    diverge = t >= 26.0

    # box x centres: phase-1 single state on left, phase-2 second state to right
    bx_a = lx0 + bw / 2          # left state box centre
    bx_b = lx1 - bw / 2          # right state box centre (after the window)

    # ---- SOFTWARE lane boxes ---------------------------------------------
    # initial X-RAY state (both lanes share this at start)
    text(ax, bx_a, sw_y + bh / 2 + 0.4, "start", 16, MUTE, alpha=sw_app * 0.8)
    box(ax, bx_a, sw_y, bw, bh, edge=AMBER, alpha=sw_app)
    text(ax, bx_a, sw_y, "X-RAY", 30, CREAM, alpha=sw_app, stroke=1.0)

    # arrow from start to the flipped state
    flip_in = reveal(t, 8.0, 0.8)
    if flip_in > 0.01:
        arrow(ax, bx_a + bw / 2 + 0.05, sw_y, bx_b - bw / 2 - 0.05, sw_y,
              color=AMBER, lw=3, alpha=flip_in)
        # the updated display box
        box(ax, bx_b, sw_y, bw, bh, edge=AMBER_HOT,
            fill=NAVY2, fill_alpha=0.6, alpha=flip_in)
        pop = pulse(t, 8.2, 0.5)
        text(ax, bx_b, sw_y, "ELECTRON", 27 + 4 * pop, AMBER_HOT,
             alpha=flip_in, stroke=1.0)
        text(ax, bx_b, sw_y + bh / 2 + 0.4, "display updated", 18, AMBER,
             alpha=reveal(t, 8.6, 0.7))

    # ---- HARDWARE lane boxes ---------------------------------------------
    box(ax, bx_a, hw_y, bw, bh, edge=AMBER, alpha=hw_app)
    text(ax, bx_a, hw_y, "X-RAY", 30, CREAM, alpha=hw_app, stroke=1.0)

    # hardware stays put — a flat "no change" arrow / it never reaches box B
    stay = reveal(t, 18.0, 0.9)
    if stay > 0.01:
        # a dashed/stalled track showing hardware did NOT move to box B
        ax.plot([bx_a + bw / 2 + 0.05, bx_b - bw / 2 - 0.05], [hw_y, hw_y],
                color=MUTE, lw=2.5, alpha=stay * 0.7, ls=(0, (4, 4)), zorder=1)
        # an empty ghost slot where ELECTRON should be (but isn't)
        box(ax, bx_b, hw_y, bw, bh, edge=MUTE, fill=NAVY2, fill_alpha=0.25,
            lw=2, alpha=stay * 0.6)
        text(ax, bx_b, hw_y, "no target", 24, MUTE, alpha=stay)
        text(ax, bx_b, hw_y - bh / 2 - 0.4, "target NOT moved", 18, MUTE,
             alpha=reveal(t, 18.6, 0.8))
        # a small "STAYS X-RAY" callout under the start box
        text(ax, bx_a, hw_y - bh / 2 - 0.4, "turntable stays", 18, CREAM,
             alpha=stay * 0.9)

    # ---- PHASE 1: operator types + 8s window bracket ---------------------
    # marker sits between the lanes on the left, fades out once flip happens
    type_app = reveal(t, 3.0, 0.7) * (1.0 - reveal(t, 8.0, 1.0))
    if type_app > 0.01:
        ty = (sw_y + hw_y) / 2
        text(ax, bx_a, ty + 0.22, "operator types...", 22, AMBER,
             alpha=type_app, stroke=0.8)
        # an 8s-window bracket spanning the gap toward box B
        bxl, bxr = bx_a + bw / 2 + 0.1, bx_b - bw / 2 - 0.1
        ax.plot([bxl, bxr], [ty - 0.45, ty - 0.45], color=AMBER, lw=2.5,
                alpha=type_app, zorder=4)
        ax.plot([bxl, bxl], [ty - 0.45, ty - 0.25], color=AMBER, lw=2.5,
                alpha=type_app, zorder=4)
        ax.plot([bxr, bxr], [ty - 0.45, ty - 0.25], color=AMBER, lw=2.5,
                alpha=type_app, zorder=4)
        text(ax, (bxl + bxr) / 2, ty - 0.78, "8 second window", 20, AMBER,
             alpha=type_app)

    # ---- PHASE 4: the DIVERGENCE ----------------------------------------
    if diverge:
        dv = reveal(t, 26.0, 0.8)
        # vertical gap connector between the two right-hand boxes
        arrow(ax, bx_b, sw_y - bh / 2 - 0.1, bx_b, hw_y + bh / 2 + 0.1,
              color=AMBER_HOT, lw=4, alpha=dv)
        # mismatch flash centred in the gap
        flash(ax, bx_b, (sw_y + hw_y) / 2, t, 26.4, r0=0.7, r1=1.5, d=0.8)
        text(ax, bx_b + 0.25, (sw_y + hw_y) / 2, "MISMATCH", 24, AMBER_HOT,
             alpha=dv, ha="left", stroke=1.0)

    # ---- bottom lethal line ---------------------------------------------
    lethal = reveal(t, 27.5, 0.9)
    if lethal > 0.01:
        text(ax, 5, 1.5, "SOFTWARE says ELECTRON  -  HARDWARE still X-RAY",
             24, AMBER_HOT, alpha=lethal, stroke=1.2)
        text(ax, 5, 0.92, "mismatch = beam fires with no target in place",
             22, CREAM, alpha=reveal(t, 28.3, 0.9))

    footer(ax, t)


def draw_beam_fires(ax, t, dur):
    # ---- title ----
    text(ax, 5, 9.2, "THE BEAM FIRES", 46, AMBER, alpha=reveal(t, 0.1, 0.5),
         stroke=1.6)

    # ---- PHASE 1: emitter (top) + patient (bottom) ----
    em = reveal(t, 0.5, 0.6)          # emitter appears
    box(ax, 5, 8.05, 3.0, 0.95, edge=AMBER, fill=NAVY2, fill_alpha=0.6,
        lw=3.5, alpha=em)
    text(ax, 5, 8.05, "EMITTER", 26, CREAM, alpha=em)

    # patient position (bottom)
    pat = reveal(t, 1.0, 0.6)
    box(ax, 5, 1.55, 3.4, 0.95, edge=AMBER, fill=NAVY2, fill_alpha=0.6,
        lw=3.5, alpha=pat)
    text(ax, 5, 1.55, "PATIENT", 26, CREAM, alpha=pat)

    # ---- the two ABSENT safeguards, in the gap, struck through ----
    # they scaffold phase 1, then fade out as the beam fires (phase 3 simplifies)
    lbl = reveal(t, 1.7, 0.6) * (1.0 - reveal(t, 4.4, 0.6))
    # NO target (left)
    text(ax, 2.45, 5.85, "TARGET", 24, AMBER_HOT, alpha=lbl)
    if lbl > 0.01:
        ax.plot([1.35, 3.55], [5.85, 5.85], color=AMBER_HOT, lw=3, alpha=lbl,
                zorder=5, solid_capstyle="round")
    text(ax, 2.45, 5.25, "(absent)", 18, MUTE, alpha=lbl)
    # NO filter (right)
    text(ax, 7.55, 5.85, "FILTER", 24, AMBER_HOT, alpha=lbl)
    if lbl > 0.01:
        ax.plot([6.45, 8.65], [5.85, 5.85], color=AMBER_HOT, lw=3, alpha=lbl,
                zorder=5, solid_capstyle="round")
    text(ax, 7.55, 5.25, "(absent)", 18, MUTE, alpha=lbl)
    text(ax, 5, 6.7, "NO target   -   NO filter", 22, CREAM, alpha=lbl * 0.9)

    # ---- PHASE 2: the full-power beam FIRES straight down ----
    # beam grows from emitter (y=7.55) to patient (y=2.05) between t=3 and t=4
    fire = reveal(t, 3.0, 0.9)
    if fire > 0.01:
        y_top = 7.55
        y_bot = 2.05
        y_now = y_top - (y_top - y_bot) * fire
        # thick, brutal beam — sustained flicker once it has landed
        flick = 0.0
        if t >= 3.9:
            flick = 0.5 + 0.5 * np.sin((t - 3.9) * 11.0)
        lwbeam = 16 + 8 * pulse(t, 3.0, 1.2) + 3 * flick
        # hot glow underlay (wide, fainter) — drawn first
        ax.plot([5, 5], [y_top, y_now], color=AMBER, lw=lwbeam + 16,
                alpha=fire * 0.22, zorder=3, solid_capstyle="round")
        ax.plot([5, 5], [y_top, y_now], color=AMBER_HOT, lw=lwbeam + 7,
                alpha=fire * 0.35, zorder=3.5, solid_capstyle="round")
        # core beam (brightest)
        ax.plot([5, 5], [y_top, y_now], color=AMBER_HOT, lw=lwbeam, alpha=fire,
                zorder=4, solid_capstyle="round")
        # white-hot inner thread for the lethal-energy read
        ax.plot([5, 5], [y_top, y_now], color=CREAM, lw=max(2, lwbeam * 0.28),
                alpha=fire * 0.85, zorder=4.5, solid_capstyle="round")

    # ---- PHASE 3: impact on the patient + hard caption ----
    # impact zone: a hot persistent glow on the patient that flickers, plus
    # overlapping radiating bursts so the gut-punch is present at every dwell
    # frame from t~5 to the end (offset off integer seconds, long decay).
    imp = reveal(t, 4.9, 0.5)
    if imp > 0.01:
        gl = 0.55 + 0.45 * abs(np.sin(t * 9.0))
        ax.scatter([5], [2.05], s=2600 * imp, c=AMBER_HOT, alpha=0.30 * imp * gl,
                   zorder=3, linewidths=0)
        ax.scatter([5], [2.05], s=1300 * imp, c=CREAM, alpha=0.35 * imp * gl,
                   zorder=3.5, linewidths=0)
    for t0 in (5.1, 5.8, 6.5, 7.2, 7.9):
        flash(ax, 5, 2.05, t, t0, r0=1.0, r1=2.4, n=14, color=AMBER_HOT, d=1.1)

    # hard caption: two clean stacks flanking the beam, never sitting on it.
    # RIGHT = FULL POWER (amber-hot), LEFT = NO TARGET (cream), matched baselines.
    yA, yB = 4.55, 3.55
    cap = reveal(t, 5.2, 0.55)
    text(ax, 5.75, yA, "FULL", 44, AMBER_HOT, alpha=cap, ha="left", stroke=1.8)
    text(ax, 5.75, yB, "POWER.", 44, AMBER_HOT, alpha=cap, ha="left", stroke=1.8)
    cap2 = reveal(t, 5.8, 0.55)
    text(ax, 4.25, yA, "NO", 44, CREAM, alpha=cap2, ha="right", stroke=1.8)
    text(ax, 4.25, yB, "TARGET.", 44, CREAM, alpha=cap2, ha="right", stroke=1.8)

    footer(ax, t)


def draw_false_safe(ax, t, dur):
    # ---- Title (persists whole scene) ----
    text(ax, 5, 9.25, "ZERO MEANS 'SAFE'", 46, AMBER, alpha=reveal(t, 0.2, 0.7),
         stroke=1.6)

    def seg(t_in, t_out, d=0.6):
        """Alpha for a phase that fades IN at t_in and OUT ending at t_out."""
        return reveal(t, t_in, d) * (1.0 - reveal(t, t_out - d, d))

    # ===================================================================
    # PHASE 1 (~0-7s): counter rolls down and lands on a big 0
    # ===================================================================
    p1 = seg(1.2, 7.4, 0.6)
    if p1 > 0.01:
        text(ax, 5, 8.35, "Class3 counter", 24, CREAM, alpha=p1 * 0.9)
        if t < 2.4:   val = 3
        elif t < 3.0: val = 2
        elif t < 3.6: val = 1
        else:         val = 0
        vcol = AMBER_HOT if val == 0 else AMBER
        pop = max((pulse(t, ct, 0.3) for ct in [2.4, 3.0, 3.6]), default=0.0)
        text(ax, 5, 6.1, str(val), 165 + 30 * pop, vcol, alpha=p1, stroke=2.0)
        land = reveal(t, 3.6, 0.4)
        text(ax, 5, 3.7, "Class3 = 0  ->  all safety checks pass", 28, CREAM,
             alpha=p1 * land)

    # ===================================================================
    # PHASE 2 (~7-14s): a SAFE lamp lights (amber) but tagged a LIE; the odds
    # ===================================================================
    p2 = seg(7.2, 14.0, 0.6)
    if p2 > 0.01:
        text(ax, 5, 8.2, "the software lights its indicator", 26, CREAM,
             alpha=p2 * 0.9)
        # the SAFE lamp (amber lamp, pulsing glow) -- dark label reads like a lit light
        glow = 0.30 + 0.22 * (0.5 + 0.5 * np.sin((t - 7.2) * 2.2))
        box(ax, 3.4, 5.4, 3.0, 1.7, edge=AMBER, fill=AMBER,
            fill_alpha=glow * p2, lw=4, alpha=p2)
        text(ax, 3.4, 5.4, "SAFE", 52, NAVY, alpha=p2, stroke=0.0)
        # the lie tag below the lamp
        false_a = reveal(t, 8.6, 0.6) * p2
        text(ax, 3.4, 3.6, "...but it's a LIE", 30, AMBER_HOT, alpha=false_a,
             stroke=1.4)
        # the odds, right side
        odds = reveal(t, 10.0, 0.7) * p2
        text(ax, 7.2, 6.25, "beam lands here:", 24, CREAM, alpha=odds * 0.9)
        text(ax, 7.2, 5.1, "1 in 256", 50, AMBER, alpha=odds, stroke=1.6)
        text(ax, 7.2, 3.9, "per cycle", 22, MUTE, alpha=odds * 0.9)

    # ===================================================================
    # PHASE 3 (~14-20s): consequence chain  0 -> interlocks satisfied -> BEAM FIRES
    # ===================================================================
    p3 = seg(14.2, 20.4, 0.6)
    if p3 > 0.01:
        cy = 5.5
        text(ax, 5, 8.2, "so the software concludes:", 26, CREAM, alpha=p3 * 0.9)
        # node A: 0
        text(ax, 1.55, cy, "0", 78, AMBER, alpha=p3, stroke=2.0)
        a2 = reveal(t, 15.2, 0.5) * p3
        arrow(ax, 2.35, cy, 3.35, cy, color=AMBER, lw=3, alpha=a2)
        # node B: interlocks satisfied
        box(ax, 5.0, cy, 2.9, 1.5, edge=AMBER, fill=NAVY2, fill_alpha=0.55,
            lw=3, alpha=a2)
        text(ax, 5.0, cy + 0.33, "interlocks", 24, CREAM, alpha=a2)
        text(ax, 5.0, cy - 0.35, "satisfied", 24, CREAM, alpha=a2)
        a3 = reveal(t, 16.6, 0.5) * p3
        arrow(ax, 6.65, cy, 7.55, cy, color=AMBER_HOT, lw=3, alpha=a3)
        # node C: BEAM FIRES (lethal beat)
        fire = reveal(t, 17.4, 0.5) * p3
        text(ax, 8.7, cy + 0.4, "BEAM", 36, AMBER_HOT, alpha=fire, stroke=1.6)
        text(ax, 8.7, cy - 0.45, "FIRES", 36, AMBER_HOT, alpha=fire, stroke=1.6)
        flash(ax, 8.7, cy, t, 17.6, r0=1.05, r1=2.0, d=0.8)
        text(ax, 5, 2.9, "the interlocks were satisfied -- and the beam fired",
             26, CREAM, alpha=reveal(t, 18.4, 0.7) * p3)

    # ===================================================================
    # PHASE 4 (~20-28s): hardware interlocks removed -- nothing left to catch it
    # ===================================================================
    p4 = reveal(t, 20.6, 0.6)
    if p4 > 0.01:
        text(ax, 5, 8.2, "and the last line of defense?", 26, CREAM, alpha=p4 * 0.9)
        text(ax, 5, 6.55, "HARDWARE INTERLOCKS", 32, MUTE, alpha=p4 * 0.85)
        labels = ["MECH STOP", "FUSE", "BACKUP"]
        bw, gap = 2.4, 0.5
        total = 3 * bw + 2 * gap
        x0 = 5 - total / 2 + bw / 2
        for i, lb in enumerate(labels):
            bx = x0 + i * (bw + gap)
            ba = reveal(t, 21.0 + 0.3 * i, 0.5)
            box(ax, bx, 4.7, bw, 1.25, edge=MUTE, fill=NAVY2, fill_alpha=0.30,
                lw=2.5, alpha=ba * 0.7)
            # label ABOVE the box (in cream) so the amber X can cross the box out
            # without obscuring the text — the old centred grey label was illegible.
            text(ax, bx, 5.72, lb, 22, CREAM, alpha=ba * 0.95)
            xa = reveal(t, 21.8 + 0.3 * i, 0.45)
            if xa > 0.01:
                hw, hh = bw / 2 - 0.12, 0.62
                ax.plot([bx - hw, bx + hw], [4.7 - hh, 4.7 + hh],
                        color=AMBER_HOT, lw=5, alpha=xa, solid_capstyle="round",
                        zorder=6)
                ax.plot([bx - hw, bx + hw], [4.7 + hh, 4.7 - hh],
                        color=AMBER_HOT, lw=5, alpha=xa, solid_capstyle="round",
                        zorder=6)
        text(ax, 5, 2.85, "REMOVED", 44, AMBER_HOT, alpha=reveal(t, 23.0, 0.7),
             stroke=1.6)
        text(ax, 5, 1.75, "nothing left to catch it", 28, CREAM,
             alpha=reveal(t, 23.8, 0.8))

    footer(ax, t)


# ---- scene registry --------------------------------------------------------
SCENES = {
    "byte_overflow": draw_byte_overflow,
    "linac": draw_linac,
    "race_condition": draw_race_condition,
    "beam_fires": draw_beam_fires,
    "false_safe": draw_false_safe,
}


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


def render_template(template: str, duration_s: float, out_path: str | Path,
                    fps: int = 15, boil: int = 5, finish: bool = True) -> str | None:
    draw = SCENES.get(template)
    if draw is None:
        print(f"[sketch] no scene for template {template!r}; have {list(SCENES)}")
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
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-framerate", str(fps), "-i", str(tdp / "f%05d.png"),
                   "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-vf", "scale=1280:720", "-an", str(raw)]
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
