"""Visual Design System — 7 distinct text card templates.

Each card type has a unique visual identity: typography, color scheme,
animation style, and layout. This eliminates the "wall of sameness"
where every text card was white Helvetica on black.

Card types:
  stat_reveal       — Shocking numbers that slam in with impact
  chapter_title     — Minimal act/section dividers
  institutional_text — Corporate/regulatory text with typewriter feel
  technical_label   — Clean factual labels
  error_message     — CRT terminal green-on-black
  verdict           — Final damning conclusions, weighted items
  quote             — Attributed quotations in italic style

All templates produce Manim Scene code strings suitable for
_run_manim_scene(). They accept target_duration_s to control
pacing via wait() calls.
"""
from __future__ import annotations

from lib.visual_router import _sanitize_for_manim


def _esc(text: str) -> str:
    """Escape text for safe embedding in single-quoted Python string literals."""
    text = _sanitize_for_manim(text)
    # Collapse multiline YAML text into single line
    text = " ".join(text.split())
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    return text


def _hold_time(target_duration_s: float, animation_time: float) -> float:
    """Calculate wait time to hit target duration after animations."""
    hold = target_duration_s - animation_time
    return max(0.5, hold)


# ---------------------------------------------------------------------------
# 1. STAT REVEAL — Shocking numerical comparison
# ---------------------------------------------------------------------------

def stat_reveal(
    text: str,
    target_duration_s: float = 5.0,
    values: dict | None = None,
    emphasis: str | None = None,
) -> tuple[str, str]:
    """Large numbers that slam in. Red accent for dangerous values.

    Returns (code, class_name).
    """
    text = _esc(text)

    # Build value lines
    value_lines = []
    if values:
        for key, val in values.items():
            color = "RED" if key == emphasis else "WHITE"
            size = 72 if key == emphasis else 48
            val_esc = _esc(str(val))
            key_esc = _esc(str(key).replace("_", " ").title())
            value_lines.append(
                f"        v_{key} = Text('{val_esc}', font_size={size}, "
                f"color={color}, weight=BOLD)"
            )
            value_lines.append(
                f"        l_{key} = Text('{key_esc}', font_size=20, color=GREY_B)"
            )
            value_lines.append(
                f"        g_{key} = VGroup(l_{key}, v_{key}).arrange(DOWN, buff=0.15)"
            )

    if not value_lines:
        # Fallback: just show the text big
        value_lines = [
            f"        v_main = Text('{text}', font_size=56, color=RED, weight=BOLD)",
            f"        g_main = v_main",
        ]

    value_code = "\n".join(value_lines)

    # Build group arrangement
    if values and len(values) > 1:
        group_names = [f"g_{k}" for k in values.keys()]
        arrange_code = (
            f"        all_values = VGroup({', '.join(group_names)})"
            f".arrange(RIGHT, buff=1.5)\n"
            f"        all_values.move_to(DOWN * 0.3)"
        )
        anim_items = ", ".join(
            f"FadeIn({g}, shift=UP*0.3)" for g in group_names
        )
    elif values:
        k = list(values.keys())[0]
        arrange_code = f"        all_values = g_{k}\n        all_values.move_to(DOWN * 0.3)"
        anim_items = f"FadeIn(g_{k}, shift=UP*0.3)"
    else:
        arrange_code = "        all_values = g_main\n        all_values.move_to(ORIGIN)"
        anim_items = "FadeIn(g_main, shift=UP*0.3)"

    # Divider line between prescribed and delivered
    divider_code = ""
    if values and len(values) >= 2:
        divider_code = """
        div = Line(LEFT*0.5, RIGHT*0.5, color=RED, stroke_width=3)
        div.move_to(DOWN * 0.3)
        self.play(Create(div), run_time=0.3)
"""

    anim_time = 2.5
    hold = _hold_time(target_duration_s, anim_time)

    code = f"""from manim import *

class StatReveal(Scene):
    def construct(self):
        self.camera.background_color = '#0a0a1a'

{value_code}
{arrange_code}
{divider_code}
        self.play({anim_items}, run_time=1.2)
        self.wait({hold:.1f})
"""
    return code, "StatReveal"


