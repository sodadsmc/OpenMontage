"""Narration↔visual alignment gate — authoring-time validation.

The scored script schema *says* visual.description "Must match the narration
content", but nothing has ever enforced it — alignment was purely on the
authoring agent's honor. The observed failure modes:

  1. A multi-beat narration arc (error appears → operators ignore it →
     habituation sets in) covered by ONE static beat-prompt, so a 30s
     stretch of escalating narration sits over a clip that never evolves.
  2. Prompts that drift from the narration entirely (wrong subject,
     wrong location, wrong era) — the viewer hears one thing and sees
     another.

This module is a pre-spend hard gate: it sends every generative segment's
narration + prompts to Gemini and asks "does the visual DEPICT what the
narration describes?" BEFORE any video credits are burned. The channel is
AI-generation-first: every scene is generated to show the narrated subject
performing the narrated action, so the gate's default standard is literal
DEPICTION, not atmospheric agreement.

The bar is per-beat, keyed off each segment's ``narration_mode``:
  - ``literal`` (the default when unset): a "match" requires the visual to
    depict the narrated subject + action (narration "the operator presses P"
    -> hands at the keyboard pressing P, not "moody control room"). An
    atmospheric-only prompt that merely agrees on subject/era/mood is a
    "partial"/"mismatch" here, because the generator can and should show the
    action.
  - ``evocative``: the looser bar (atmospheric footage that agrees on subject,
    location, era, mood, and stakes is a match) — a valid PER-BEAT treatment,
    no longer the system-wide default.
  - ``none``: minimal alignment expectation (e.g. a pure mood/transition beat).

Fail-closed: an API or parse failure for a segment yields verdict "error",
which fails the gate — we never green-light spend on an unvalidated segment.

Usage:
    from lib.scored_script import load_scored_script
    from lib.narration_gate import validate_script_alignment, gate

    script = load_scored_script("projects/x/script_v5/scored_script.yaml")
    results = validate_script_alignment(script)
    if not gate(results):
        abort_before_spending()

CLI:
    python -m lib.narration_gate projects/x/script_v5/scored_script.yaml \
        [--report out.json]
    # exits 1 if any segment is a mismatch (or errored) — match/partial pass
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Visual types that route to paid generative video — the only ones worth
# gating (manim/text cards have their own validators and no prompt-drift
# failure mode). Only GENERATED types belong here: ``atmospheric_footage`` is
# RETRIEVED stock (judged by relevance, not this depiction gate) and is
# intentionally excluded; ``generated_footage`` is a legacy alias for ai_video
# kept for back-compat. Names match lib/script_validator.py and lib/asset_bible.py.
GENERATIVE_TYPES = frozenset({
    "ai_video",
    "generated_footage",  # legacy alias for ai_video
})

# Documentary narration pace used to estimate on-screen duration from word
# count. The duration matters to the rubric: a 30s narration arc demands
# visual evolution that a 6s one does not.
_WORDS_PER_SECOND = 2.5

# Severity ladder for gate(). "error" is the worst — fail-closed means an
# unvalidated segment can never pass a pre-spend gate.
_SEVERITY = {"match": 0, "partial": 1, "mismatch": 2, "error": 3}

# How many segments per Gemini call. One big call for 32+ segments risks
# truncated JSON (2.5-flash thinking tokens eat into max_output_tokens);
# one call per segment is slow. Small batches + per-segment retry on the
# stragglers is the reliable middle ground.
_BATCH_SIZE = 8

# The alignment rubric. Kept as a single verbatim block so reviewers can
# audit exactly what standard the gate applies.
ALIGNMENT_RUBRIC = """\
You are an alignment auditor for an AI-GENERATED documentary video pipeline.

Every visual here is generated from scratch (not retrieved stock), so the
visual CAN and SHOULD depict exactly what the narration describes. For each
segment you get the NARRATION (what the viewer hears), the VISUAL PROMPT(s)
(what the AI video model will generate for that exact stretch), and a MODE
that sets which standard to apply.

Apply the standard by MODE:
- MODE "literal" (the default): the visual must DEPICT the narrated subject
  performing the narrated action. Narration "the operator back-spaces and
  types P" -> hands at the keyboard doing exactly that. A prompt that only
  sets mood or shows the right place/era WITHOUT the narrated action/subject
  is NOT a match here — the generator could show the action and didn't.
  (Non-depictable characterization like "because she was experienced" informs
  HOW she moves; it is not itself a required on-screen element.)
