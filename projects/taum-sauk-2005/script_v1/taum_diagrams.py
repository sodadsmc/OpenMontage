"""Taum Sauk explanatory diagrams (manim 0.20).

Two templates on the house duotone palette (deep navy + warm amber, ink):
  PumpedStorageCycle  -> seg_005  (water battery: pump up at night / generate by day)
  SensorGap008        -> seg_008  (five probes; top+bottom wired, middle three silent)
  SensorGap015        -> seg_015  (master: TRUE vs SENSED level, the 4.2 ft gap)
  SensorGap016        -> seg_016  (damning geometry: fail-safe above the wall low point)
  SensorGap026        -> seg_026  (essence: the gap between TRUE and BELIEVED, held)

Timed to the narration length (elements reveal as the VO explains them, then a
modest end hold) so no long frozen tail. One shared coordinate frame, crest = 0:
  wall settled crest .... y  0.0   (the low point, Panel 72 — water spills here)
  original wall top ..... y +0.7   (a foot higher, before settling)
  five probes ........... y +0.7 .. +1.9   (hang near the OLD top; all now above crest)
  emergency stop ........ y +0.7   (lowest wired probe: 0.7 ft ABOVE the crest)
  TRUE water (last night) y +0.2   (already over the crest, spilling)
  SENSED water .......... y -0.9   (machine believes 6 ft below top: the 4.2 ft lie)
"""
from manim import *
import numpy as np

BG     = "#091327"
AMBER  = "#f3a541"
CREAM  = "#e0d9c5"
INK    = "#0c1a2e"
WATER  = "#2b6f8f"
WATER2 = "#1c4a63"
BLUE   = "#8fb8d6"
RED    = "#d0413a"
GREY   = "#5a6b82"
STONE  = "#6b6256"

config.background_color = BG


def label(txt, size=26, color=CREAM, weight=NORMAL):
    return Text(txt, font_size=size, color=color, weight=weight)


# --------------------------------------------------------------------------- #
class PumpedStorageCycle(Scene):
    def construct(self):
        self.camera.background_color = BG
        mountain = Polygon([-6.4, -3.4, 0], [0.2, 2.6, 0], [6.4, -3.4, 0],
                           color=STONE, fill_color="#14243c", fill_opacity=1, stroke_width=2)
        upper = RoundedRectangle(width=2.6, height=0.9, corner_radius=0.15,
                                 stroke_color=AMBER, stroke_width=3,
                                 fill_color=WATER2, fill_opacity=0.9).move_to([0.2, 1.9, 0])
        upper_lbl = label("UPPER RESERVOIR", 20, AMBER, BOLD).next_to(upper, UP, buff=0.15)
        lower = Rectangle(width=5.2, height=0.8, stroke_color=WATER, stroke_width=2,
                          fill_color=WATER2, fill_opacity=0.9).move_to([-2.7, -3.0, 0])
        lower_lbl = label("LOWER RESERVOIR  (river, 90 m below)", 18, CREAM).move_to([-2.7, -2.35, 0])
        shaft = Line([-1.05, -2.7, 0], [-0.1, 1.55, 0], stroke_color=CREAM, stroke_width=7)
        plant = Rectangle(width=0.7, height=0.5, stroke_color=CREAM, stroke_width=2,
                          fill_color=INK, fill_opacity=1).move_to([-1.05, -2.55, 0])
        title = label("A BATTERY MADE OF WATER AND GRAVITY", 30, CREAM, BOLD).to_edge(UP, buff=0.4)

        self.play(FadeIn(mountain), run_time=1.0)
        self.play(Write(title), run_time=1.2)
        self.play(Create(lower), FadeIn(lower_lbl), Create(shaft), FadeIn(plant),
                  Create(upper), FadeIn(upper_lbl), run_time=2.4)
        self.wait(1.4)

        # NIGHT: pump water UP
        moon = Circle(radius=0.32, color=CREAM, fill_color=CREAM, fill_opacity=0.9).move_to([5.2, 2.4, 0])
        night = label("NIGHT   —   cheap power   —   PUMPING UP", 26, BLUE, BOLD).to_edge(DOWN, buff=0.35)
        self.play(FadeIn(moon), FadeIn(night), run_time=0.9)
        up_arrow = Arrow([-1.05, -2.4, 0], [-0.2, 1.4, 0], color=BLUE, stroke_width=8, buff=0.1)
        self.play(GrowArrow(up_arrow), run_time=0.6)
        for _ in range(4):
            drops = VGroup(*[Dot(color="#9fd0ec", radius=0.07).move_to(shaft.point_from_proportion(0.02))
                             for _ in range(4)])
            self.add(drops)
            self.play(*[d.animate.move_to(shaft.point_from_proportion(min(0.98, 0.25 * j + 0.05)))
                        for j, d in enumerate(drops)], run_time=1.1, rate_func=linear)
            self.play(upper.animate.set_fill(WATER, opacity=0.95), run_time=0.2)
            self.remove(drops)
        self.wait(0.8)

        # DAY: generate DOWN
        self.play(FadeOut(moon), FadeOut(up_arrow), FadeOut(night), run_time=0.6)
        sun = VGroup(Circle(radius=0.34, color=AMBER, fill_color=AMBER, fill_opacity=1).move_to([5.2, 2.4, 0]))
        for a in range(8):
            r = Line([5.2 + 0.42 * np.cos(a * PI / 4), 2.4 + 0.42 * np.sin(a * PI / 4), 0],
                     [5.2 + 0.62 * np.cos(a * PI / 4), 2.4 + 0.62 * np.sin(a * PI / 4), 0],
                     stroke_color=AMBER, stroke_width=3)
            sun.add(r)
        day = label("DAY   —   peak demand   —   GENERATING DOWN", 26, AMBER, BOLD).to_edge(DOWN, buff=0.35)
        down_arrow = Arrow([-0.2, 1.4, 0], [-1.05, -2.4, 0], color=AMBER, stroke_width=8, buff=0.1)
        self.play(FadeIn(sun), FadeIn(day), GrowArrow(down_arrow), run_time=0.9)
        bolt = label("POWER", 24, AMBER, BOLD).next_to(plant, LEFT, buff=0.2)
        for k in range(4):
            drops = VGroup(*[Dot(color="#9fd0ec", radius=0.07).move_to(shaft.point_from_proportion(0.98))
                             for _ in range(4)])
            self.add(drops)
            self.play(*[d.animate.move_to(shaft.point_from_proportion(max(0.02, 0.98 - 0.25 * j)))
                        for j, d in enumerate(drops)], run_time=1.1, rate_func=linear)
            if k == 0:
                self.play(FadeIn(bolt), run_time=0.2)
            self.remove(drops)
        self.wait(2.6)