# ---------------------------------------------------------------------------
# 2. CHAPTER TITLE — Act/section divider
# ---------------------------------------------------------------------------

def chapter_title(
    text: str,
    target_duration_s: float = 4.0,
    **kwargs,
) -> tuple[str, str]:
    """Centered text, slow fade in/out. Minimal and elegant."""
    text = _esc(text)
    hold = _hold_time(target_duration_s, 3.0)

    code = f"""from manim import *

class ChapterTitle(Scene):
    def construct(self):
        self.camera.background_color = '#000000'

        title = Text('{text}', font_size=56, color=WHITE, weight=BOLD)
        title.move_to(ORIGIN)

        # Thin decorative line above
        line = Line(LEFT * 1.5, RIGHT * 1.5, color='#444444', stroke_width=1)
        line.next_to(title, UP, buff=0.4)

        self.play(FadeIn(title, shift=UP * 0.2), run_time=1.2)
        self.play(Create(line), run_time=0.5)
        self.wait({hold:.1f})
        self.play(FadeOut(title), FadeOut(line), run_time=1.0)
"""
    return code, "ChapterTitle"


# ---------------------------------------------------------------------------
# 3. INSTITUTIONAL TEXT — Corporate/regulatory statements
# ---------------------------------------------------------------------------

def institutional_text(
    text: str,
    target_duration_s: float = 5.0,
    **kwargs,
) -> tuple[str, str]:
    """Typewriter-style text on off-white paper feel. Institutional weight."""
    text = _esc(text)
    hold = _hold_time(target_duration_s, 2.5)

    # Split text for typewriter animation (character by character look)
    code = f"""from manim import *

class InstitutionalText(Scene):
    def construct(self):
        self.camera.background_color = '#1a1a2e'

        # Paper-like rectangle background
        paper = RoundedRectangle(
            corner_radius=0.05, width=12, height=5,
            fill_color='#f0ede4', fill_opacity=0.08,
            stroke_color='#3a3a4e', stroke_width=1,
        )
        paper.move_to(ORIGIN)
        self.play(FadeIn(paper), run_time=0.4)

        # Institutional header bar
        header = Rectangle(width=12, height=0.06, fill_color='#1e3a5f',
                          fill_opacity=0.8, stroke_width=0)
        header.next_to(paper, UP, buff=-0.5)
        self.play(FadeIn(header), run_time=0.2)

        # Main text — monospaced feel
        body = Text('{text}', font_size=28, color='#c8c4bc',
                    line_spacing=1.4)
        body.move_to(ORIGIN)
        # Ensure text fits within paper
        if body.width > 10:
            body.scale(10 / body.width)

        self.play(FadeIn(body, shift=RIGHT * 0.1), run_time=1.2)
        self.wait({hold:.1f})
"""
    return code, "InstitutionalText"


# ---------------------------------------------------------------------------
# 4. TECHNICAL LABEL — Clean, minimal factual display
# ---------------------------------------------------------------------------

def technical_label(
    text: str,
    target_duration_s: float = 4.0,
    **kwargs,
) -> tuple[str, str]:
    """Clean sans-serif, small caps feel. Cut in, no fancy transition."""
    text = _esc(text)
    hold = _hold_time(target_duration_s, 1.0)

    code = f"""from manim import *

class TechnicalLabel(Scene):
    def construct(self):
        self.camera.background_color = '#0d0d14'

        # Accent line on left
        accent = Line(UP * 0.6, DOWN * 0.6, color='#3a7bd5', stroke_width=3)
        accent.move_to(LEFT * 5)

        label = Text('{text}', font_size=32, color='#e0e0e8')
        label.next_to(accent, RIGHT, buff=0.4)

        group = VGroup(accent, label)
        group.move_to(ORIGIN)

        self.play(
            Create(accent, run_time=0.3),
            FadeIn(label, shift=RIGHT * 0.15, run_time=0.5),
        )
        self.wait({hold:.1f})
"""
    return code, "TechnicalLabel"


