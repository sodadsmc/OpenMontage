"""Taum Sauk explanatory diagrams as hand-inked, narration-SYNCED sketch scenes
(lib.sketch_diagrams style) — the house graphic-novel look, animating at every
point (rippling water, blinking probes, flowing penstock, breathing gap arrows),
graded by the channel finishing pass so they sit in the same look as the footage.

  taum_pumped   -> seg_005  (water battery: pump up at night / generate by day)
  taum_probes   -> seg_008  (five probes; top+bottom wired, middle three silent)
  taum_gap      -> seg_015  (TRUE vs BELIEVED water level, the 4.2 ft lie)
  taum_failsafe -> seg_016  (emergency stop set ABOVE the wall low point)
  taum_essence  -> seg_026  (the gap between TRUE and BELIEVED, then the CRT)

Each factory takes a resolved cue map C = {cue: start_seconds} and returns a
draw(ax, t, dur). Render via render_diagrams_sketch.py (resolve_cues + finishing).
"""
import numpy as np
from matplotlib.patches import Polygon, Circle
from lib.sketch_diagrams import (
    text, box, arrow, reveal, pulse, flash, pulse_glow, clamp,
    NAVY, NAVY2, AMBER, AMBER_HOT, AMBER_D, CREAM, MUTE)

ROCK = "#2a2318"   # rockfill (warm-dark -> low amber after duotone)
WFILL = "#122238"  # water body (dark navy)


# --- source footnote (taum, not therac) ------------------------------------
def taum_footer(ax, t):
    text(ax, 5, 0.32, "Taum Sauk Upper Reservoir  -  FERC / Ameren investigation, 2006",
         15, CREAM, alpha=reveal(t, 1.0, 0.9) * 0.5)


# --- always-moving water: a wavy filled surface -----------------------------
def ripple(ax, x0, x1, floor, level, t, alpha=0.55, amp=0.07, speed=2.1,
           fill=WFILL, surf=AMBER):
    xs = np.linspace(x0, x1, 46)
    top = level + amp * (np.sin(xs * 2.0 + t * speed) + 0.4 * np.sin(xs * 4.6 - t * 1.4))
    verts = [(x0, floor)] + list(zip(xs, top)) + [(x1, floor)]
    ax.add_patch(Polygon(verts, closed=True, facecolor=fill, edgecolor="none",
                         alpha=alpha, zorder=2))
    ax.plot(xs, top, color=surf, lw=2, alpha=min(1.0, alpha + 0.35), zorder=3,
            solid_capstyle="round")
    return top


# --- dots travelling along a segment (penstock flow) ------------------------
def flow(ax, p0, p1, t, t0, period=1.1, n=5, color=AMBER_HOT, alpha=1.0, rev=False):
    if t < t0 or alpha <= 0.01:
        return
    (x0, y0), (x1, y1) = p0, p1
    for i in range(n):
        ph = (((t - t0) + i * period / n) % period) / period
        if rev:
            ph = 1 - ph
        ax.scatter([x0 + (x1 - x0) * ph], [y0 + (y1 - y0) * ph], s=85, c=color,
                   alpha=alpha * 0.9, zorder=6, linewidths=0)


# ===========================================================================
# seg_005 — the water battery
# ===========================================================================
PUMP_CUES = {"title": "a battery", "grav": "water and gravity", "night": "At night",
             "pumps": "push water", "up": "up the mountain", "day": "In the day",
             "down": "falls back down", "power": "becomes electricity"}