# --------------------------------------------------------------------------- #
class _SensorBase(Scene):
    CREST = 0.0
    ORIG = 0.7
    PROBES = [0.7, 1.0, 1.3, 1.6, 1.9]
    WX0, WX1 = 2.2, 3.5
    WBOT = -3.0
    LEFT_X = -6.4
    LBL_X = -3.3

    def build_base(self):
        wall = Polygon([self.WX0, self.WBOT, 0], [self.WX0, self.CREST, 0],
                       [self.WX1, self.CREST, 0], [self.WX1, self.WBOT, 0],
                       color=STONE, fill_color="#3a352c", fill_opacity=1, stroke_width=2)
        orig = DashedLine([self.WX0 - 0.15, self.ORIG, 0], [self.WX1 + 0.15, self.ORIG, 0],
                          color=GREY, stroke_width=2, dash_length=0.1)
        orig_lbl = label("original wall top", 15, GREY).next_to(orig, RIGHT, buff=0.12)
        crest_lbl = label("WALL CREST\n(settled low point, Panel 72)", 15, CREAM)\
            .next_to([self.WX1, self.CREST, 0], RIGHT, buff=0.15)
        mast = Line([self.WX0, self.CREST, 0], [self.WX0, self.PROBES[-1] + 0.3, 0],
                    stroke_color=STONE, stroke_width=5)
        return VGroup(wall, mast, orig, orig_lbl, crest_lbl), wall

    def water(self, level, color=WATER, opacity=0.7):
        return Rectangle(width=(self.WX0 - self.LEFT_X), height=(level - self.WBOT),
                         stroke_width=0, fill_color=color, fill_opacity=opacity)\
            .move_to([(self.LEFT_X + self.WX0) / 2, (level + self.WBOT) / 2, 0])

    def probe(self, y, wired):
        c = RED if wired else GREY
        dot = Circle(radius=0.1, color=c, fill_color=c, fill_opacity=1).move_to([self.WX0 - 0.45, y, 0])
        stem = Line([self.WX0 - 0.45, y, 0], [self.WX0, y, 0], stroke_color=c, stroke_width=3)
        return VGroup(dot, stem)

    def connect(self, probe, lbl, color):
        return Line(probe[0].get_left(), lbl.get_right() + RIGHT * 0.12, color=color, stroke_width=2)