# ---------------------------------------------------------------------------
# 5. ERROR MESSAGE — CRT terminal green-on-black
# ---------------------------------------------------------------------------

def error_message(
    text: str,
    target_duration_s: float = 5.0,
    terminal_text: str | None = None,
    subtext: str | None = None,
    **kwargs,
) -> tuple[str, str]:
    """Green monospace on black CRT. Blinking cursor. Scanline hint."""
    main_text = _esc(terminal_text or text)
    sub = _esc(subtext) if subtext else ""
    hold = _hold_time(target_duration_s, 3.0)

    sub_code = ""
    if sub:
        sub_code = f"""
        sub = Text('{sub}', font_size=22, color='#55aa55')
        sub.next_to(error_text, DOWN, buff=0.6)
        self.play(FadeIn(sub), run_time=0.6)
"""

    code = f"""from manim import *

class ErrorMessage(Scene):
    def construct(self):
        self.camera.background_color = '#020804'

        # CRT border frame
        frame = RoundedRectangle(
            corner_radius=0.3, width=13, height=7,
            stroke_color='#1a3a1a', stroke_width=2,
            fill_color='#030a04', fill_opacity=0.5,
        )
        frame.move_to(ORIGIN)
        self.add(frame)

        # Scanline overlay (subtle horizontal lines)
        for i in range(-15, 16):
            line = Line(LEFT * 6, RIGHT * 6, stroke_width=0.3,
                       color='#0a1a0a', stroke_opacity=0.4)
            line.move_to(UP * i * 0.22)
            self.add(line)

        # Prompt
        prompt = Text('> ', font_size=28, color='#33cc33')
        prompt.move_to(LEFT * 4 + UP * 1.5)
        self.play(FadeIn(prompt), run_time=0.2)

        # Error text — character by character feel
        error_text = Text('{main_text}', font_size=42, color='#44ff44',
                         weight=BOLD)
        error_text.move_to(UP * 0.3)
        self.play(FadeIn(error_text, lag_ratio=0.05), run_time=1.0)

        # Cursor blink
        cursor = Text('_', font_size=42, color='#44ff44')
        cursor.next_to(error_text, RIGHT, buff=0.1)
        self.play(FadeIn(cursor), run_time=0.1)
        self.play(FadeOut(cursor), run_time=0.3)
        self.play(FadeIn(cursor), run_time=0.1)
{sub_code}
        self.wait({hold:.1f})
"""
    return code, "ErrorMessage"


# ---------------------------------------------------------------------------
# 6. VERDICT — Final damning conclusions
# ---------------------------------------------------------------------------

def verdict(
    text: str,
    target_duration_s: float = 5.0,
    **kwargs,
) -> tuple[str, str]:
    """Bold items appearing one by one with weight. Red underlines for emphasis."""
    text = _esc(text)

    # Split text into lines for sequential reveal
    lines = [l.strip() for l in text.split(".") if l.strip()]
    if not lines:
        lines = [text]

    line_code_parts = []
    anim_parts = []
    for i, line in enumerate(lines[:4]):  # Max 4 items
        y_pos = 1.0 - i * 1.0
        line_esc = _esc(line)
        line_code_parts.append(
            f"        item_{i} = Text('{line_esc}', font_size=36, "
            f"color=WHITE, weight=BOLD)\n"
            f"        item_{i}.move_to(UP * {y_pos})"
        )
        # Red underline for emphasis
        line_code_parts.append(
            f"        ul_{i} = Line(item_{i}.get_left() + DOWN*0.15, "
            f"item_{i}.get_right() + DOWN*0.15, "
            f"color='#cc3333', stroke_width=2)"
        )
        anim_parts.append(
            f"        self.play(FadeIn(item_{i}, shift=UP*0.2), run_time=0.6)\n"
            f"        self.play(Create(ul_{i}), run_time=0.3)"
        )

    line_code = "\n".join(line_code_parts)
    anim_code = "\n".join(anim_parts)
    anim_time = len(lines[:4]) * 1.2
    hold = _hold_time(target_duration_s, anim_time)

    code = f"""from manim import *

class Verdict(Scene):
    def construct(self):
        self.camera.background_color = '#0a0408'

{line_code}

{anim_code}

        self.wait({hold:.1f})
"""
    return code, "Verdict"