def taum_pumped(C):
    UP = (5.0, 6.95)      # upper reservoir centre
    LO = (2.55, 1.95)     # lower reservoir centre
    PH = (3.5, 2.25)      # powerhouse
    p0, p1 = (3.5, 2.55), (4.15, 6.6)   # penstock endpoints

    def draw(ax, t, dur):
        # mountain
        m = reveal(t, C["grav"], 0.7)
        ax.add_patch(Polygon([(0.5, 1.6), (5.0, 8.3), (9.5, 1.6)], closed=True,
                             facecolor="#0f1c30", edgecolor=MUTE, lw=1.5,
                             alpha=m, zorder=1))
        text(ax, 5, 9.25, "A BATTERY OF WATER AND GRAVITY", 40, AMBER,
             alpha=reveal(t, C["title"], 0.6), stroke=1.6)

        # reservoirs + powerhouse + penstock
        box(ax, UP[0], UP[1], 2.3, 0.72, edge=AMBER, fill=WFILL, fill_alpha=0.0, lw=3, alpha=m)
        ripple(ax, UP[0] - 1.05, UP[0] + 1.05, UP[1] - 0.34, UP[1] + 0.16, t, alpha=0.75 * m)
        text(ax, UP[0], UP[1] + 0.62, "UPPER RESERVOIR", 18, CREAM, alpha=m)
        box(ax, LO[0], LO[1], 3.3, 0.66, edge=AMBER, fill=WFILL, fill_alpha=0.0, lw=2.5, alpha=m)
        ripple(ax, LO[0] - 1.55, LO[0] + 1.55, LO[1] - 0.3, LO[1] + 0.12, t, alpha=0.7 * m)
        text(ax, LO[0], LO[1] - 0.62, "LOWER RESERVOIR  (river)", 16, CREAM, alpha=m)
        ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=CREAM, lw=6, alpha=m, zorder=2,
                solid_capstyle="round")
        box(ax, PH[0], PH[1], 0.85, 0.62, edge=CREAM, fill=NAVY2, fill_alpha=1.0, lw=2, alpha=m)

        night = reveal(t, C["night"], 0.6) * (1 - reveal(t, C["day"] - 0.4, 0.5))
        day = reveal(t, C["day"], 0.6)

        # NIGHT: pump UP
        if night > 0.01:
            ax.add_patch(Circle((8.4, 7.7), 0.34, facecolor=CREAM, edgecolor="none",
                                alpha=night * 0.9, zorder=4))
            flow(ax, p0, p1, t, C["pumps"], period=1.0, color="#bcd6ea", alpha=night)
            arrow(ax, 5.5, 3.2, 4.7, 6.0, color="#bcd6ea", lw=3, alpha=night * reveal(t, C["up"], 0.5))
            text(ax, 5.0, 0.95, "NIGHT   -   cheap power   -   PUMPING UP", 24,
                 CREAM, alpha=night, stroke=1.0)

        # DAY: generate DOWN
        if day > 0.01:
            sx, sy = 8.4, 7.7
            ax.add_patch(Circle((sx, sy), 0.36, facecolor=AMBER_HOT, edgecolor="none",
                                alpha=day, zorder=4))
            for a in range(8):
                ang = a * np.pi / 4
                ax.plot([sx + 0.45 * np.cos(ang), sx + 0.66 * np.cos(ang)],
                        [sy + 0.45 * np.sin(ang), sy + 0.66 * np.sin(ang)],
                        color=AMBER_HOT, lw=2.5, alpha=day, zorder=4)
            flow(ax, p0, p1, t, C["down"], period=0.85, color=AMBER_HOT, alpha=day, rev=True)
            arrow(ax, 4.7, 6.0, 5.5, 3.2, color=AMBER_HOT, lw=3, alpha=day)
            pw = reveal(t, C["power"], 0.5)
            pulse_glow(ax, PH[0], PH[1], t, C["power"], rmax=1.1, alpha=pw)
            text(ax, PH[0] - 1.15, PH[1], "POWER", 20, AMBER_HOT, alpha=pw, ha="right", stroke=1.0)
            text(ax, 5.0, 0.95, "DAY   -   peak demand   -   GENERATING DOWN", 24,
                 AMBER, alpha=day, stroke=1.0)
    return draw


# ===========================================================================
# shared reservoir cross-section for the sensor diagrams
# ===========================================================================
FLOOR, CREST, ORIG = 1.8, 5.2, 6.05
WX0, WX1 = 6.5, 9.3          # rockfill base
WTX0, WTX1 = 7.15, 8.75      # rockfill crest span
WLEFT = 6.5                  # water meets the wall here
PROBE_X = 6.35
PROBE_Y = [5.35, 5.78, 6.21, 6.64, 7.07]


def reservoir(ax, t, appear=1.0, show_orig=False, label_dam=True, crest_label=True):
    ax.add_patch(Polygon([(WX0, FLOOR), (WTX0, CREST), (WTX1, CREST), (WX1, FLOOR)],
                         closed=True, facecolor=ROCK, edgecolor=AMBER_D, lw=2,
                         alpha=appear, zorder=2))
    box(ax, (WTX0 + WTX1) / 2, CREST + 0.13, (WTX1 - WTX0) + 0.22, 0.3,
        edge=CREAM, fill=NAVY2, fill_alpha=0.7, lw=2, alpha=appear)
    if crest_label:
        text(ax, (WTX0 + WTX1) / 2, CREST - 0.55, "top of the wall", 15, CREAM, alpha=appear * 0.85)
    if label_dam:
        text(ax, (WX0 + WX1) / 2 + 0.05, FLOOR + 1.05, "THE DAM WALL", 15, CREAM, alpha=appear * 0.65)
        text(ax, (WX0 + WX1) / 2 + 0.05, FLOOR + 0.55, "(cross-section)", 12, MUTE, alpha=appear * 0.6)
    if show_orig:
        ax.plot([WTX0 - 0.35, WTX1 + 0.4], [ORIG, ORIG], color=MUTE, lw=1.6,
                dashes=(5, 3), alpha=appear * 0.7, zorder=3)
        text(ax, WTX1 + 0.55, ORIG + 0.02, "original height\n(wall has sunk)", 12, MUTE,
             alpha=appear * 0.7, ha="left")


