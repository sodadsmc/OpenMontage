"""Generative hand-sketched diagrams: a Coder->Critic loop over sketch_diagrams.

WHY this exists: lib.sketch_diagrams gives the channel its "page torn out of the
graphic novel" diagram look, but every scene there is hand-coded — the pipeline
cannot mint a diagram for a NEW segment without an engineer writing matplotlib.
This module closes that gap: Gemini writes ONLY the scene body (a
`draw(ax, t, dur)` callback) against a frozen helper contract, the result is
rendered through the exact same render_template pipeline as the hand-coded
scenes (same wobble, palette, background, finishing), and the rendered mp4 is
judged by the Gemini critic in lib.manim_validator. Critic failures (and
compile/runtime errors) are fed back to the coder for a corrected version, up
to max_attempts.

The model never touches the render harness, the palette, or ffmpeg — it can
only compose the same helpers the hand-coded scenes use, which is what keeps
generated scenes on-style by construction rather than by prompt-begging.

Safety note: the denylist scan below is tooling hygiene (catch the model
reaching for os/subprocess/network by accident), NOT a security boundary —
this runs locally on operator briefs only.

Public API:
  generate_diagram_scene(brief, duration_s, out_path, narration="",
                         max_attempts=3) -> Path | None
"""
from __future__ import annotations

import inspect
import logging
import os
import re
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from lib import sketch_diagrams as sd
from lib.manim_validator import SKETCH_VALIDATION_PROMPT, validate_animation

_log = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# HELPER CONTRACT — the documented surface generated code may use.
# This is the single source of truth the coder prompt is built from; it
# describes lib.sketch_diagrams as an API so the model composes existing
# helpers instead of inventing off-palette matplotlib.
# ---------------------------------------------------------------------------
HELPER_CONTRACT = """\
You write ONE Python function for a hand-sketched animated diagram that renders
inside an existing matplotlib pipeline (xkcd sketch mode + handwriting font +
navy/amber graphic-novel palette). You do NOT control the figure, style, or
encoding — only the function body.

SIGNATURE (exactly this, nothing else at module level except an optional
helper function or two that `draw` calls):

    def draw(ax, t, dur):

It is called once per frame with the SAME axes, t = seconds elapsed in
[0, dur], dur = total scene seconds. It must be a pure function of t: draw the
complete frame for time t every call (no state between calls).

CANVAS:
- ax spans x in [0, 10], y in [0, 10], origin BOTTOM-LEFT, axis hidden.
- The frame is 16:9 (1920x1080): 1 x-unit ~ 192 px, 1 y-unit ~ 108 px.
  Units are NOT square — a "circle" of equal radii renders ~1.8x wider than
  tall. Size widths and heights independently (the helpers already do).
- A dark indigo gradient + halftone background is painted before draw() runs.
- MARGINS: keep settled content inside x in [0.7, 9.3], y in [0.8, 9.4].
  Title at y ~ 9.25, optional subtitle at y ~ 8.45, body in y in [1.5, 7.8],
  bottom captions y in [0.9, 1.7]. Nothing may clip at the frame edges.

PALETTE (constants available by name — use ONLY these):
- NAVY      "#0a1428"  background ink (dark)
- NAVY2     "#13233d"  box fill
- AMBER     "#e8a44c"  primary ink: titles, boxes, arrows
- AMBER_HOT "#f6c06a"  emphasis / danger / the dramatic element
- AMBER_D   "#a8742c"  de-emphasized amber (tracks, ghosts)
- CREAM     "#e6dcc6"  body text
- MUTE      "#8a93a3"  footnotes / de-emphasized labels

HELPERS (available by name — prefer these over raw matplotlib):
- text(ax, x, y, s, size, color=CREAM, alpha=1.0, ha="center", va="center",
       stroke=0.0)
  Hand-written text. stroke>0 adds a navy outline — use stroke 1.2-2.0 on big
  display text (titles, huge numbers) only; leave 0 for body text.
- box(ax, cx, cy, w, h, edge=AMBER, fill=NAVY2, fill_alpha=0.55, lw=3,
      alpha=1.0)
  Rounded hand-drawn box centred at (cx, cy).
- arrow(ax, x0, y0, x1, y1, color=AMBER, lw=3, alpha=1.0)
- flash(ax, cx, cy, t, t0, r0=0.9, r1=1.7, n=12, color=AMBER_HOT, d=0.6)
  Radiating impact burst around (cx, cy) shortly after t0.
- reveal(t, t0, d=0.5) -> 0..1   smoothstep fade-in starting at t0 over d sec
- pulse(t, t0, d=0.6) -> 0..1..0 hump just after t0 (size/flash pops)
- clamp(x, lo=0.0, hi=1.0)
- footer(ax, t, src="Source: ...") draws a small source citation at the very
  bottom. Call it ONLY if the brief supplies a real source; otherwise omit.
Raw matplotlib on ax is allowed for what helpers can't do (ax.plot lines,
ax.scatter glows, ax.fill_between): zorder conventions — tracks/underlays 1,
boxes 3, arrows 4, text 5, flashes/strikethroughs 6.
`np` (numpy) and `math` are in scope. NOTHING ELSE: no imports (except
`import math` if you like), no plt, no file/network access, no np.random
(randomness breaks the deterministic ink-boil — use sin/cos of t for flicker).

TIMING IDIOM (this is how every scene is animated):
- Everything fades in with `a = reveal(t, t0, 0.6)` passed as alpha=a.
- Stage the scene in 2-4 phases with explicit time windows scaled to `dur`
  (e.g. for dur=12: build 0-4, develop 4-8, payoff 8-12). Gate phases with
  `if t < X: ... elif ...` and overlap the reveal t0s slightly so no frame
  is ever empty between phases.
- Animate values/sizes/positions directly from reveal()/pulse() outputs
  (a bar grows: h = H_MAX * reveal(t, 5.0, 2.5)).
- Use pulse() pops on number changes and flash() on the dramatic beat.
- The final phase must land by ~dur-1.5s and HOLD so the last frames show the
  finished diagram. No stretch longer than ~2.5s where nothing changes.
- Sustained flicker for danger elements: 0.5 + 0.5*np.sin(t * 9.0).

TEXT SIZES (at 1080p): title 40-48, section labels 26-32, body 22-26,
footnotes 18-20. NEVER below 16. Width budget: at size 24 one character is
~0.09 x-units wide — a 60-char line centred at x=5 spans ~5.4 units. Keep
lines short, break long thoughts into stacked lines ~0.6-0.8 y-units apart.
Huge hero numbers (size 100-165) are very effective for one focal value.
"""