# ---------------------------------------------------------------------------
# 7. QUOTE — Attributed quotation
# ---------------------------------------------------------------------------

def quote(
    text: str,
    target_duration_s: float = 5.0,
    **kwargs,
) -> tuple[str, str]:
    """Italic-feel serif text with attribution. Warm, contemplative."""
    text = _esc(text)

    # Try to split attribution if present (look for " - " or " -- ")
    if ' - ' in text:
        parts = text.rsplit(' - ', 1)
        quote_text = parts[0].strip().strip('"').strip("'")
        attribution = parts[1].strip()
    elif ' -- ' in text:
        parts = text.rsplit(' -- ', 1)
        quote_text = parts[0].strip().strip('"').strip("'")
        attribution = parts[1].strip()
    else:
        quote_text = text.strip('"').strip("'")
        attribution = ""

    quote_esc = _esc(quote_text)
    attr_esc = _esc(attribution)
    hold = _hold_time(target_duration_s, 2.8)

    attr_code = ""
    if attr_esc:
        attr_code = f"""
        attr = Text('- {attr_esc}', font_size=22, color='#b8a070')
        attr.next_to(body, DOWN, buff=0.6)
        attr.align_to(body, RIGHT)
        self.play(FadeIn(attr, shift=UP * 0.1), run_time=0.8)
"""

    code = f"""from manim import *

class Quote(Scene):
    def construct(self):
        self.camera.background_color = '#0a0a12'

        # Opening quotation mark — large, decorative
        qmark = Text(chr(8220), font_size=120, color='#3a3a50')
        qmark.move_to(LEFT * 4.5 + UP * 2)
        self.play(FadeIn(qmark), run_time=0.4)

        # Quote body
        body = Text('{quote_esc}', font_size=32, color='#e8e4dc',
                    line_spacing=1.5)
        body.move_to(ORIGIN)
        if body.width > 10:
            body.scale(10 / body.width)

        self.play(FadeIn(body, shift=UP * 0.15), run_time=1.2)
{attr_code}
        self.wait({hold:.1f})
"""
    return code, "Quote"


# ---------------------------------------------------------------------------
# Template registry — maps card_type → generator function
# ---------------------------------------------------------------------------

CARD_TEMPLATES = {
    "stat_reveal": stat_reveal,
    "chapter_title": chapter_title,
    "institutional_text": institutional_text,
    "technical_label": technical_label,
    "error_message": error_message,
    "verdict": verdict,
    "quote": quote,
}


def generate_card_code(
    card_type: str,
    text: str,
    target_duration_s: float = 5.0,
    **kwargs,
) -> tuple[str, str]:
    """Generate Manim code for a styled text card.

    Args:
        card_type: One of the 7 card types
        text: Display text
        target_duration_s: How long the card should display
        **kwargs: Type-specific params (values, emphasis, terminal_text, subtext)

    Returns:
        (manim_code, class_name) ready for _run_manim_scene()
    """
    if card_type not in CARD_TEMPLATES:
        raise ValueError(
            f"Unknown card_type '{card_type}'. "
            f"Must be one of: {', '.join(CARD_TEMPLATES.keys())}"
        )
    return CARD_TEMPLATES[card_type](text, target_duration_s, **kwargs)