def draw_probes(ax, t, appear, wired=(0, 4), dim_mid=False):
    ax.plot([PROBE_X + 0.18, PROBE_X + 0.18], [CREST, PROBE_Y[-1] + 0.2],
            color=MUTE, lw=3, alpha=appear * 0.8, zorder=3)  # mounting mast
    for i, y in enumerate(PROBE_Y):
        w = i in wired
        blink = 0.55 + 0.45 * np.sin(t * 3.0 + i * 1.3)
        col = AMBER_HOT if w else MUTE
        a = appear * (0.4 if (dim_mid and not w) else 1.0)
        ax.plot([PROBE_X, PROBE_X + 0.18], [y, y], color=col, lw=2.5, alpha=a, zorder=4)
        ax.add_patch(Circle((PROBE_X, y), 0.1, facecolor=col, edgecolor="none",
                            alpha=a * (0.6 + 0.4 * blink), zorder=5))


def level_line(ax, y, t, color, label, dashed=False, up=True, alpha=1.0, x1=WLEFT):
    xs = np.linspace(1.2, x1, 40)
    yy = y + 0.05 * np.sin(xs * 2.2 + t * 2.0)
    if dashed:
        ax.plot(xs, yy, color=color, lw=3, dashes=(6, 4), alpha=alpha, zorder=4)
    else:
        ax.plot(xs, yy, color=color, lw=3, alpha=alpha, zorder=4)
    text(ax, 1.6, y + (0.3 if up else -0.3), label, 17, color, alpha=alpha,
         ha="left", stroke=0.8)


# ===========================================================================
# seg_008 — five backup probes
# ===========================================================================
PROBE_CUES = {"title": "five simple probes", "switch": "bare metal switches",
              "topbot": "very top one", "kill": "wired to kill",
              "between": "ones between", "noalarm": "no alarm",
              "nolog": "write it down"}


def taum_probes(C):
    def draw(ax, t, dur):
        text(ax, 5, 9.25, "FIVE BACKUP PROBES", 42, AMBER,
             alpha=reveal(t, C["title"], 0.6), stroke=1.6)
        app = reveal(t, C["title"], 0.7)
        reservoir(ax, t, appear=app)
        ripple(ax, 1.2, WLEFT, FLOOR, 4.3, t, alpha=0.5 * app)  # water mid level, always moving
        # probes reveal
        pa = reveal(t, C["title"] + 0.4, 0.8)
        dim = reveal(t, C["between"], 0.6) > 0.4
        draw_probes(ax, t, pa, wired=(0, 4), dim_mid=dim)
        # wired -> KILL
        kill = reveal(t, C["kill"], 0.6)
        if kill > 0.01:
            for idx in (0, 4):
                y = PROBE_Y[idx]
                ax.plot([PROBE_X - 0.1, 3.6], [y, y], color=AMBER_HOT, lw=2, alpha=kill, zorder=4)
            text(ax, 3.4, PROBE_Y[4], "WIRED -> KILL PUMPS", 18, AMBER_HOT,
                 alpha=kill, ha="right", stroke=1.0)
            text(ax, 3.4, PROBE_Y[0], "WIRED -> KILL PUMPS", 18, AMBER_HOT,
                 alpha=kill, ha="right", stroke=1.0)
            pulse_glow(ax, PROBE_X, PROBE_Y[4], t, C["kill"], rmax=0.7, alpha=kill)
            pulse_glow(ax, PROBE_X, PROBE_Y[0], t, C["kill"], rmax=0.7, alpha=kill)
        # middle silent
        na = reveal(t, C["noalarm"], 0.6)
        if na > 0.01:
            for idx in (1, 2, 3):
                ax.plot([PROBE_X - 0.1, 3.6], [PROBE_Y[idx], PROBE_Y[2]], color=MUTE,
                        lw=1.6, alpha=na * 0.7, zorder=3)
            text(ax, 3.4, PROBE_Y[2] + 0.28, "NO ALARM", 20, MUTE, alpha=na, ha="right", stroke=1.0)
            text(ax, 3.4, PROBE_Y[2] - 0.32, "not even logged", 16, MUTE,
                 alpha=reveal(t, C["nolog"], 0.6), ha="right")
        taum_footer(ax, t)
    return draw


# ===========================================================================
# seg_015 — TRUE vs BELIEVED, the 4.2 ft lie
# ===========================================================================
GAP_CUES = {"title": "machine's picture", "loose": "loose pipes",
            "low": "four point two feet low", "believe": "six feet below the top",
            "catch": "catch the lie", "lowest": "lowest point", "middle": "wouldn't say"}
TRUE_Y, SENS_Y = 5.45, 4.05