# Few-shot scene sources are pulled live from sketch_diagrams so the examples
# can never drift out of sync with the real helper behavior. Each entry maps a
# scene to the module-level helpers its source calls — those must be shown too,
# or the model imitates names that don't exist in its exec namespace.
_FEW_SHOT_SCENES: dict[str, tuple[str, ...]] = {
    "draw_byte_overflow": ("_bits",),
    "draw_beam_fires": (),
}

# Imports the generated source may declare. np/math are injected anyway; numpy
# is allowed because models habitually re-import what they're told is in scope.
_ALLOWED_IMPORTS = {"math", "numpy"}

# Substring/regex denylist — local-tooling hygiene, not a sandbox.
_DENYLIST = (
    r"\bimport\s+os\b", r"\bimport\s+sys\b", r"\bsubprocess\b",
    r"\bopen\s*\(", r"\brequests\b", r"\b__import__\b", r"\beval\s*\(",
    r"\bexec\s*\(", r"\bsocket\b", r"\burllib\b", r"\bshutil\b",
    r"\bpathlib\b", r"\bplt\.", r"\bmatplotlib\b", r"np\.random",
)


def _build_system_prompt() -> str:
    """Assemble contract + few-shot examples into the coder system prompt."""
    shots = []
    for name, deps in _FEW_SHOT_SCENES.items():
        src = "".join(inspect.getsource(getattr(sd, d)) + "\n" for d in deps)
        src += inspect.getsource(getattr(sd, name))
        shots.append(f"EXAMPLE (a real production scene — match this idiom):\n"
                     f"```python\n{src}```")
    return (
        HELPER_CONTRACT
        + "\n\n" + "\n\n".join(shots)
        + "\n\nOUTPUT FORMAT: respond with ONLY the Python source for "
          "`def draw(ax, t, dur):` (plus any small module-level helper it "
          "needs). No markdown fences, no commentary, no imports beyond "
          "`import math`."
    )


def _extract_code(reply: str) -> str:
    """Strip markdown fences if the model ignored the no-fences instruction."""
    txt = reply.strip()
    m = re.search(r"```(?:python)?\s*\n(.*?)```", txt, re.DOTALL)
    return m.group(1).strip() if m else txt


