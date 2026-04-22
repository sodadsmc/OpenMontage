"""Visual strategy router for the narrated-documentary pipeline.

Reads a scene's ``visual_strategy`` field and generates the appropriate
visual asset — Manim animation, Mermaid diagram, Remotion chart, or
text card.  Scenes routed to ``stock_footage``, ``archival``, or
``generated`` are handled by the existing footage_search and gap_fill
stages and return None here.

Runs AFTER footage_search and BEFORE assembly.  Produces short MP4 or
PNG files that the assembly stage inserts alongside stock footage cuts.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class VisualAsset:
    """A generated visual asset ready for the assembly stage."""
    scene_id: str
    path: str
    kind: str        # "video" or "image"
    duration: float  # seconds (0 for images)
    strategy: str    # which visual_strategy produced this
    description: str


def route_scene(
    scene: dict[str, Any],
    output_dir: str | Path,
    topic: str = "",
) -> VisualAsset | None:
    """Generate a visual asset for one scene based on its visual_strategy.

    Returns None for strategies handled by other pipeline stages
    (stock_footage, archival, generated, mixed).
    """
    strategy = scene.get("visual_strategy", "stock_footage")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if strategy == "math_animation":
        return _generate_manim(scene, output_dir, topic)
    elif strategy == "diagram":
        return _generate_manim_diagram(scene, output_dir, topic)
    elif strategy == "animated_chart":
        return _generate_remotion_chart(scene, output_dir)
    elif strategy == "text_card":
        return _generate_text_card(scene, output_dir)
    else:
        # stock_footage, archival, generated, mixed — handled elsewhere
        return None


def route_all_scenes(
    segment_plan: dict[str, Any],
    output_dir: str | Path,
    topic: str = "",
) -> dict[str, VisualAsset]:
    """Route all scenes and return a map of scene_id → VisualAsset.

    Only scenes with generatable strategies are included.
    """
    results: dict[str, VisualAsset] = {}
    for scene in segment_plan.get("scenes", []):
        asset = route_scene(scene, output_dir, topic)
        if asset is not None:
            results[asset.scene_id] = asset
            _log.info("Generated %s for %s: %s", asset.strategy, asset.scene_id, asset.path)
    return results


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _generate_manim(
    scene: dict[str, Any],
    output_dir: Path,
    topic: str,
) -> VisualAsset | None:
    """Generate a Manim animation for math/data scenes.

    Uses the scene's narration and visual_description to determine
    what to animate.  Currently supports pre-built animation types
    that match common documentary patterns.
    """
    sid = scene["scene_id"]
    narration = scene.get("narration", "")
    vis_desc = scene.get("visual_description", "")
    duration = float(scene.get("duration_seconds", 10))

    # Detect what type of math animation is needed
    narr_lower = narration.lower()

    if any(kw in narr_lower for kw in ["dose", "rad ", "gray", "overdose", "prescribed"]):
        return _manim_dose_chart(scene, output_dir)
    elif any(kw in narr_lower for kw in ["overflow", "counter", "255", "256", "byte", "rollover"]):
        return _manim_byte_overflow(scene, output_dir)
    elif any(kw in narr_lower for kw in ["timeline", "chronolog", "sequence of events"]):
        return _manim_timeline(scene, output_dir)
    else:
        _log.warning("No Manim template matched for %s, falling back to text card", sid)
        return _generate_text_card(scene, output_dir)


def _generate_manim_diagram(
    scene: dict[str, Any],
    output_dir: Path,
    topic: str,
) -> VisualAsset | None:
    """Generate a Manim animated diagram (flowchart, state diagram)."""
    sid = scene["scene_id"]
    narr_lower = scene.get("narration", "").lower()

    if any(kw in narr_lower for kw in ["race condition", "timing", "8 second", "edit"]):
        return _manim_race_condition(scene, output_dir)
    elif any(kw in narr_lower for kw in ["state machine", "mode", "x-ray", "electron"]):
        return _manim_state_machine(scene, output_dir)
    elif any(kw in narr_lower for kw in ["flow", "keystroke", "malfunction", "press"]):
        return _manim_data_flow(scene, output_dir)
    else:
        return _manim_race_condition(scene, output_dir)  # default diagram


def _generate_remotion_chart(
    scene: dict[str, Any],
    output_dir: Path,
) -> VisualAsset | None:
    """Generate an animated chart via Remotion Explainer composition."""
    # TODO: implement Remotion chart rendering
    _log.warning("Remotion chart generation not yet implemented for %s", scene["scene_id"])
    return _generate_text_card(scene, output_dir)


def _generate_text_card(
    scene: dict[str, Any],
    output_dir: Path,
) -> VisualAsset | None:
    """Generate a text card as a PNG image (renders as Ken Burns in CinematicRenderer)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        _log.warning("Pillow not installed — cannot generate text cards")
        return None

    sid = scene["scene_id"]
    narration = scene.get("narration", "")

    # Extract the key text — first sentence or the whole thing if short
    text = narration.split(".")[0].strip() + "." if "." in narration else narration
    if len(text) > 120:
        text = text[:117] + "..."

    # Create dark card
    img = Image.new("RGB", (1920, 1080), color=(10, 10, 26))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 52)
        small_font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        small_font = font

    # Wrap text
    wrapped = textwrap.fill(text, width=40)
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (1920 - text_w) // 2
    y = (1080 - text_h) // 2
    draw.text((x, y), wrapped, fill=(243, 246, 250), font=font)

    # Source citation at bottom
    source = scene.get("source_citation", "")
    if source:
        draw.text((60, 1020), source, fill=(128, 128, 128), font=small_font)

    out_path = output_dir / f"{sid}_text_card.png"
    img.save(str(out_path), quality=95)

    return VisualAsset(
        scene_id=sid,
        path=str(out_path),
        kind="image",
        duration=0,
        strategy="text_card",
        description=text[:60],
    )