def taum_gap(C):
    def draw(ax, t, dur):
        text(ax, 5, 9.25, "THE MACHINE'S PICTURE OF THE WORLD", 30, AMBER,
             alpha=reveal(t, C["title"], 0.6), stroke=1.4)
        app = reveal(t, C["title"], 0.7)
        reservoir(ax, t, appear=app)
        draw_probes(ax, t, reveal(t, C["catch"], 0.6), wired=(0, 4), dim_mid=False)
        # water sits at the SENSED (believed) level and ripples
        sens = reveal(t, C["loose"], 0.6)
        ripple(ax, 1.2, WLEFT, FLOOR, SENS_Y, t, alpha=0.55 * sens)
        if sens > 0.01:
            level_line(ax, SENS_Y, t, "#8fb8d6", "BELIEVED  (6 ft below top)",
                       dashed=False, up=False, alpha=reveal(t, C["believe"], 0.6))
        # the TRUE level, above the crest
        tr = reveal(t, C["catch"], 0.6)
        if tr > 0.01:
            level_line(ax, TRUE_Y, t, AMBER_HOT, "TRUE water level", dashed=True,
                       up=True, alpha=tr)
            # the 4.2 ft gap, breathing
            gx = 2.5
            breath = 0.5 + 0.5 * np.sin(t * 2.2)
            ax.annotate("", xy=(gx, TRUE_Y), xytext=(gx, SENS_Y),
                        arrowprops=dict(arrowstyle="<->", color=AMBER_HOT,
                                        lw=3 + breath, alpha=tr), zorder=5)
            text(ax, gx + 0.35, (TRUE_Y + SENS_Y) / 2, "4.2 ft\nLIE", 20, AMBER_HOT,
                 alpha=tr, ha="left", stroke=1.0)
        text(ax, 5, 1.15, "sensors ride in loose pipes  -  reading 4.2 ft low", 18,
             CREAM, alpha=reveal(t, C["low"], 0.6))
        taum_footer(ax, t)
    return draw


# ===========================================================================
# seg_016 — the fail-safe that could not fire
# ===========================================================================
FAIL_CUES = {"title": "Read that again", "stop": "emergency stop",
             "defense": "last line of defense", "seven": "seven tenths",
             "above": "above the lowest point", "pour": "pouring over the dam",
             "notice": "allowed to notice", "cannot": "could not fire"}


def taum_failsafe(C):
    def draw(ax, t, dur):
        text(ax, 5, 9.25, "THE FAIL-SAFE THAT COULD NOT FIRE", 30, AMBER_HOT,
             alpha=reveal(t, C["title"], 0.6), stroke=1.4)
        app = reveal(t, C["stop"], 0.6)
        reservoir(ax, t, appear=max(app, reveal(t, C["title"], 0.6)), crest_label=False)
        # the emergency stop sits a clear 0.7 ft ABOVE the crest so the fatal gap reads
        ey = CREST + 0.7
        sa = reveal(t, C["stop"], 0.6)
        ax.plot([PROBE_X, PROBE_X + 0.18], [ey, ey], color=AMBER_HOT, lw=3, alpha=sa, zorder=4)
        ax.add_patch(Circle((PROBE_X, ey), 0.12, facecolor=AMBER_HOT, edgecolor="none",
                            alpha=sa * (0.6 + 0.4 * np.sin(t * 3)), zorder=5))
        # crest line + probe line + the 0.7 ft measure between
        cl = reveal(t, C["above"], 0.6)
        ax.plot([1.2, WLEFT], [CREST, CREST], color=CREAM, lw=2.5, alpha=cl, zorder=4)
        text(ax, 1.5, CREST - 0.32, "wall crest (spills here)", 15, CREAM, alpha=cl, ha="left")
        ax.plot([1.2, PROBE_X], [ey, ey], color=AMBER_HOT, lw=2.5, dashes=(6, 4), alpha=sa, zorder=4)
        text(ax, 1.5, ey + 0.3, "emergency stop set HERE", 15, AMBER_HOT, alpha=sa, ha="left")
        sv = reveal(t, C["seven"], 0.5)
        if sv > 0.01:
            ax.annotate("", xy=(2.6, ey), xytext=(2.6, CREST),
                        arrowprops=dict(arrowstyle="<->", color=AMBER_HOT, lw=3, alpha=sv), zorder=5)
            text(ax, 2.9, (ey + CREST) / 2, "0.7 ft", 18, AMBER_HOT, alpha=sv, ha="left", stroke=1.0)
        # water RISES past the crest and spills, but stays BELOW the emergency stop
        pr = reveal(t, C["pour"], 0.9)
        wl = CREST - 1.6 + (CREST + 0.1 - (CREST - 1.6)) * pr   # rises to just over crest
        ripple(ax, 1.2, WLEFT, FLOOR, wl, t, alpha=0.6)
        if pr > 0.3:
            flow(ax, (WLEFT, CREST + 0.05), (WX1 - 0.2, FLOOR + 0.6), t, C["pour"],
                 period=0.7, n=6, color=AMBER_HOT, alpha=pr)  # spill down the outer face
            text(ax, 5.0, 1.15, "water is already pouring over the dam", 20, AMBER_HOT,
                 alpha=pr, stroke=1.0)
        ns = reveal(t, C["cannot"], 0.6)
        if ns > 0.01:
            text(ax, 5.0, 0.62, "...the machine still reads SAFE", 18, MUTE, alpha=ns)
        taum_footer(ax, t)
    return draw


# ===========================================================================
# seg_026 — the gap between TRUE and BELIEVED, then the CRT
# ===========================================================================
ESS_CUES = {"title": "here is the lesson", "told": "exactly what it was told",
            "faith": "reported faithfully", "drift": "drifted out of place",
            "truth": "its own small truth", "lied": "the whole system lied",
            "typed": "point four feet", "gap": "gap between", "live": "disasters live"}