def _scan_source(src: str) -> list[str]:
    """Return denylist/structure violations in generated source (empty = ok)."""
    problems = [f"forbidden pattern: {pat}"
                for pat in _DENYLIST if re.search(pat, src)]
    for mod in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src,
                          re.MULTILINE):
        if mod.split(".")[0] not in _ALLOWED_IMPORTS:
            problems.append(f"forbidden import: {mod}")
    if not re.search(r"^def draw\(ax,\s*t,\s*dur\):", src, re.MULTILINE):
        problems.append("missing exact signature `def draw(ax, t, dur):`")
    return problems


def _compile_draw(src: str) -> Callable:
    """Compile generated source and return its draw() callable.

    The exec namespace exposes exactly the contract surface (helpers, palette,
    np/math) so generated code runs against the same names the prompt
    documents — anything off-contract raises NameError here, which is cheap
    feedback for the retry loop.
    """
    import math
    ns: dict[str, Any] = {
        "np": np, "math": math,
        "clamp": sd.clamp, "reveal": sd.reveal, "pulse": sd.pulse,
        "text": sd.text, "box": sd.box, "arrow": sd.arrow,
        "flash": sd.flash, "footer": sd.footer,
        "NAVY": sd.NAVY, "NAVY2": sd.NAVY2, "AMBER": sd.AMBER,
        "AMBER_HOT": sd.AMBER_HOT, "AMBER_D": sd.AMBER_D,
        "CREAM": sd.CREAM, "MUTE": sd.MUTE,
    }
    code = compile(src, "<generated_scene>", "exec")
    exec(code, ns)  # noqa: S102 — local tooling, source already denylist-scanned
    draw = ns.get("draw")
    if not callable(draw):
        raise ValueError("generated source did not define a callable `draw`")
    return draw


def _preview(draw: Callable, duration_s: float) -> str | None:
    """Cheap 640x360 smoke-render at a handful of t samples.

    Catches runtime errors (NameError, bad color strings, math domain errors
    in late phases) BEFORE paying for the ~180-frame full render. savefig is
    required — matplotlib only realizes many artist errors at raster time.
    Returns a traceback string on failure, None when clean.
    """
    ts = [0.0, duration_s * 0.25, duration_s * 0.5, duration_s * 0.75,
          max(0.0, duration_s - 0.05)]
    try:
        with sd.sketch_style():
            fig = plt.figure(figsize=(6.4, 3.6), dpi=100)
            with tempfile.TemporaryDirectory() as td:
                for i, t in enumerate(ts):
                    np.random.seed(1000 + i)
                    fig.clf()
                    fig.patch.set_facecolor(sd.NAVY)
                    ax = sd._new_ax(fig)
                    sd.paint_background(ax)
                    draw(ax, t, duration_s)
                    fig.savefig(Path(td) / f"p{i}.png", facecolor=sd.NAVY)
            plt.close(fig)
        return None
    except Exception:  # noqa: BLE001 — the traceback IS the feedback payload
        plt.close("all")
        return traceback.format_exc(limit=6)


def _call_coder(brief: str, narration: str, duration_s: float,
                feedback: str | None, prev_source: str | None) -> str:
    """One Gemini codegen call; returns extracted Python source (may be bad)."""
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(
        _MODEL,
        system_instruction=_build_system_prompt(),
        generation_config=genai.types.GenerationConfig(
            temperature=0.5,
            max_output_tokens=8192,
        ),
    )
    task = (f"Write the scene for this brief. Duration: the scene fills "
            f"EXACTLY dur={duration_s:.1f} seconds.\n\nBRIEF: {brief}")
    if narration:
        task += (f"\n\nNARRATION over this diagram (pace the visual beats to "
                 f"it, do NOT write it on screen verbatim):\n{narration}")
    if feedback and prev_source:
        task += (
            "\n\nYour previous version FAILED review. Fix every issue and "
            "return the complete corrected source.\n\nPREVIOUS SOURCE:\n"
            f"```python\n{prev_source}\n```\n\nISSUES TO FIX:\n{feedback}"
        )
    resp = model.generate_content(task)
    return _extract_code(resp.text)


def _critique(video_path: str, brief: str, duration_s: float) -> dict[str, Any]:
    """Run the sketch-rubric critic, with the brief as grounding context."""
    context = (f"The diagram was generated from this brief: {brief!r} "
               f"(target duration {duration_s:.1f}s).\n\n")
    return validate_animation(video_path,
                              prompt=context + SKETCH_VALIDATION_PROMPT)