- MODE "evocative": looser. Atmospheric/period imagery that agrees with the
  narration's subject, location, era, mood, and stakes counts as a match even
  if it does not literally show the action.
- MODE "none": minimal expectation — only a hard contradiction (wrong era,
  wrong subject entirely) is a problem.

Verdicts:
- "match": meets the bar for its MODE. For "literal", the narrated subject and
  action are clearly depicted; for "evocative", the imagery clearly agrees.
- "partial": compatible but falls short of the MODE's bar — e.g. a "literal"
  beat whose prompt sets the scene/mood but does not show the narrated action,
  or names the wrong subject; OR the narration moves through several distinct
  beats (something appears -> someone reacts -> a consequence) while one static
  prompt covers a single beat and cannot carry the arc for the duration.
- "mismatch": a viewer would notice the visual contradicts or ignores the
  words — wrong location, wrong subject, wrong action, wrong era; or a
  "literal" beat with no attempt to depict the narrated action.
- "score": 1-10 overall. 9-10 = clearly depicts/enacts the narrated moment;
  7-8 = depicts the subject + action with minor staging gaps; 5-6 = sets the
  scene but the key action is unclear or missing (a "literal" beat cannot be a
  "match" at this tier); 1-4 = a viewer would be confused or pulled out.
- "missing_elements": concrete things the narration establishes that the
  visual fails to depict and a viewer would expect to SEE happen.
- "extraneous_elements": things in the prompt that contradict the narration
  or introduce off-script content a viewer would question.
- "suggested_prompt": only when verdict is "partial" or "mismatch" — a revised
  prompt that preserves the segment's visual approach but DEPICTS the narrated
  subject + action; otherwise an empty string.
- "narration_beats": the number of distinct narrative beats in the NARRATION
  alone (ignore the visual). A beat is a distinct event or state change a
  viewer would expect to register — e.g. "an error appears" -> "operators
  ignore it" -> "ignoring becomes habit" is 3 beats. A single sustained
  description or mood is 1 beat.

Return ONLY a JSON array, one object per segment, in the same order:
[{"segment_id": "...", "verdict": "match"|"partial"|"mismatch",
  "score": 1-10, "narration_beats": N, "missing_elements": [...],
  "extraneous_elements": [...], "suggested_prompt": "..."}]