def taum_essence(C):
    def draw(ax, t, dur):
        text(ax, 5, 9.25, "EVERY PART TOLD ITS OWN SMALL TRUTH", 28, AMBER,
             alpha=reveal(t, C["truth"], 0.6), stroke=1.3)
        fade = 1 - reveal(t, C["gap"] + 1.5, 1.2)      # the section fades late, leaving the gap
        app = reveal(t, C["title"], 0.7) * clamp(fade + 0.15)
        reservoir(ax, t, appear=app, show_orig=False, label_dam=False)
        ripple(ax, 1.2, WLEFT, FLOOR, SENS_Y, t, alpha=0.5 * app)
        # TRUE / BELIEVED lines persist through the fade
        lz = reveal(t, C["faith"], 0.6)
        level_line(ax, TRUE_Y, t, AMBER_HOT, "TRUE", dashed=True, up=True, alpha=lz)
        level_line(ax, SENS_Y, t, "#8fb8d6", "BELIEVED", dashed=False, up=False, alpha=lz)
        # the gap, emphasised on 'the whole system lied'
        gz = reveal(t, C["lied"], 0.6)
        if gz > 0.01:
            gx = 3.1
            breath = 0.5 + 0.5 * np.sin(t * 2.2)
            ax.annotate("", xy=(gx, TRUE_Y), xytext=(gx, SENS_Y),
                        arrowprops=dict(arrowstyle="<->", color=AMBER_HOT, lw=3 + breath, alpha=gz), zorder=5)
            text(ax, gx + 0.35, (TRUE_Y + SENS_Y) / 2, "the gap is where\ndisasters live", 18,
                 AMBER_HOT, alpha=reveal(t, C["gap"], 0.6), ha="left", stroke=1.0)
        # the CRT believing '6.0 ft', glowing, on 'typed point four feet'
        cz = reveal(t, C["typed"], 0.7)
        if cz > 0.01:
            box(ax, 5.0, 4.9, 3.0, 2.0, edge=AMBER, fill="#1a1206", fill_alpha=1.0,
                lw=3, alpha=cz)
            glow = 0.7 + 0.3 * np.sin(t * 4.0)
            text(ax, 5.0, 4.9, "6.0 ft", 40, AMBER_HOT, alpha=cz * glow, stroke=1.2)
            text(ax, 5.0, 3.6, "what the computer believed", 16, CREAM, alpha=cz * 0.8)
        taum_footer(ax, t)
    return draw


SCENES = {
    "seg_005": (taum_pumped, PUMP_CUES),
    "seg_008": (taum_probes, PROBE_CUES),
    "seg_015": (taum_gap, GAP_CUES),
    "seg_016": (taum_failsafe, FAIL_CUES),
    "seg_026": (taum_essence, ESS_CUES),
}


# ===========================================================================
# seg_011 — the email: the real quote types on as it is narrated
# ===========================================================================
EMAIL11_CUES = {"writes": "writes it down", "nono": "an absolute no-no",
                "erode": "erode the dam", "fail": "the dam will fail",
                "knew": "They knew", "writing": "It is in writing",
                "dated": "dated September", "weeks": "eleven weeks"}


def _boil(t, key=0, amp=0.02):
    """Small deterministic ink-boil offset, re-rolled 3x/sec (matplotlib's
    path.sketch wobble is deterministic per path, so a static hold renders
    pixel-identical frames — nudging the path each boil tick restores the
    hand-drawn 'boil' the operator expects instead of a dead still)."""
    rs = np.random.RandomState(9000 + 31 * key + int(t * 3))
    return rs.uniform(-amp, amp)


def _type_on(ax, x, y, s, size, color, t, t0, t1, ha="left", stroke=0.8):
    """Reveal s character-by-character between t0 and t1 (typewriter).
    Returns (text_artist, typing_now, chars_shown)."""
    if t < t0: return None, False, 0
    k = len(s) if t >= t1 else max(0, int(len(s) * (t - t0) / max(0.1, t1 - t0)))
    a = text(ax, x, y, s[:k], size, color, alpha=1.0, ha=ha, stroke=stroke) if k else None
    return a, t < t1, k


def _caret(ax, artist, y, t, h=0.42):
    """Blinking typewriter caret just right of a text artist's ink."""
    if artist is None or int(t * 2.4) % 2 == 1:
        return
    try:
        r = ax.figure.canvas.get_renderer()
        bb = artist.get_window_extent(r).transformed(ax.transData.inverted())
        x1 = bb.x1
    except Exception:
        return
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((x1 + 0.08, y - 0.08), 0.16, h, facecolor=AMBER_HOT,
                           edgecolor="none", alpha=0.9, zorder=6))