class SensorGap008(_SensorBase):
    def construct(self):
        self.camera.background_color = BG
        title = label("FIVE BACKUP PROBES", 30, CREAM, BOLD).to_edge(UP, buff=0.35)
        base, wall = self.build_base()
        self.play(Write(title), run_time=1.0)
        self.play(FadeIn(base), run_time=1.6)
        self.wait(1.2)
        # probes reveal one at a time (bottom to top)
        probes = [self.probe(y, wired=(i in (0, 4))) for i, y in enumerate(self.PROBES)]
        for p in probes:
            self.play(FadeIn(p), run_time=0.35)
            self.wait(0.25)
        self.wait(2.2)  # "bare metal switches that close when water touches them"
        # top + bottom -> wired to KILL PUMPS
        kt = label("WIRED  →  KILL PUMPS", 20, RED, BOLD).move_to([self.LBL_X, self.PROBES[4], 0])
        kb = label("WIRED  →  KILL PUMPS", 20, RED, BOLD).move_to([self.LBL_X, self.PROBES[0], 0])
        lt = self.connect(probes[4], kt, RED)
        lb = self.connect(probes[0], kb, RED)
        self.play(probes[0][0].animate.scale(1.3), probes[4][0].animate.scale(1.3),
                  Create(lt), Create(lb), FadeIn(kt), FadeIn(kb), run_time=1.6)
        self.wait(2.6)
        # middle three -> silent
        bell = label("NO ALARM\nnot even logged", 21, GREY, BOLD).move_to([self.LBL_X, self.PROBES[2], 0])
        slines = VGroup(*[self.connect(probes[i], bell, GREY) for i in (1, 2, 3)])
        self.play(*[probes[i].animate.set_opacity(0.45) for i in (1, 2, 3)],
                  Create(slines), FadeIn(bell), run_time=1.8)
        self.wait(4.0)


class SensorGap015(_SensorBase):
    def construct(self):
        self.camera.background_color = BG
        title = label("THE MACHINE'S PICTURE OF THE WORLD", 27, CREAM, BOLD).to_edge(UP, buff=0.35)
        base, wall = self.build_base()
        self.play(Write(title), run_time=1.0)
        self.play(FadeIn(base), run_time=1.4)
        probes = [self.probe(y, wired=(i in (0, 4))) for i, y in enumerate(self.PROBES)]
        self.play(*[FadeIn(p) for p in probes], run_time=1.0)
        self.wait(1.4)
        true_y, sens_y = 0.2, -0.9
        # sensed first (what the machine believes)
        w = self.water(sens_y)
        sens_line = Line([self.LEFT_X, sens_y, 0], [self.WX0, sens_y, 0], color=WATER, stroke_width=4)
        sens_lbl = label("SENSED level  —  \"6 ft below the top\"", 18, BLUE)\
            .next_to([-2.0, sens_y, 0], DOWN, buff=0.1)
        self.play(FadeIn(w), Create(sens_line), FadeIn(sens_lbl), run_time=1.2)
        self.wait(2.6)
        # then the truth
        true_line = DashedLine([self.LEFT_X, true_y, 0], [self.WX0, true_y, 0], color=AMBER,
                               stroke_width=4, dash_length=0.15)
        true_lbl = label("TRUE water level", 20, AMBER, BOLD).next_to([-4.4, true_y, 0], UP, buff=0.1)
        self.play(Create(true_line), FadeIn(true_lbl), run_time=1.2)
        self.wait(2.2)
        gap = DoubleArrow([-5.5, sens_y, 0], [-5.5, true_y, 0], color=RED, stroke_width=5, buff=0.03)
        gap_lbl = label("4.2 ft\nLIE", 22, RED, BOLD).next_to(gap, RIGHT, buff=0.12)
        self.play(GrowFromCenter(gap), FadeIn(gap_lbl), run_time=1.3)
        self.wait(2.6)
        note = label("sensors ride in loose pipes  —  reading 4.2 ft low", 18, CREAM).to_edge(DOWN, buff=0.35)
        self.play(FadeIn(note), run_time=0.9)
        self.wait(3.8)