# ---------------------------------------------------------------------------
# Manim scene generators
# ---------------------------------------------------------------------------

def _run_manim_scene(code: str, class_name: str, output_dir: Path, scene_id: str) -> VisualAsset | None:
    """Write Manim code to a temp file, render it, return the asset."""
    script_path = output_dir / f"{scene_id}_manim.py"
    script_path.write_text(code, encoding="utf-8")

    try:
        # Run via Python API (CLI has path bugs on Windows)
        abs_script = str(script_path.resolve()).replace("\\", "\\\\")
        abs_media = str((output_dir / "media").resolve()).replace("\\", "\\\\")
        render_code = f"""
import sys
exec(open(r'{abs_script}').read())
from manim import tempconfig
with tempconfig({{
    'quality': 'medium_quality',
    'media_dir': r'{abs_media}',
    'disable_caching': True,
    'pixel_width': 1920,
    'pixel_height': 1080,
    'frame_rate': 30,
}}):
    scene = {class_name}()
    scene.render()
    print('MANIM_OUTPUT:' + str(scene.renderer.file_writer.movie_file_path))
"""
        result = subprocess.run(
            ["python", "-c", render_code],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            _log.warning("Manim render failed for %s: %s", scene_id, result.stderr[-300:])
            return None

        # Find output path
        for line in result.stdout.splitlines():
            if line.startswith("MANIM_OUTPUT:"):
                mp4_path = line.split(":", 1)[1].strip()
                if Path(mp4_path).is_file():
                    duration = _probe_duration(mp4_path)
                    return VisualAsset(
                        scene_id=scene_id,
                        path=mp4_path,
                        kind="video",
                        duration=duration,
                        strategy="manim",
                        description=class_name,
                    )

        _log.warning("Manim output not found for %s", scene_id)
        return None

    except subprocess.TimeoutExpired:
        _log.warning("Manim render timed out for %s", scene_id)
        return None
    except Exception as exc:
        _log.warning("Manim render error for %s: %s", scene_id, exc)
        return None


def _manim_dose_chart(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated dose comparison bar chart."""
    code = '''
from manim import *

class DoseChart(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("Radiation Dose Comparison", font_size=36, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        bars = BarChart(
            values=[86, 10000],
            bar_names=["Prescribed\\n(86 rad)", "Received\\n(~10,000 rad)"],
            y_range=[0, 12000, 2000],
            y_length=4,
            x_length=8,
            bar_colors=[GREEN, RED],
            y_axis_config={"label_constructor": Text, "font_size": 18},
            x_axis_config={"label_constructor": Text, "font_size": 18},
        )
        bars.next_to(title, DOWN, buff=0.5)
        self.play(Create(bars), run_time=2)

        label = Text("116x overdose", font_size=30, color=RED, weight=BOLD)
        label.next_to(bars, DOWN, buff=0.3)
        self.play(FadeIn(label))

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=14, color=GREY)
        source.to_edge(DOWN, buff=0.2)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "DoseChart", output_dir, scene["scene_id"])


def _manim_byte_overflow(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated byte counter showing the 255→0 overflow."""
    code = '''
from manim import *

class ByteOverflow(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("The Arithmetic Overflow", font_size=36, color=WHITE, weight=BOLD)
        subtitle = Text("Class3 variable: 1 byte (max 255)", font_size=20, color=GREY_B)
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.15)
        header.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.5)

        counter = Text("252", font_size=120, color=GREEN)
        counter.move_to(ORIGIN)
        counter_label = Text("Class3 value", font_size=22, color=GREY_B)
        counter_label.next_to(counter, UP, buff=0.3)

        status = Text("SAFETY CHECK: PASS", font_size=24, color=GREEN)
        status.next_to(counter, DOWN, buff=0.5)

        self.play(FadeIn(counter_label), FadeIn(counter), FadeIn(status))
        self.wait(0.5)

        # Count up: 252 -> 253 -> 254 -> 255
        warning = None
        for val in [253, 254, 255]:
            new_counter = Text(str(val), font_size=120, color=GREEN if val < 255 else YELLOW)
            new_counter.move_to(ORIGIN)
            self.play(Transform(counter, new_counter), run_time=0.4)
            if val == 255:
                warning = Text("MAX VALUE", font_size=18, color=YELLOW)
                warning.next_to(counter, RIGHT, buff=0.5)
                self.play(FadeIn(warning), run_time=0.3)
            self.wait(0.2)

        self.wait(0.5)

        # OVERFLOW: 255 -> 0
        zero = Text("0", font_size=120, color=RED)
        zero.move_to(ORIGIN)
        anims = [Transform(counter, zero)]
        if warning:
            anims.append(FadeOut(warning))
        self.play(*anims, run_time=0.5)
        self.play(Flash(counter, color=RED, line_length=0.5, num_lines=12), run_time=0.6)

        new_status = Text("SAFETY CHECK: BYPASSED", font_size=24, color=RED, weight=BOLD)
        new_status.move_to(status)
        self.play(Transform(status, new_status), run_time=0.5)

        explanation = Text("Value 0 = safe to proceed (incorrectly)", font_size=18, color=RED_B)
        explanation.next_to(status, DOWN, buff=0.3)
        self.play(FadeIn(explanation), run_time=0.5)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=14, color=GREY)
        source.to_edge(DOWN, buff=0.2)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "ByteOverflow", output_dir, scene["scene_id"])


def _manim_race_condition(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated flowchart showing the race condition."""
    code = '''
from manim import *

class RaceCondition(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("The Race Condition", font_size=38, color=WHITE, weight=BOLD)
        subtitle = Text("How the Therac-25 killed patients", font_size=20, color=GREY_B)
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.15)
        header.to_edge(UP, buff=0.3)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.5)

        def make_node(text, color=BLUE, w=3.5, h=0.65):
            box = RoundedRectangle(corner_radius=0.12, width=w, height=h, fill_color=color, fill_opacity=0.15, stroke_color=color, stroke_width=2)
            lbl = Text(text, font_size=17, color=WHITE)
            lbl.move_to(box)
            return VGroup(box, lbl)

        def make_diamond(text, color=YELLOW, s=1.3):
            d = Square(side_length=s, color=color, fill_opacity=0.12, stroke_width=2).rotate(PI/4)
            lbl = Text(text, font_size=15, color=WHITE)
            lbl.move_to(d)
            return VGroup(d, lbl)

        n1 = make_node("Operator types command", BLUE_C)
        n2 = make_node("Changes mode (X to E)", BLUE_C)
        n3 = make_diamond("Edit < 8\\nseconds?", YELLOW, 1.5)
        n_danger = make_node("Software state inconsistent", RED, w=3.8)
        n_safe = make_node("Normal operation", GREEN)
        n_mal = make_node("Malfunction 54 displayed", ORANGE, w=3.8)
        n_press = make_node("Operator presses P", ORANGE, w=3.8)
        n_fire = make_node("BEAM FIRES — NO TARGET", RED_E, w=3.8, h=0.75)
        n_ok = make_node("Safe treatment delivered", GREEN)

        n1.move_to(UP * 2.0)
        n2.move_to(UP * 0.9)
        n3.move_to(DOWN * 0.4)
        n_danger.move_to(DOWN * 1.6 + LEFT * 3.2)
        n_safe.move_to(DOWN * 1.6 + RIGHT * 3.2)
        n_mal.move_to(DOWN * 2.6 + LEFT * 3.2)
        n_press.move_to(DOWN * 3.5 + LEFT * 3.2)
        n_fire.move_to(DOWN * 3.5 + RIGHT * 1.5)
        n_ok.move_to(DOWN * 2.6 + RIGHT * 3.2)

        def varrow(s, e, c=GREY_B):
            return Arrow(s.get_bottom(), e.get_top(), buff=0.08, color=c, stroke_width=2, max_tip_length_to_length_ratio=0.12)

        a1 = varrow(n1, n2)
        a2 = varrow(n2, n3)
        a_yes = Arrow(n3.get_left()+DOWN*0.2, n_danger.get_top(), buff=0.08, color=RED, stroke_width=2.5, max_tip_length_to_length_ratio=0.12)
        a_no = Arrow(n3.get_right()+DOWN*0.2, n_safe.get_top(), buff=0.08, color=GREEN, stroke_width=2.5, max_tip_length_to_length_ratio=0.12)
        yes_lbl = Text("YES", font_size=14, color=RED, weight=BOLD).next_to(a_yes, LEFT, buff=0.08).shift(UP*0.3)
        no_lbl = Text("NO", font_size=14, color=GREEN, weight=BOLD).next_to(a_no, RIGHT, buff=0.08).shift(UP*0.3)
        a_mal = varrow(n_danger, n_mal, RED)
        a_press = varrow(n_mal, n_press, ORANGE)
        a_fire = Arrow(n_press.get_right(), n_fire.get_left(), buff=0.08, color=RED_E, stroke_width=3, max_tip_length_to_length_ratio=0.12)
        a_ok = varrow(n_safe, n_ok, GREEN)

        self.play(FadeIn(n1, shift=DOWN*0.2), run_time=0.5)
        self.play(GrowArrow(a1), FadeIn(n2, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.4)
        self.play(GrowArrow(a2), FadeIn(n3, scale=0.8), run_time=0.7)
        self.wait(0.5)
        self.play(GrowArrow(a_no), FadeIn(no_lbl), FadeIn(n_safe, shift=LEFT*0.3), run_time=0.7)
        self.play(GrowArrow(a_ok), FadeIn(n_ok, shift=DOWN*0.2), run_time=0.5)
        self.wait(0.3)
        self.play(GrowArrow(a_yes), FadeIn(yes_lbl), FadeIn(n_danger, shift=RIGHT*0.3), run_time=0.8)
        self.play(n_danger[0].animate.set_fill(RED, opacity=0.25), run_time=0.4)
        self.wait(0.3)
        self.play(GrowArrow(a_mal), FadeIn(n_mal, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.3)
        self.play(GrowArrow(a_press), FadeIn(n_press, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.3)
        self.play(GrowArrow(a_fire), run_time=0.6)
        self.play(FadeIn(n_fire, scale=1.1), n_fire[0].animate.set_fill(RED_E, opacity=0.4), run_time=0.5)
        self.play(Flash(n_fire, color=RED, line_length=0.4, num_lines=16, flash_radius=1.2), Indicate(n_fire, color=RED_E, scale_factor=1.05), run_time=0.8)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "RaceCondition", output_dir, scene["scene_id"])


def _manim_state_machine(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """X-ray / Electron mode state machine diagram."""
    # TODO: implement
    _log.warning("State machine Manim not yet implemented for %s", scene["scene_id"])
    return _generate_text_card(scene, output_dir)


def _manim_data_flow(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Keystroke → Malfunction 54 → P → Fire data flow."""
    # TODO: implement
    _log.warning("Data flow Manim not yet implemented for %s", scene["scene_id"])
    return _generate_text_card(scene, output_dir)


def _manim_timeline(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Timeline of Therac-25 incidents."""
    # TODO: implement
    _log.warning("Timeline Manim not yet implemented for %s", scene["scene_id"])
    return _generate_text_card(scene, output_dir)


def _probe_duration(path: str) -> float:
    """Get video duration via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0