def taum_email011(C):
    LINES = [(6.3, '"Overflowing this reservoir', CREAM, "nono", -1.2, -0.1),
             (5.65, 'is an absolute NO-NO.', AMBER_HOT, "nono", -0.1, 1.1),
             (4.75, 'The water will erode the dam,', CREAM, "erode", -0.4, 1.0),
             (4.1, 'and the dam will fail."', AMBER_HOT, "fail", -0.2, 1.0)]

    def draw(ax, t, dur):
        wr = reveal(t, C["writing"], 0.6)
        glow = 1.0 if wr < 1 else 0.86 + 0.14 * (0.5 + 0.5 * np.sin(t * 1.7))
        text(ax, 5, 9.2, "IN WRITING", 40, AMBER, alpha=wr * glow, stroke=1.6)
        # paper panel — boiled each ink-tick so the hold never reads as a still
        pa = reveal(t, C["writes"], 0.7)
        if pa > 0.01:
            from matplotlib.patches import FancyBboxPatch
            bp = 0.12 + _boil(t, key=1, amp=0.012)
            p = FancyBboxPatch((1.1, 2.1), 7.8, 5.6, boxstyle=f"round,pad={bp:.4f}",
                               linewidth=2.5 + _boil(t, key=2, amp=0.3), edgecolor=AMBER_D,
                               facecolor="#101c30", alpha=min(1.0, pa), zorder=2)
            ax.add_patch(p)
            text(ax, 1.45, 7.28, "internal e-mail  -  Ameren", 15, MUTE, alpha=pa, ha="left")
            uy = 7.0 + _boil(t, key=3, amp=0.03)
            ax.plot([1.35, 8.65], [uy, uy + 0.02], color=AMBER_D, lw=1.5, alpha=pa * 0.8, zorder=3)
            # header furniture types on while the narrator sets the scene
            h0 = C["writes"] + 0.3
            _type_on(ax, 1.6, 6.85, "from:  engineering", 14, MUTE, t, h0, h0 + 1.0)
            _type_on(ax, 3.9, 6.85, "to:  plant operations", 14, MUTE, t, h0 + 1.1, h0 + 2.1)
            _type_on(ax, 6.35, 6.85, "re:  reservoir levels", 14, MUTE, t, h0 + 2.2, h0 + 3.2)
        # the quote types on AS the narrator reads it; caret rides the live line
        last_art, last_y = None, None
        typing_art, typing_y = None, None
        for y, s, col, cue, d0, d1 in LINES:
            a, live, k = _type_on(ax, 1.6, y, s, 26, col, t, C[cue] + d0, C[cue] + d1)
            if a is not None:
                last_art, last_y = a, y
                if live:
                    typing_art, typing_y = a, y
        if typing_art is not None:
            _caret(ax, typing_art, typing_y, t)
        elif last_art is not None:
            _caret(ax, last_art, last_y, t)   # parked caret keeps blinking
        # dateline lands on 'dated September twenty-seventh'
        dt = reveal(t, C["dated"], 0.5)
        if dt > 0.01:
            text(ax, 1.6, 3.0, "dated:  SEPTEMBER 27, 2005", 22, AMBER, alpha=dt, ha="left", stroke=1.0)
        wk = reveal(t, C["weeks"], 0.5)
        text(ax, 1.6, 2.45, "11 weeks before the breach", 19, MUTE, alpha=wk, ha="left")
        # quiet pulse on the no-no line at 'They knew'
        pulse_glow(ax, 3.6, 5.65, t, C["knew"], color=AMBER_HOT, d=1.4, rmax=1.6, alpha=0.6)
        taum_footer(ax, t)
    return draw


# ===========================================================================
# seg_013 — the arithmetic email: 7"/4" probes vs the wall top, then nothing
# ===========================================================================
EMAIL13_CUES = {"another": "Another email", "arith": "done the arithmetic",
                "seven": "seven inches", "four": "four inches", "top": "from the top",
                "reached": "must have reached", "tripped": "should have tripped",
                "didnt": "They didn't", "lower": "lower the probes",
                "next": "what happened next", "nothing": "Nothing"}