def _feedback_from(verdict: dict[str, Any]) -> str:
    """Flatten a critic verdict into coder-readable fix instructions."""
    lines = [f"- [{i.get('severity', '?')}] {i.get('type', '?')}: "
             f"{i.get('description', '')}" for i in verdict.get("issues", [])]
    lines += [f"- suggestion: {s}" for s in verdict.get("suggestions", [])]
    return "\n".join(lines) or "- reviewer failed it without details; tighten layout and pacing"


def generate_diagram_scene(brief: str, duration_s: float, out_path: str | Path,
                           narration: str = "", max_attempts: int = 3,
                           finish: bool = True, scene_name: str | None = None,
                           attempts_log: list | None = None) -> Path | None:
    """Generate, render, and critique a new hand-sketched diagram scene.

    Loop per attempt: Gemini writes draw(ax, t, dur) source -> denylist scan ->
    compile -> low-res preview (cheap runtime-error catch) -> full 1080p30
    render via sketch_diagrams.render_template -> Gemini critic on the mp4.
    Any failure becomes structured feedback for the next attempt.

    Returns the rendered mp4 Path on critic pass. If every attempt fails the
    critic but a render exists, the LAST render is still returned (with a
    warning) — downstream quality gates decide, and a slightly-flawed diagram
    beats a hole in the timeline. Returns None only when no video was produced.

    `scene_name`: when given, the winning draw() is registered in
    sketch_diagrams.SCENES under that name so it renders like any hand-coded
    scene from then on. `attempts_log`: optional list; one dict per attempt is
    appended (stage reached, errors, critic verdict) for reporting/tests.
    """
    out_path = Path(out_path)
    feedback: str | None = None
    prev_source: str | None = None
    last_render: Path | None = None
    last_draw: Callable | None = None

    for attempt in range(1, max_attempts + 1):
        entry: dict[str, Any] = {"attempt": attempt}
        if attempts_log is not None:
            attempts_log.append(entry)
        _log.info("codegen attempt %d/%d for %r", attempt, max_attempts,
                  brief[:60])

        try:
            src = _call_coder(brief, narration, duration_s, feedback,
                              prev_source)
        except Exception as exc:  # noqa: BLE001 — API hiccup: retry is the point
            _log.warning("coder call failed: %s", exc)
            entry["stage"] = "coder_call"
            entry["error"] = str(exc)[:200]
            time.sleep(2)
            continue
        prev_source = src
        entry["source_lines"] = src.count("\n") + 1

        violations = _scan_source(src)
        if violations:
            _log.warning("denylist/structure violations: %s", violations)
            entry["stage"] = "source_scan"
            entry["error"] = "; ".join(violations)
            feedback = ("Your source violated hard constraints:\n"
                        + "\n".join(f"- {v}" for v in violations))
            continue

        try:
            draw = _compile_draw(src)
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc(limit=4)
            _log.warning("generated source failed to compile/exec:\n%s", tb)
            entry["stage"] = "compile"
            entry["error"] = tb[-400:]
            feedback = f"Your source failed to compile/exec:\n{tb}"
            continue

        err = _preview(draw, duration_s)
        if err:
            _log.warning("low-res preview crashed:\n%s", err)
            entry["stage"] = "preview"
            entry["error"] = err[-400:]
            feedback = f"Your draw() crashed during rendering:\n{err}"
            continue

        t0 = time.time()
        rendered = sd.render_template(draw, duration_s, out_path,
                                      finish=finish)
        entry["render_s"] = round(time.time() - t0, 1)
        if not rendered or not Path(rendered).exists():
            entry["stage"] = "render"
            entry["error"] = "render_template returned no file"
            feedback = "The full render failed to produce a file; simplify the scene."
            continue
        last_render, last_draw = Path(rendered), draw

        verdict = _critique(rendered, brief, duration_s)
        entry["stage"] = "critic"
        entry["verdict"] = verdict
        if verdict.get("passed"):
            _log.info("critic PASSED on attempt %d", attempt)
            if scene_name:
                sd.register_scene(scene_name, draw)
            return Path(rendered)

        feedback = _feedback_from(verdict)
        _log.info("critic FAILED attempt %d:\n%s", attempt, feedback)

    if last_render is not None:
        _log.warning("critic never passed in %d attempts — returning last "
                     "render %s anyway (downstream gate decides)",
                     max_attempts, last_render)
        if scene_name and last_draw is not None:
            sd.register_scene(scene_name, last_draw)
        return last_render
    _log.error("no usable render produced in %d attempts", max_attempts)
    return None