"""

# Code-enforced arc rule: an LLM judge applies content rules well but
# duration rules unreliably (it tends to accept ambient motion — flicker,
# scrolling, a drifting camera — as "progression" inside one fixed tableau).
# So the model only COUNTS narration beats; Python enforces the policy: a
# segment this long, with this many beats, covered by a single shot prompt,
# cannot be better than "partial". This is exactly the observed real-world
# failure (a 31s habituation arc filled by one static beat-prompt).
_ARC_CAP_MIN_S = 20.0
_ARC_CAP_MIN_BEATS = 2


# ---------------------------------------------------------------------------
# Gemini plumbing
# ---------------------------------------------------------------------------

def _make_model(model: str):
    """Build the Gemini model handle (same pattern as lib/quality_gate.py).

    Raises on missing dependency/key — callers convert that into "error"
    verdicts per segment (fail-closed) rather than silently passing.
    """
    import google.generativeai as genai  # deferred: keep import-time light

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — cannot validate alignment")

    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model,
        generation_config=genai.types.GenerationConfig(
            temperature=0.2,
            max_output_tokens=16384,
            response_mime_type="application/json",
        ),
    )


def _parse_json_response(text: str) -> Any:
    """Parse a Gemini JSON response, tolerating markdown code fences."""
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Segment formatting
# ---------------------------------------------------------------------------

def _segment_block(seg: Any) -> str:
    """Render one segment as a prompt block for the auditor.

    Includes everything the video model will actually see: the effective
    prompt, per-shot prompts when present (shots are what gets generated
    for ai_video), and motion direction — plus an estimated duration so
    the rubric can judge whether a single beat can carry the arc.
    """
    vis = seg.visual
    est_s = seg.word_count / _WORDS_PER_SECOND
    # Depiction is the default standard; a beat opts into the looser bar by
    # setting narration_mode to "evocative" (or "none"). Unset -> "literal".
    mode = (getattr(seg, "narration_mode", None) or "literal").lower()
    lines = [
        f"segment_id: {seg.id}",
        f"MODE: {mode}",
        f"estimated_on_screen_duration: {est_s:.0f}s",
        f'NARRATION: "{seg.narration}"',
        f'VISUAL PROMPT: "{vis.effective_prompt}"',
    ]
    if vis.ai_motion:
        lines.append(f'MOTION DIRECTION: "{vis.ai_motion}"')
    for shot in vis.shots:
        shot_line = f'SHOT {shot.shot_id} PROMPT: "{shot.ai_prompt}"'
        if shot.ai_motion:
            shot_line += f' (motion: "{shot.ai_motion}")'
        lines.append(shot_line)
    return "\n".join(lines)


def _error_result(segment_id: str, why: str) -> dict:
    """Fail-closed placeholder for a segment we could not validate."""
    return {
        "segment_id": segment_id,
        "verdict": "error",
        "score": 0,
        "missing_elements": [],
        "extraneous_elements": [],
        "suggested_prompt": "",
        "error": why[:200],
    }


def _normalize_result(segment_id: str, raw: dict) -> dict:
    """Coerce a model-produced result into the gate's contract.

    The model occasionally drops keys or returns odd types; normalizing here
    means downstream consumers (gate(), CLI table, report JSON) never have
    to defend against malformed entries.
    """
    verdict = str(raw.get("verdict", "")).lower().strip()
    if verdict not in ("match", "partial", "mismatch"):
        return _error_result(segment_id, f"unrecognized verdict {verdict!r}")
    try:
        score = max(1, min(10, int(raw.get("score", 0))))
    except (TypeError, ValueError):
        score = 1
    try:
        beats = max(1, int(raw.get("narration_beats", 1)))
    except (TypeError, ValueError):
        beats = 1
    return {
        "segment_id": segment_id,
        "verdict": verdict,
        "score": score,
        "narration_beats": beats,
        "missing_elements": [str(x) for x in raw.get("missing_elements") or []],
        "extraneous_elements": [str(x) for x in raw.get("extraneous_elements") or []],
        "suggested_prompt": str(raw.get("suggested_prompt") or ""),
    }


def _apply_arc_cap(result: dict, seg: Any) -> dict:
    """Downgrade "match" to "partial" for long multi-beat single-shot segments.

    The LLM counts beats; this cap is the deterministic policy on top.
    "partial" does not fail the default gate (fail_on="mismatch") — the point
    is to SURFACE these segments so they get beat-split legs or extra shots,
    not to block production on them.
    """
    if result["verdict"] != "match":
        return result
    est_s = seg.word_count / _WORDS_PER_SECOND
    n_shots = len(seg.visual.shots)
    if (
        est_s >= _ARC_CAP_MIN_S
        and result.get("narration_beats", 1) >= _ARC_CAP_MIN_BEATS
        and n_shots <= 1
    ):
        result = dict(result)
        result["verdict"] = "partial"
        result["score"] = min(result["score"], 6)
        result["missing_elements"] = result["missing_elements"] + [
            f"narration spans {result['narration_beats']} beats over "
            f"~{est_s:.0f}s but a single shot prompt covers one beat — "
            f"needs beat-split legs or additional shots to carry the arc"
        ]
        result["arc_capped"] = True
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_script_alignment(
    script: Any,
    model: str = "gemini-2.5-flash",
) -> list[dict]:
    """Audit narration↔visual alignment for every generative segment.

    Args:
        script: A loaded ScoredScript (lib.scored_script.load_scored_script).
        model: Gemini model name (repo standard: gemini-2.5-flash).

    Returns:
        One dict per generative segment, in script order:
        {segment_id, verdict: "match"|"partial"|"mismatch"|"error",
         score: 1-10, missing_elements, extraneous_elements, suggested_prompt}

    Segments are batched per Gemini call for cost/latency; any segment the
    batch fails to cover is retried individually, and a segment that still
    cannot be validated gets verdict "error" — fail-closed, because this
    gate exists to stop spend on unverified prompts.
    """
    targets = [s for s in script.segments if s.visual.type in GENERATIVE_TYPES]
    if not targets:
        _log.info("No generative segments to validate")
        return []

    try:
        gem = _make_model(model)
    except Exception as exc:
        _log.error("Cannot initialize Gemini: %s", exc)
        return [_error_result(s.id, str(exc)) for s in targets]

    results: dict[str, dict] = {}

    for start in range(0, len(targets), _BATCH_SIZE):
        batch = targets[start:start + _BATCH_SIZE]
        results.update(_validate_batch(gem, batch))

    # Per-segment retry for anything the batch missed or errored — a batch
    # response can drop a segment without the whole call failing.
    for seg in targets:
        if results.get(seg.id, {}).get("verdict") == "error" or seg.id not in results:
            _log.info("Retrying %s individually", seg.id)
            results.update(_validate_batch(gem, [seg]))

    return [
        _apply_arc_cap(
            results.get(seg.id) or _error_result(seg.id, "no result returned"),
            seg,
        )
        for seg in targets
    ]


def _validate_batch(gem: Any, batch: list[Any]) -> dict[str, dict]:
    """Run one Gemini call over a batch of segments; fail-closed per segment."""
    blocks = "\n\n---\n\n".join(_segment_block(s) for s in batch)
    prompt = f"{ALIGNMENT_RUBRIC}\n\nSEGMENTS ({len(batch)}):\n\n{blocks}"

    try:
        response = gem.generate_content(prompt)
        parsed = _parse_json_response(response.text)
        if isinstance(parsed, dict):  # single-segment calls may return an object
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    except Exception as exc:
        _log.warning("Batch of %d failed: %s", len(batch), str(exc)[:200])
        return {s.id: _error_result(s.id, str(exc)) for s in batch}

    by_id = {
        str(item.get("segment_id", "")): item
        for item in parsed if isinstance(item, dict)
    }
    out: dict[str, dict] = {}
    for seg in batch:
        if seg.id in by_id:
            out[seg.id] = _normalize_result(seg.id, by_id[seg.id])
        else:
            out[seg.id] = _error_result(seg.id, "segment missing from model response")
    return out


def gate(results: list[dict], fail_on: str = "mismatch") -> bool:
    """Decide pass/fail from alignment results.

    Args:
        results: Output of validate_script_alignment().
        fail_on: Minimum verdict severity that fails the gate —
            "partial" (strict), "mismatch" (default), or "error".
            "error" verdicts always fail regardless (fail-closed: we never
            approve spend on a segment we could not validate).

    Returns:
        True if the script passes (no verdict at or above the threshold).
    """
    if fail_on not in _SEVERITY:
        raise ValueError(f"fail_on must be one of {sorted(_SEVERITY)}, got {fail_on!r}")
    threshold = _SEVERITY[fail_on]
    return not any(
        _SEVERITY.get(r.get("verdict", "error"), _SEVERITY["error"]) >= threshold
        for r in results
    )


# ---------------------------------------------------------------------------
# CLI — the pre-spend hard gate
# ---------------------------------------------------------------------------

def _print_table(results: list[dict]) -> None:
    """Readable verdict table, worst offenders first (severity desc, score asc)."""
    ordered = sorted(
        results,
        key=lambda r: (-_SEVERITY.get(r.get("verdict", "error"), 3), r.get("score", 0)),
    )
    print(f"{'SEGMENT':<10} {'VERDICT':<10} {'SCORE':>5}  WHY")
    print("-" * 100)
    for r in ordered:
        why_parts = []
        if r.get("error"):
            why_parts.append(f"error: {r['error']}")
        if r.get("missing_elements"):
            why_parts.append("missing: " + "; ".join(r["missing_elements"][:3]))
        if r.get("extraneous_elements"):
            why_parts.append("extraneous: " + "; ".join(r["extraneous_elements"][:3]))
        why = " | ".join(why_parts) or "-"
        if len(why) > 160:
            why = why[:157] + "..."
        print(f"{r['segment_id']:<10} {r['verdict']:<10} {r.get('score', 0):>5}  {why}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m lib.narration_gate",
        description="Pre-spend narration<->visual alignment gate for scored scripts.",
    )
    parser.add_argument("script", help="Path to scored_script.yaml")
    parser.add_argument("--report", help="Write full JSON results to this path")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument(
        "--fail-on", default="mismatch", choices=["partial", "mismatch", "error"],
        help="Verdict severity that fails the gate (default: mismatch)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Windows consoles default to cp1252 — narration text contains em dashes
    # and other punctuation that must not crash the gate's own output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # The CLI must work standalone (it is the Phase A pre-spend gate):
    # importing the tool contract loads .env (GOOGLE_API_KEY) as a side
    # effect, same as every other repo entry point.
    try:
        import tools.base_tool  # noqa: F401
    except ImportError:
        pass  # running outside the repo — rely on the ambient environment

    from lib.scored_script import load_scored_script

    script = load_scored_script(args.script)
    results = validate_script_alignment(script, model=args.model)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print(f"\nNarration alignment — {script.title} "
          f"({len(results)} generative segments)")
    print("Verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")
    _print_table(results)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {"script": str(args.script), "model": args.model,
                 "counts": counts, "results": results},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nReport written: {report_path}")

    passed = gate(results, fail_on=args.fail_on)
    print(f"\nGATE: {'PASSED' if passed else 'FAILED'} (fail_on={args.fail_on})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