def taum_email013(C):
    TOPY, P7, P4 = 6.4, 5.05, 5.63   # wall-top line and probe heights (7" and 4" below)
    def draw(ax, t, dur):
        az = reveal(t, C["arith"], 0.6)
        glow = 1.0 if az < 1 else 0.88 + 0.12 * (0.5 + 0.5 * np.sin(t * 1.9))
        text(ax, 5, 9.2, "SOMEONE DID THE ARITHMETIC", 32, AMBER,
             alpha=az * glow, stroke=1.4)
        text(ax, 5, 8.5, "another e-mail  -  October 7, 2005", 18, MUTE,
             alpha=reveal(t, C["another"], 0.6))
        base = reveal(t, C["another"], 1.4)   # stage draws first, headline follows
        if base > 0.01:
            # wall-top line: draws on left-to-right, then keeps a hand-wave breath
            x1 = 1.6 + (8.4 - 1.6) * min(1.0, base)
            xs0 = np.linspace(1.6, x1, 32)
            ax.plot(xs0, TOPY + 0.02 * np.sin(xs0 * 3.1 + t * 1.6), color=CREAM,
                    lw=3, alpha=min(1.0, base + 0.3), zorder=4)
            text(ax, 8.3, TOPY + 0.3, "top of the wall", 16, CREAM, alpha=base, ha="right")
            # probes — each with its OWN measure arrow (separate x, no stacking)
            for y, cue, lbl, mx in ((P7, "seven", '7"', 4.6), (P4, "four", '4"', 7.9)):
                pr = reveal(t, C[cue], 0.4)
                if pr > 0.01:
                    blink = 0.6 + 0.4 * np.sin(t * 3.2 + y)
                    ax.add_patch(Circle((6.4, y), 0.13, facecolor=AMBER_HOT, edgecolor="none",
                                        alpha=pr * blink, zorder=5))
                    ax.plot([6.4, 7.0], [y, y], color=AMBER_HOT, lw=2.5, alpha=pr, zorder=4)
                    ax.plot([mx - 0.15, 6.4], [y, y], color=AMBER_D, lw=1.5, alpha=pr * 0.7, zorder=3)
                    br = 0.5 + 0.5 * np.sin(t * 2.1)
                    ax.annotate("", xy=(mx, TOPY), xytext=(mx, y),
                                arrowprops=dict(arrowstyle="<->", color=AMBER, lw=2.5 + br, alpha=pr), zorder=5)
                    text(ax, mx - 0.25, (TOPY + y) / 2, lbl, 21, AMBER, alpha=pr, ha="right", stroke=1.0)
        # September spill: water line rises to touch the probes
        rz = reveal(t, C["reached"], 1.0)
        if rz > 0.01:
            wl = P7 - 0.9 + (TOPY + 0.06 - (P7 - 0.9)) * min(1.0, rz)
            xs = np.linspace(1.6, 8.4, 40)
            ax.plot(xs, wl + 0.05 * np.sin(xs * 2.4 + t * 2.2), color="#8fb8d6", lw=3,
                    alpha=0.9, zorder=3)
            text(ax, 1.75, wl - 0.35, "September spill level", 15, "#8fb8d6", alpha=rz, ha="left")
            for y in (P7, P4):
                flash(ax, 6.4, y, t, C["tripped"], r0=0.3, r1=0.9, n=10, d=0.9)
        # they didn't
        dz = reveal(t, C["didnt"], 0.5)
        if dz > 0.01:
            text(ax, 5, 3.6, "they should have tripped  -  THEY DIDN'T", 24, AMBER_HOT,
                 alpha=dz, stroke=1.2)
        lz = reveal(t, C["lower"], 0.5)
        text(ax, 5, 2.75, '"we can lower the probes if you want"', 21, CREAM, alpha=lz)
        nz = reveal(t, C["nothing"], 0.8)
        if nz > 0.01:
            text(ax, 5, 1.9, "reply:  NOTHING", 26, MUTE, alpha=nz, stroke=1.0)
        taum_footer(ax, t)
    return draw


SCENES["seg_011"] = (taum_email011, EMAIL11_CUES)
SCENES["seg_013"] = (taum_email013, EMAIL13_CUES)


# ===========================================================================
# seg_022 b1+b2 — the emergency call list: real readable rows, Toops ringed
# ===========================================================================
CALL22_CUES = {"list": "emergency call list", "jerry": "Jerry Toops",
               "supt": "park superintendent"}

CALL22_ROWS = [
    ("PLANT MANAGER", "ext. 200"),
    ("SHIFT SUPERVISOR - OSAGE", "ext. 214"),
    ("PLANT ENGINEER", "ext. 221"),
    ("SECURITY GATE", "ext. 101"),
    ("REYNOLDS CO. SHERIFF", "dispatch"),
    ("J. TOOPS - PARK SUPT., JOHNSON'S SHUT-INS", "residence"),
    ("MODOT DISTRICT 9", "dispatch"),
    ("AMEREN ST. LOUIS DUTY DESK", "ext. 500"),
]
TOOPS_ROW = 5