class SensorGap016(_SensorBase):
    def construct(self):
        self.camera.background_color = BG
        title = label("THE FAIL-SAFE THAT COULD NOT FIRE", 27, RED, BOLD).to_edge(UP, buff=0.35)
        base, wall = self.build_base()
        low_wired = self.probe(self.PROBES[0], wired=True)
        self.play(Write(title), run_time=1.0)
        self.play(FadeIn(base), FadeIn(low_wired), run_time=1.5)
        self.wait(1.2)
        crest_line = Line([self.LEFT_X, self.CREST, 0], [self.WX0, self.CREST, 0], color=CREAM, stroke_width=3)
        probe_line = DashedLine([self.LEFT_X, self.PROBES[0], 0], [self.WX0, self.PROBES[0], 0],
                                color=RED, stroke_width=3, dash_length=0.14)
        cl = label("wall crest  —  water spills here", 18, CREAM).next_to([-3.2, self.CREST, 0], DOWN, buff=0.1)
        pl = label("emergency stop set HERE", 18, RED, BOLD).next_to([-3.2, self.PROBES[0], 0], UP, buff=0.1)
        self.play(Create(crest_line), FadeIn(cl), run_time=1.0)
        self.wait(0.8)
        self.play(Create(probe_line), FadeIn(pl), run_time=1.0)
        self.wait(1.2)
        meas = DoubleArrow([-5.4, self.CREST, 0], [-5.4, self.PROBES[0], 0], color=RED, stroke_width=5, buff=0.03)
        meas_lbl = label("0.7 ft", 22, RED, BOLD).next_to(meas, LEFT, buff=0.12)
        self.play(GrowFromCenter(meas), FadeIn(meas_lbl), run_time=1.1)
        self.wait(2.2)
        w = self.water(self.CREST - 1.4, opacity=0.7)
        self.add(w)
        spill = label("water is already pouring over the dam", 20, AMBER, BOLD).to_edge(DOWN, buff=0.35)
        self.play(w.animate.become(self.water(self.CREST + 0.15, opacity=0.7)),
                  FadeIn(spill), run_time=2.2)
        self.wait(1.4)
        stillsafe = label("... the machine still reads SAFE", 20, GREY).next_to(spill, UP, buff=0.15)
        self.play(FadeIn(stillsafe), run_time=0.9)
        self.wait(3.4)


class SensorGap026(_SensorBase):
    def construct(self):
        self.camera.background_color = BG
        base, wall = self.build_base()
        title = label("EVERY PART TOLD ITS OWN SMALL TRUTH", 25, CREAM, BOLD).to_edge(UP, buff=0.35)
        self.play(FadeIn(base), Write(title), run_time=1.8)
        self.wait(1.6)
        true_y, sens_y = 0.2, -0.9
        w = self.water(sens_y)
        true_line = DashedLine([self.LEFT_X, true_y, 0], [self.WX0, true_y, 0], color=AMBER,
                               stroke_width=4, dash_length=0.15)
        sens_line = Line([self.LEFT_X, sens_y, 0], [self.WX0, sens_y, 0], color=WATER, stroke_width=4)
        tl = label("TRUE", 22, AMBER, BOLD).next_to([-5.6, true_y, 0], UP, buff=0.08)
        sl = label("BELIEVED", 22, BLUE, BOLD).next_to([-5.6, sens_y, 0], DOWN, buff=0.08)
        self.play(FadeIn(w), Create(true_line), Create(sens_line), FadeIn(tl), FadeIn(sl), run_time=2.2)
        self.wait(2.8)
        gap = DoubleArrow([-3.0, sens_y, 0], [-3.0, true_y, 0], color=RED, stroke_width=5, buff=0.04)
        gap_lbl = label("the gap is where\ndisasters live", 22, RED, BOLD).next_to(gap, RIGHT, buff=0.2)
        self.play(GrowFromCenter(gap), FadeIn(gap_lbl), run_time=1.6)
        self.wait(3.4)
        self.play(FadeOut(base), FadeOut(w), FadeOut(title), FadeOut(tl), FadeOut(sl),
                  FadeOut(gap), FadeOut(gap_lbl), run_time=1.8)
        self.play(true_line.animate.set_opacity(0.3), sens_line.animate.set_opacity(0.3), run_time=1.0)
        self.wait(1.4)
        crt = RoundedRectangle(width=3.2, height=2.2, corner_radius=0.12, stroke_color=AMBER,
                               stroke_width=3, fill_color="#1a1206", fill_opacity=1).move_to([2.4, -0.2, 0])
        glow = label("6.0 ft", 34, AMBER, BOLD).move_to(crt.get_center())
        self.play(FadeIn(crt), FadeIn(glow), run_time=1.5)
        self.wait(4.0)
        self.play(FadeOut(true_line), FadeOut(sens_line), run_time=1.6)
        self.wait(2.2)