def taum_call22(C):
    def draw(ax, t, dur):
        from matplotlib.patches import FancyBboxPatch, Ellipse
        pa = reveal(t, 0.2, 0.6)
        bp = 0.12 + _boil(t, key=11, amp=0.012)
        ax.add_patch(FancyBboxPatch((1.3, 1.3), 7.4, 7.4, boxstyle=f"round,pad={bp:.4f}",
                                    linewidth=2.5 + _boil(t, key=12, amp=0.3),
                                    edgecolor=AMBER_D, facecolor="#101c30",
                                    alpha=min(1.0, pa), zorder=2))
        text(ax, 5, 8.15, "EMERGENCY CALL LIST", 27, AMBER, alpha=pa, stroke=1.2)
        text(ax, 5, 7.55, "Taum Sauk Plant  -  December 14, 2005", 15, MUTE, alpha=pa)
        uy = 7.2 + _boil(t, key=13, amp=0.03)
        ax.plot([1.7, 8.3], [uy, uy + 0.02], color=AMBER_D, lw=1.5, alpha=pa * 0.8, zorder=3)
        t0 = C["list"] + 0.2
        y0, dy = 6.7, 0.62
        for r, (name, ext) in enumerate(CALL22_ROWS):
            rt = t0 + r * 0.45
            rr = reveal(t, rt, 0.35)
            if rr <= 0.01:
                continue
            hot = r == TOOPS_ROW
            col = AMBER_HOT if (hot and t >= C["jerry"]) else CREAM
            y = y0 - r * dy
            a, live, _k = _type_on(ax, 1.95, y, name, 16.5, col, t, rt, rt + 0.4)
            text(ax, 8.05, y, ext, 13, MUTE, alpha=rr * 0.9, ha="right")
            dots_x0 = 1.95 + 0.115 * len(name)
            if dots_x0 < 6.9:
                ax.plot([dots_x0 + 0.15, 6.95], [y - 0.05, y - 0.05], color=MUTE,
                        lw=1.0, ls=":", alpha=rr * 0.5, zorder=3)
        # the ring lands on 'Jerry Toops'
        rz = reveal(t, C["jerry"], 0.7)
        if rz > 0.01:
            y = y0 - TOOPS_ROW * dy
            e = Ellipse((4.45, y + 0.05), 5.4 * min(1.0, rz + 0.15), 0.72,
                        fill=False, edgecolor=AMBER_HOT,
                        lw=3 + _boil(t, key=14, amp=0.5), alpha=min(1.0, rz), zorder=6)
            ax.add_patch(e)
            text(ax, 5, 1.72, "lives DIRECTLY below the dam", 16, AMBER_HOT,
                 alpha=reveal(t, C.get("supt", C["jerry"] + 1.2), 0.5))
        taum_footer(ax, t)
    return draw


SCENES["seg_022_card"] = (taum_call22, CALL22_CUES)


# ===========================================================================
# seg_023 — what the investigation found: four hollowed layers, one at a time
# ===========================================================================
INVEST23_CUES = {"trust": "design of trust", "anchor": "re-anchored",
                 "offset": "offset instead of a repair", "silent": "wired to stay silent",
                 "sinking": "above the top of a sinking wall", "hollow": "hollowed out",
                 "reasonable": "one reasonable decision"}

INVEST23_ROWS = [
    ("anchor",  "SENSORS",        "never re-anchored"),
    ("offset",  "SOFTWARE",       "an offset instead of a repair"),
    ("silent",  "WARNING PROBES", "wired to stay silent"),
    ("sinking", "FAIL-SAFE",      "set above a sinking wall"),
]


def taum_invest23(C):
    def draw(ax, t, dur):
        from matplotlib.patches import FancyBboxPatch
        tz = reveal(t, C["trust"], 0.6)
        glow = 1.0 if tz < 1 else 0.88 + 0.12 * (0.5 + 0.5 * np.sin(t * 1.8))
        text(ax, 5, 9.2, "THE DESIGN OF TRUST", 34, AMBER, alpha=tz * glow, stroke=1.4)
        text(ax, 5, 8.55, "what the investigation found  -  2006", 16, MUTE, alpha=tz)
        pa = reveal(t, C["trust"] + 0.4, 0.7)
        if pa > 0.01:
            bp = 0.12 + _boil(t, key=21, amp=0.012)
            ax.add_patch(FancyBboxPatch((2.55, 2.0), 4.9, 5.9, boxstyle=f"round,pad={bp:.4f}",
                                        linewidth=2.5 + _boil(t, key=22, amp=0.3),
                                        edgecolor=AMBER_D, facecolor="#101c30",
                                        alpha=min(1.0, pa), zorder=2))
        y0, dy = 7.15, 1.28
        last_art = last_y = None
        for r, (cue, layer, what) in enumerate(INVEST23_ROWS):
            rz = reveal(t, C[cue] - 0.9, 0.5)
            if rz <= 0.01:
                continue
            y = y0 - r * dy
            text(ax, 3.0, y, layer, 21, AMBER_HOT, alpha=rz, ha="left", stroke=1.0)
            a, live, _k = _type_on(ax, 3.05, y - 0.52, what, 17, CREAM, t,
                                   C[cue] - 0.7, C[cue] + 0.5)
            if a is not None:
                last_art, last_y = a, y - 0.52
            # each layer gets struck through on 'hollowed out'
            hz = reveal(t, C["hollow"] + r * 0.25, 0.4)
            if hz > 0.01:
                sy = y + 0.05 + _boil(t, key=30 + r, amp=0.03)
                x1 = 2.9 + (0.45 + 0.135 * len(layer)) * min(1.0, hz)
                ax.plot([2.9, x1], [sy, sy - 0.04], color=AMBER_HOT,
                        lw=3.5, alpha=0.9, zorder=6)
        if last_art is not None:
            _caret(ax, last_art, last_y, t)
        rz = reveal(t, C["reasonable"], 0.6)
        if rz > 0.01:
            text(ax, 5, 2.45, "one reasonable decision at a time", 20, CREAM,
                 alpha=rz, stroke=1.0)
        taum_footer(ax, t)
    return draw


SCENES["seg_023_card"] = (taum_invest23, INVEST23_CUES)
