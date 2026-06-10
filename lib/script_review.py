"""Adversarial script-review panel — fact vetting, style review, flow review.

The pipeline already has *alignment* gates (lib/narration_gate.py checks that
visuals match narration). What nothing checks is whether the narration itself
is (a) factually defensible and (b) written like a story instead of a court
exhibit. The observed failure mode driving this module: scripts that read
like evidence — segment after segment opening with date/location slates
("June third, nineteen eighty-five. Marietta, Georgia. ...") — which an
affirming LLM reviewer happily scores 9/10 because every fact is correct.

So this panel is deliberately ADVERSARIAL. Each reviewer is instructed that
its job is to find what's wrong, and that finding nothing is a failed review
unless the work is genuinely clean. LLM-as-judge research and our own runs
agree: a judge told to refute is far more discriminating than one asked to
grade.

Three reviewers, all reusable across episodes (no topic hardcoding — the
research brief is passed in as the evidence base):

  1. fact_vetting()  — per segment, extract checkable claims and try to
     REFUTE each one against the research brief (primary) and the model's
     own knowledge of the documented record (secondary).
  2. style_review()  — per segment, score against the channel's narrative
     style rubric (STYLE_RUBRIC below, kept verbatim so it is auditable)
     and produce concrete fixes that preserve the facts.
  3. flow_review()   — one whole-script pass: hook, act structure,
     transitions, cross-segment repetition (including a deterministic
     Python cross-check for date/location slate openers — an adversarial
     model can still under-count, code cannot), dead spots, ending.

Fail-closed: an API or parse failure for a segment yields an "error" result
that the CLI treats like a critical finding — a script is never declared
clean on the strength of reviews that did not run.

Usage:
    from lib.script_review import run_panel

    report = run_panel(
        "projects/x/script_v5/scored_script.yaml",
        report_path="projects/x/artifacts/script_review_report.json",
    )

CLI:
    python -m lib.script_review projects/x/script_v5/scored_script.yaml \
        [--report out.json] [--panel fact,style,flow]
    # exits 1 if any critical fact issue (or any segment errored — fail-closed)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"

# How many segments per Gemini call. Same reasoning as lib/narration_gate.py:
# one big call risks truncated JSON (2.5-flash thinking tokens eat into
# max_output_tokens), one call per segment is slow. Fact batches are smaller
# because each segment fans out into several claim objects.
_FACT_BATCH_SIZE = 6
_STYLE_BATCH_SIZE = 8

# Severity ladder shared by the panel. "error" is worst — fail-closed means
# an unreviewed segment can never let a script pass.
_SEVERITY = {"info": 0, "warn": 1, "critical": 2, "error": 3}

# Cap on serialized brief evidence injected into fact prompts. The brief is
# passed verbatim (not summarized) so the reviewer cites the same artifacts a
# human checker would — but a runaway brief must not starve the output budget.
_EVIDENCE_CHAR_CAP = 14000

# Brief sections that constitute checkable evidence. Anything else in the
# brief (landscape analysis, audience insights) is strategy, not evidence,
# and would only dilute the fact reviewer's context.
_EVIDENCE_KEYS: tuple[str, ...] = (
    "topic",
    "primary_source",
    "timeline_events",
    "data_points",
    "stakeholders",
    "unverifiable_claims",
)

_FACT_VERDICTS = frozenset({
    "supported",
    "unsupported_by_brief",
    "overstated",
    "contradicts_known_facts",
})

_CLAIM_SEVERITIES = frozenset({"info", "warn", "critical"})

STYLE_VIOLATION_TYPES = frozenset({
    "evidence_reading",
    "data_dump",
    "repetitive_form",
    "tell_dont_show",
    "passive_recitation",
    "weak_transition",
})

_SHOT_VERDICTS = frozenset({"stages", "illustrates"})

# ---------------------------------------------------------------------------
# Rubrics — kept as verbatim module constants so reviewers can audit exactly
# what standard the panel applies (same convention as ALIGNMENT_RUBRIC in
# lib/narration_gate.py).
# ---------------------------------------------------------------------------

# Shared adversarial system framing. Every reviewer gets this prepended —
# the panel's whole value is that it attacks instead of affirms.
ADVERSARIAL_FRAMING = """\
You are an adversarial reviewer on a script-review panel. Your job is to find
what's wrong; finding nothing is a failed review unless the work is genuinely
clean. Do not be polite. Do not affirm. Do not grade on a curve. Actively try
to refute, attack, and break the material in front of you, and only conclude
it is clean after you have genuinely tried to tear it down and failed.
"""

FACT_RUBRIC = """\
You are the FACT VETTING reviewer for a documentary narration script.

For each segment you get the NARRATION the viewer will hear. Extract every
checkable factual claim in it (dates, names, places, numbers, doses, causal
statements, "first/only/most" superlatives, attributions). Then TRY TO REFUTE
each claim using, in priority order:
  1. the RESEARCH BRIEF evidence provided (primary evidence), and
  2. your own knowledge of the documented historical record (secondary).

Verdicts per claim:
- "supported": the brief or the well-documented record backs the claim as
  stated. You tried to refute it and failed.
- "unsupported_by_brief": nothing in the brief covers it and you cannot
  confirm it from the documented record — the claim rests on nothing.
- "overstated": directionally true but the narration inflates, rounds,
  sharpens, or dramatizes beyond what the evidence says (a range stated as
  its maximum, "always"/"never" where evidence says "usually", an editorial
  characterization presented as established fact).
- "contradicts_known_facts": the brief or the documented record says
  otherwise.

Severity per claim:
- "info": cosmetic — would not mislead a viewer (rounding, a simplified
  paraphrase that keeps the substance true).
- "warn": a careful viewer or subject-matter commenter could call it out;
  erodes trust but does not change the story.
- "critical": materially false or unsupported on a load-bearing point —
  publishing it would be irresponsible (wrong death, wrong dose magnitude,
  wrong attribution of blame, invented event).

Rules:
- Every claim needs a one-line "reason" naming the evidence you checked it
  against (a brief item or the known record) — not a restatement.
- Narrative framing, mood, and rhetorical questions are NOT claims. Do not
  pad the list with non-checkable lines.
- "supported" claims always carry severity "info".
- If a segment genuinely contains no checkable claims, return an empty
  claims list for it.

Return ONLY a JSON array, one object per segment, in the same order:
[{"segment_id": "...",
  "claims": [{"claim": "...",
              "verdict": "supported"|"unsupported_by_brief"|"overstated"|"contradicts_known_facts",
              "severity": "info"|"warn"|"critical",
              "reason": "..."}]}]
"""

STYLE_RUBRIC = """\
You are the STYLE reviewer for a narrative documentary channel.

Style target: suspenseful narrative documentary. Present tense. Dread and
momentum. Second person allowed sparingly. Anchors (dates, places,
institutions) are used only when they earn their place mid-flow — woven into
the story, never recited as scene-opening statements. The narration must
TELL A STORY, not read evidence into the record.

Score each segment 1-10 (10 = fully achieves the style target) and flag
concrete violations from this rubric:

- "evidence_reading": opens with or recites date/location/institution slates
  instead of weaving anchors into the story. ("June third, nineteen
  eighty-five. Marietta, Georgia. ..." as a scene opener is THE flagship
  violation.) An anchor that arrives mid-sentence, attached to an action or
  a consequence, is fine.
- "data_dump": numbers/specifics listed rather than dramatized — the
  narration recites figures where it should make the viewer feel their
  weight.
- "repetitive_form": same sentence shape as neighboring segments — short
  declaratives everywhere, every scene opening the same way, the same rhythm
  segment after segment. Use the PREVIOUS/NEXT SEGMENT OPENS context lines
  to judge this.
- "tell_dont_show": states a conclusion or emotion the viewer should be led
  to feel ("it was terrifying", "this was a disaster") instead of showing
  the thing that produces it.
- "passive_recitation": passive voice or agent-less constructions that drain
  momentum ("mistakes were made", "the dose was delivered") where an actor
  should be driving the sentence.
- "weak_transition": the segment neither hooks from the previous beat nor
  hands off to the next — it sits inert, killing momentum.

For every violation provide:
- "evidence": the exact offending text quoted from the narration.
- "fix": a concrete rewritten line that PRESERVES the facts (every date,
  name, number, and place in the original must survive the rewrite) while
  fixing the violation.

Scoring guide: 9-10 = clean, no violations worth flagging; 7-8 = minor
issues; 5-6 = a real violation hurting the segment; 3-4 = multiple
violations or one severe one; 1-2 = evidence-reading or data-dumping wall
to wall.

Return ONLY a JSON array, one object per segment, in the same order:
[{"segment_id": "...", "score": 1-10,
  "violations": [{"type": "...", "evidence": "...", "fix": "..."}]}]
"""

FLOW_RUBRIC = """\
You are the FLOW reviewer for a narrative documentary script. You see the
ENTIRE script in order, and you judge it as one viewing experience, not
segment by segment.

Audit, adversarially:
- hook: do the first ~3 segments earn the next ten minutes of the viewer's
  attention? Say exactly where the hook leaks.
- act_structure: does tension actually rise act over act, or does the middle
  flatten into case-file recitation? Identify where the curve breaks.
- transitions: where do consecutive segments fail to hand off — no causal or
  emotional thread carrying the viewer across the cut?
- repetition: cross-segment repetition of structure. Explicitly COUNT how
  many segments OPEN with a date or location slate (a date, place, or
  institution recited as the opening statement before any story happens) and
  list their segment ids in "date_location_openers". Also flag any other
  opening formula used 3+ times.
- dead_spots: stretches where pacing dies (consecutive segments at the same
  intensity, information without stakes).
- ending: does the final stretch land an idea, or just stop?

Each finding: {"issue": short name, "segments": [ids involved],
"severity": "info"|"warn"|"critical", "suggestion": concrete fix}.

"overall": 1-10 for the whole script as a viewing experience. Be harsh: 8+
means you tried to find structural problems and genuinely could not.

Return ONLY a JSON object:
{"date_location_openers": ["seg_id", ...],
 "counts": {"segments": N, "date_location_openers": N},
 "findings": [{"issue": "...", "segments": [...],
               "severity": "info"|"warn"|"critical", "suggestion": "..."}],
 "overall": 1-10}
"""


# ---------------------------------------------------------------------------
# Gemini plumbing (same pattern as lib/narration_gate.py)
# ---------------------------------------------------------------------------

def _make_model(model: str):
    """Build the Gemini model handle.

    Raises on missing dependency/key — callers convert that into "error"
    results per segment (fail-closed) rather than silently passing.
    """
    import google.generativeai as genai  # deferred: keep import-time light

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — cannot run review panel")

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
# Shared batch runner
# ---------------------------------------------------------------------------

def _run_batches(
    gem: Any,
    segments: Sequence[Any],
    rubric: str,
    block_fn: Callable[[Any], str],
    normalize_fn: Callable[[str, dict], dict],
    error_fn: Callable[[str, str], dict],
    batch_size: int,
    evidence: str = "",
) -> list[dict]:
    """Batched per-segment review with per-item retry, fail-closed.

    A batch response can drop or mangle individual segments without the call
    failing, so anything missing/errored after the batch pass is retried in a
    single-segment call; a segment that still cannot be reviewed keeps its
    "error" result — the CLI treats that as a failure, never a pass.
    """
    results: dict[str, dict] = {}

    def call(batch: Sequence[Any]) -> dict[str, dict]:
        blocks = "\n\n---\n\n".join(block_fn(s) for s in batch)
        parts = [ADVERSARIAL_FRAMING, rubric]
        if evidence:
            parts.append(evidence)
        parts.append(f"SEGMENTS ({len(batch)}):\n\n{blocks}")
        prompt = "\n\n".join(parts)
        try:
            response = gem.generate_content(prompt)
            parsed = _parse_json_response(response.text)
            if isinstance(parsed, dict):  # single-segment calls may return an object
                parsed = [parsed]
            if not isinstance(parsed, list):
                raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
        except Exception as exc:
            _log.warning("Batch of %d failed: %s", len(batch), str(exc)[:200])
            return {s.id: error_fn(s.id, str(exc)) for s in batch}
        by_id = {
            str(item.get("segment_id", "")): item
            for item in parsed if isinstance(item, dict)
        }
        out: dict[str, dict] = {}
        for seg in batch:
            if seg.id in by_id:
                out[seg.id] = normalize_fn(seg.id, by_id[seg.id])
            else:
                out[seg.id] = error_fn(seg.id, "segment missing from model response")
        return out

    for start in range(0, len(segments), batch_size):
        results.update(call(segments[start:start + batch_size]))

    for seg in segments:
        if seg.id not in results or results[seg.id].get("error"):
            _log.info("Retrying %s individually", seg.id)
            results.update(call([seg]))

    return [
        results.get(seg.id) or error_fn(seg.id, "no result returned")
        for seg in segments
    ]


# ---------------------------------------------------------------------------
# Reviewer 1 — fact vetting
# ---------------------------------------------------------------------------

def _fact_error(segment_id: str, why: str) -> dict:
    """Fail-closed placeholder for a segment we could not fact-vet."""
    return {
        "segment_id": segment_id,
        "claims": [],
        "worst_severity": "error",
        "error": why[:200],
    }


def _normalize_fact(segment_id: str, raw: dict) -> dict:
    """Coerce a model-produced fact result into the panel's contract.

    Any malformed claim errors the whole segment rather than being patched
    over — that routes it into the per-item retry, and if it stays broken
    the "error" verdict fails the gate (fail-closed, same policy as
    lib/narration_gate.py). worst_severity is computed HERE, in code, so the
    model cannot under-report its own findings.
    """
    claims_raw = raw.get("claims")
    if not isinstance(claims_raw, list):
        return _fact_error(segment_id, "claims missing from model response")

    claims: list[dict] = []
    for c in claims_raw:
        if not isinstance(c, dict):
            return _fact_error(segment_id, "non-object claim entry")
        verdict = str(c.get("verdict", "")).lower().strip()
        severity = str(c.get("severity", "")).lower().strip()
        if verdict not in _FACT_VERDICTS or severity not in _CLAIM_SEVERITIES:
            return _fact_error(
                segment_id, f"unrecognized verdict/severity {verdict!r}/{severity!r}")
        if verdict == "supported":
            severity = "info"  # code-enforced: a supported claim carries no weight
        claims.append({
            "claim": str(c.get("claim", "")).strip(),
            "verdict": verdict,
            "severity": severity,
            "reason": str(c.get("reason", "")).strip(),
        })

    worst = "info"
    for c in claims:
        if _SEVERITY[c["severity"]] > _SEVERITY[worst]:
            worst = c["severity"]
    return {"segment_id": segment_id, "claims": claims, "worst_severity": worst}


def _brief_evidence(brief: dict | None) -> str:
    """Serialize the research brief into the fact reviewer's evidence block.

    The brief goes in verbatim (selected sections, not a summary) so the
    reviewer cites the same artifacts a human checker would — summarizing it
    here would launder away exactly the precision the vetting needs.
    """
    if not brief:
        return (
            "NO RESEARCH BRIEF PROVIDED. Vet claims against the documented\n"
            "historical record only; a claim you cannot confirm from the\n"
            'record is "unsupported_by_brief".'
        )
    picked = {k: brief[k] for k in _EVIDENCE_KEYS if k in brief}
    if not picked:  # unfamiliar brief shape — pass it whole rather than nothing
        picked = brief
    text = json.dumps(picked, indent=1, ensure_ascii=False)
    if len(text) > _EVIDENCE_CHAR_CAP:
        text = text[:_EVIDENCE_CHAR_CAP] + "\n...[evidence truncated]"
    return (
        "RESEARCH BRIEF EVIDENCE (verified for this episode — primary "
        "evidence):\n" + text
    )


def _fact_block(seg: Any) -> str:
    return f'segment_id: {seg.id}\nNARRATION: "{seg.narration}"'


def fact_vetting(
    script: Any,
    brief: dict | None,
    model: str = _DEFAULT_MODEL,
) -> list[dict]:
    """Adversarially vet every segment's narration for factual defensibility.

    Args:
        script: A loaded ScoredScript (lib.scored_script.load_scored_script).
        brief: The episode's research brief dict (primary evidence), or None
            to vet against the model's knowledge of the record alone.
        model: Gemini model name (repo standard: gemini-2.5-flash).

    Returns:
        One dict per segment, in script order:
        {segment_id, claims: [{claim, verdict, severity, reason}],
         worst_severity: "info"|"warn"|"critical"|"error"}
    """
    segments = list(script.segments)
    if not segments:
        return []
    try:
        gem = _make_model(model)
    except Exception as exc:
        _log.error("Cannot initialize Gemini: %s", exc)
        return [_fact_error(s.id, str(exc)) for s in segments]
    return _run_batches(
        gem, segments, FACT_RUBRIC, _fact_block,
        _normalize_fact, _fact_error, _FACT_BATCH_SIZE,
        evidence=_brief_evidence(brief),
    )


# ---------------------------------------------------------------------------
# Reviewer 2 — style review
# ---------------------------------------------------------------------------

def _style_error(segment_id: str, why: str) -> dict:
    """Fail-closed placeholder for a segment we could not style-review."""
    return {
        "segment_id": segment_id,
        "score": 0,
        "violations": [],
        "error": why[:200],
    }


def _normalize_style(segment_id: str, raw: dict) -> dict:
    """Coerce a model-produced style result into the panel's contract.

    Unknown violation types error the segment (after light spelling
    normalization) instead of passing through — the rubric's type set is the
    panel's vocabulary, and downstream tooling (aggregation, fix application)
    must be able to switch on it.
    """
    try:
        score = max(1, min(10, int(raw.get("score", 0))))
    except (TypeError, ValueError):
        return _style_error(segment_id, f"non-integer score {raw.get('score')!r}")

    violations: list[dict] = []
    for v in raw.get("violations") or []:
        if not isinstance(v, dict):
            return _style_error(segment_id, "non-object violation entry")
        vtype = str(v.get("type", "")).lower().strip().replace("-", "_").replace(" ", "_")
        if vtype not in STYLE_VIOLATION_TYPES:
            return _style_error(segment_id, f"unrecognized violation type {vtype!r}")
        violations.append({
            "type": vtype,
            "evidence": str(v.get("evidence", "")).strip(),
            "fix": str(v.get("fix", "")).strip(),
        })
    return {"segment_id": segment_id, "score": score, "violations": violations}


def _opening(narration: str, n_words: int = 14) -> str:
    words = narration.split()
    head = " ".join(words[:n_words])
    return head + (" ..." if len(words) > n_words else "")


def style_review(script: Any, model: str = _DEFAULT_MODEL) -> list[dict]:
    """Adversarially score every segment against the channel style rubric.

    Each segment block carries the openings of its neighbors because
    repetitive_form is only judgeable relative to what surrounds a segment —
    a short declarative opener is fine once and a violation the fourth time
    in a row.

    Returns:
        One dict per segment, in script order:
        {segment_id, score: 1-10, violations: [{type, evidence, fix}]}
        (score 0 + "error" key when the segment could not be reviewed).
    """
    segments = list(script.segments)
    if not segments:
        return []
    try:
        gem = _make_model(model)
    except Exception as exc:
        _log.error("Cannot initialize Gemini: %s", exc)
        return [_style_error(s.id, str(exc)) for s in segments]

    # Precompute blocks with neighbor context so the batch boundary never
    # hides a neighbor from the model.
    blocks: dict[str, str] = {}
    for i, seg in enumerate(segments):
        lines = [
            f"segment_id: {seg.id}",
            f"pacing: {seg.pacing or '-'}",
            f'NARRATION: "{seg.narration}"',
        ]
        if i > 0:
            lines.append(f'PREVIOUS SEGMENT OPENS: "{_opening(segments[i - 1].narration)}"')
        if i < len(segments) - 1:
            lines.append(f'NEXT SEGMENT OPENS: "{_opening(segments[i + 1].narration)}"')
        blocks[seg.id] = "\n".join(lines)

    return _run_batches(
        gem, segments, STYLE_RUBRIC, lambda s: blocks[s.id],
        _normalize_style, _style_error, _STYLE_BATCH_SIZE,
    )


# ---------------------------------------------------------------------------
# Reviewer 3 — flow review (whole script, one pass)
# ---------------------------------------------------------------------------

# Deterministic slate-opener detection. The flow reviewer is asked to count
# these, but an LLM can under-count even when framed adversarially; a regex
# cannot. Heuristic: a SHORT opening sentence (a fragment, not a woven
# sentence) containing a month name or a year — optionally preceded by an
# even shorter location fragment ("Hamilton, Ontario. July twenty-sixth.").
_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_YEARISH = re.compile(
    r"\b(?:1[89]\d{2}|20\d{2})\b"  # numeric year
    r"|\b(?:seventeen|eighteen|nineteen|twenty)\s+"
    r"(?:hundred|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|oh)\b",
    re.IGNORECASE,
)
_SLATE_MAX_WORDS = 10


def _is_dateish(sentence: str) -> bool:
    low = sentence.lower()
    if _YEARISH.search(low):
        return True
    return any(re.search(rf"\b{m}\b", low) for m in _MONTHS)


def _opens_with_slate(narration: str) -> bool:
    """True when the narration opens on a date/location slate fragment."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", narration.strip()) if s]
    if not sents:
        return False
    first = sents[0]
    if len(first.split()) <= _SLATE_MAX_WORDS and _is_dateish(first):
        return True
    if len(sents) >= 2:
        second = sents[1]
        if (
            len(first.split()) <= 6
            and len(second.split()) <= _SLATE_MAX_WORDS
            and _is_dateish(second)
        ):
            return True
    return False


def _normalize_finding(raw: dict, valid_ids: frozenset[str]) -> dict:
    severity = str(raw.get("severity", "")).lower().strip()
    if severity not in _CLAIM_SEVERITIES:
        severity = "warn"  # a finding with a junk severity is still a finding
    return {
        "issue": str(raw.get("issue", "")).strip(),
        "segments": [str(s) for s in raw.get("segments") or [] if str(s) in valid_ids],
        "severity": severity,
        "suggestion": str(raw.get("suggestion", "")).strip(),
    }


def flow_review(script: Any, model: str = _DEFAULT_MODEL) -> dict:
    """Adversarially audit the whole script as one viewing experience.

    One Gemini call over the full narration sequence — flow problems (tension
    curve, repetition, dead spots) are invisible to per-segment review by
    construction. The model's slate-opener list is merged (union) with the
    deterministic _opens_with_slate() detection: either reviewer missing one
    must not make it disappear.

    Returns:
        {date_location_openers: [ids], counts: {...},
         findings: [{issue, segments, severity, suggestion}], overall: 1-10}
        (overall 0 + "error" key when the model pass failed; the heuristic
        opener detection still runs so the report is never empty).
    """
    segments = list(script.segments)
    order = {s.id: i for i, s in enumerate(segments)}
    valid_ids = frozenset(order)
    heuristic_ids = [s.id for s in segments if _opens_with_slate(s.narration)]

    seg_lines = "\n".join(
        f'{s.id} [{s.act} | pacing: {s.pacing or "-"}]: "{s.narration}"'
        for s in segments
    )
    prompt = (
        f"{ADVERSARIAL_FRAMING}\n\n{FLOW_RUBRIC}\n\n"
        f'SCRIPT: "{script.title}" — {len(segments)} segments, in order:\n\n'
        f"{seg_lines}"
    )

    model_ids: list[str] = []
    findings: list[dict] = []
    overall = 0
    error: str | None = None
    try:
        gem = _make_model(model)
        response = gem.generate_content(prompt)
        parsed = _parse_json_response(response.text)
        if not isinstance(parsed, dict):
            raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
        model_ids = [
            str(s) for s in parsed.get("date_location_openers") or []
            if str(s) in valid_ids
        ]
        findings = [
            _normalize_finding(f, valid_ids)
            for f in parsed.get("findings") or [] if isinstance(f, dict)
        ]
        try:
            overall = max(1, min(10, int(parsed.get("overall", 0))))
        except (TypeError, ValueError):
            overall = 1
    except Exception as exc:
        error = str(exc)[:200]
        _log.error("Flow review failed: %s", error)

    merged = sorted(set(model_ids) | set(heuristic_ids), key=order.__getitem__)
    result: dict[str, Any] = {
        "date_location_openers": merged,
        "counts": {
            "segments": len(segments),
            "date_location_openers": len(merged),
            "model_reported": len(model_ids),
            "heuristic_detected": len(heuristic_ids),
        },
        "findings": findings,
        "overall": overall,
    }
    if error:
        result["error"] = error
    return result


# ---------------------------------------------------------------------------
# Reviewer 5 — retention architecture (whole script, one pass)
# ---------------------------------------------------------------------------

# Digital-retention craft per the channel's scriptwriting research: viewers
# leave when they stop anticipating, so the script is judged as an engineered
# anticipation machine — hook formulas, a scope-promise that earns runtime,
# Setup-Tension-Payoff loops instead of chronology, ascending revelation
# value, and every planted question paid off.
RETENTION_RUBRIC = """\
You are the RETENTION ARCHITECTURE reviewer for a digital documentary script.
You receive every segment (id + narration + pacing + silence). Judge the
script as an anticipation machine:

1. "hook": grade the first ~30 seconds (first 2-3 segments) against BOTH
   formulas — Kallaway (context + contrast word + contrarian statement) and
   Blackman (character + concept + dire stakes). Check hook sentences stay
   under ~10 words. Does it open a real curiosity gap AND sell the script's
   actual best material (write-the-hook-last test: does the hook promise what
   the body delivers)? Score 1-10 with specifics.
2. "roadmap": is there a scope-promise/roadmap beat in the first ~90 seconds
   (how big this gets + what the payoff will be)? If absent, say where it
   would fit. Score 1-10.
3. "loops": map the body into Setup-Tension-Payoff loops: every setup/tease
   planted (segment id + quote), where it pays off (segment id), or UNFIRED.
   Flag teaching-without-a-question stretches (mechanism explained before any
   question makes the viewer want it) and value leaks (a climax revelation
   spent early).
4. "value_sequencing": are revelations in ascending order — second-best early
   to build trust, the best at the climax slot? Name any inversions.
5. "open_questions": every curiosity question the script plants, with status
   closed/half-closed/never-closed (segment ids).

Return ONLY JSON:
{"hook": {"score": N, "kallaway": "...", "blackman": "...", "sells_payload": true/false, "issues": ["..."]},
 "roadmap": {"score": N, "present": true/false, "where_it_fits": "..."},
 "loops": [{"setup_segment": "...", "setup": "...", "payoff_segment": "..." or "", "status": "closed"|"half"|"unfired", "note": "..."}],
 "value_sequencing": {"compliant": true/false, "inversions": ["..."]},
 "open_questions": [{"question": "...", "planted": "seg_xxx", "status": "closed"|"half"|"never", "closed_at": "..."}],
 "overall": N,
 "top_fixes": ["..."]}
"""


def retention_review(script: Any, model: str = _DEFAULT_MODEL) -> dict:
    """Whole-script retention-architecture review. Fail-closed on errors."""
    try:
        gem = _make_model(model)
        lines = []
        for seg in script.segments:
            lines.append(f"[{seg.id}] (pacing: {seg.pacing or '-'}; "
                         f"silence_after: {getattr(seg, 'silence_after_s', 0)}s)\n"
                         f"{seg.narration.strip()}")
        body = "\n\n".join(lines)
        resp = gem.generate_content(
            "\n\n".join([ADVERSARIAL_FRAMING, RETENTION_RUBRIC,
                         f"SCRIPT ({len(script.segments)} segments):\n\n{body}"]))
        out = _parse_json_response(resp.text)
        out.setdefault("overall", 0)
        out["verdict"] = "ok"
        return out
    except Exception as exc:  # noqa: BLE001
        _log.error("retention review failed: %s", str(exc)[:200])
        return {"verdict": "error", "error": str(exc)[:200], "overall": 0,
                "loops": [], "open_questions": []}


# ---------------------------------------------------------------------------
# Reviewer 4 — shot doctor (visual staging per segment)
# ---------------------------------------------------------------------------

# A visual that merely ILLUSTRATES the setting wastes the beat: the alignment
# gate passes it (it depicts what's narrated) but the frame makes no argument.
# Canonical example from this channel: narration lands the irony "the machine's
# display read: treatment delivered normally" — the weak shot is a portrait of
# a calm terminal; the strong shot STAGES the irony in depth: the terminal
# serene in the foreground while, small in the background, the patient on the
# table flinches under the machine. The shot doctor hunts that gap.
SHOT_RUBRIC = """\
You are the SHOT DOCTOR — the visual-staging reviewer for an illustrated
documentary. For each segment you get the NARRATION the viewer hears, the
segment's editorial intent and pacing, and the CURRENT VISUAL PROMPT(S) that
will generate the on-screen image (a keyframe animated by an image-to-video
model).

Your question is never "does the visual match the narration" (another gate
checks that). Your question is: IS THIS THE STRONGEST SHOT? A beat's visual
must STAGE its dramatic core — the conflict, irony, turn, or consequence in
the narration — inside the frame, not merely illustrate the setting where it
happened.

Staging devices to consider (name the one the current shot misses):
- foreground/background juxtaposition: two truths in one frame (the calm
  machine readout in front, the suffering it denies behind)
- the prop that lies: frame the object whose message contradicts reality
- consequence in frame: the damage/result visible WITH its cause
- scale contrast: the small human against the huge machine (power relations)
- isolation: the subject alone in oversized negative space
- point of view: the camera as a participant (the patient's view up at the
  beam head; the operator's view of only the screen)
- before/within the turn: stage the instant the narration pivots on

HARD CHANNEL CONSTRAINTS — every suggestion must respect these:
- Graphic-novel illustration; do NOT include style/medium words (the pipeline
  appends style separately).
- NO legible text, numbers, or readouts inside the generated frame (text warps
  under image-to-video). When the WORDS are the irony (a screen message, a
  label), the frame stages the silent prop (a calm glowing screen) and the
  burned-caption overlay layer carries the words — propose "overlay_lines"
  (short, ALL-CAPS-style caption lines) in that case.
- Anonymous illustrated figures only; faces may show emotion but never depict
  a real identifiable person's likeness.
- Period 1985: CRT, beige metal, no modern hardware.
- One frame per shot, generated from a single keyframe: stage compositions
  that read in ONE still (depth, blocking, light), not edits or montages.

Score staging_score 1-10: 9-10 = the frame argues the beat (a viewer with the
sound off would still feel the irony/turn); 6-8 = solid but generic staging;
3-5 = illustrates the setting, wastes the beat; 1-2 = works against the beat.
Verdict "stages" (>=6) or "illustrates" (<=5).

For every segment scoring <=7, write "staged_prompt": a concrete replacement
shot prompt (content and composition only — subjects, blocking, depth,
camera angle, light; no style words) that stages the dramatic core. Keep the
segment's location/assets unless the staging genuinely needs a second
recurring asset in frame — then name it in "support_assets_hint".

Return ONLY a JSON array, one object per segment:
{"segment_id": "...", "dramatic_core": "one line - the beat's conflict/irony/turn",
 "verdict": "stages" | "illustrates", "staging_score": 1-10,
 "missed_device": "which staging device the current shot leaves on the table ('' if none)",
 "staged_prompt": "replacement shot prompt ('' when score >= 8)",
 "overlay_lines": ["CAPTION LINE", ...] or [],
 "support_assets_hint": "bible asset_id(s) the staging needs in-frame beyond the segment's own ('' if none)"}
"""


def _shot_error(segment_id: str, why: str) -> dict:
    """Fail-closed placeholder for a segment the shot doctor could not review."""
    return {
        "segment_id": segment_id,
        "staging_score": 0,
        "verdict": "error",
        "error": why[:200],
    }


def _normalize_shot(segment_id: str, raw: dict) -> dict:
    """Coerce a model-produced shot review into the panel's contract."""
    try:
        score = max(1, min(10, int(raw.get("staging_score", 0))))
    except (TypeError, ValueError):
        return _shot_error(segment_id, f"non-integer staging_score {raw.get('staging_score')!r}")
    verdict = str(raw.get("verdict", "")).lower().strip()
    if verdict not in _SHOT_VERDICTS:
        return _shot_error(segment_id, f"unrecognized verdict {verdict!r}")
    overlay = raw.get("overlay_lines") or []
    if not isinstance(overlay, list):
        overlay = []
    return {
        "segment_id": segment_id,
        "verdict": verdict,
        "staging_score": score,
        "dramatic_core": str(raw.get("dramatic_core", "")).strip(),
        "missed_device": str(raw.get("missed_device", "")).strip(),
        "staged_prompt": str(raw.get("staged_prompt", "")).strip(),
        "overlay_lines": [str(x).strip() for x in overlay if str(x).strip()],
        "support_assets_hint": str(raw.get("support_assets_hint", "")).strip(),
    }


_SHOT_TYPES = frozenset({"ai_video", "atmospheric_footage", "generated_footage"})


def shot_review(script: Any, model: str = _DEFAULT_MODEL) -> list[dict]:
    """Adversarially review every AI-video segment's visual STAGING.

    Returns one dict per reviewed segment, in script order:
    {segment_id, verdict: stages|illustrates, staging_score, dramatic_core,
     missed_device, staged_prompt, overlay_lines, support_assets_hint}
    (staging_score 0 + "error" when a segment could not be reviewed).
    Non-generative segments (diagrams, cards) are skipped — their staging
    lives in the diagram/card systems, not in shot prompts.
    """
    segments = [s for s in script.segments
                if getattr(s.visual, "type", "") in _SHOT_TYPES]
    if not segments:
        return []
    try:
        gem = _make_model(model)
    except Exception as exc:  # noqa: BLE001
        _log.error("Cannot initialize Gemini: %s", exc)
        return [_shot_error(s.id, str(exc)) for s in segments]

    def block(seg: Any) -> str:
        vs = seg.visual
        prompts = [f'SHOT {sh.shot_id}: "{sh.ai_prompt}"' for sh in (vs.shots or [])]
        if not prompts:
            prompts = [f'PROMPT: "{vs.effective_prompt}"']
        lines = [
            f"segment_id: {seg.id}",
            f"editorial_intent: {seg.editorial_intent or '-'}    pacing: {seg.pacing or '-'}",
            f'NARRATION: "{seg.narration}"',
            *prompts,
        ]
        if vs.ai_motion:
            lines.append(f'MOTION: "{vs.ai_motion}"')
        if vs.text_overlay:
            lines.append(f"EXISTING OVERLAY LINES: {vs.text_overlay}")
        if vs.asset_ref or vs.location_id:
            lines.append(f"asset: {vs.asset_ref or vs.location_id}")
        return "\n".join(lines)

    return _run_batches(
        gem, segments, SHOT_RUBRIC, block,
        _normalize_shot, _shot_error, _STYLE_BATCH_SIZE,
    )


# ---------------------------------------------------------------------------
# Panel aggregation
# ---------------------------------------------------------------------------

_PANELS = ("fact", "style", "flow", "shots", "retention")


def _find_brief(script_path: Path) -> Path | None:
    """Locate the project's research brief relative to the script.

    Project layout puts the script under projects/<name>/script_*/ and the
    brief under projects/<name>/research/, so walking a few ancestors finds
    it without any per-project configuration.
    """
    for parent in [script_path.parent, *list(script_path.parent.parents)[:3]]:
        candidate = parent / "research" / "research_brief.json"
        if candidate.exists():
            return candidate
    return None


def _summarize(report: dict) -> dict:
    """Compute the roll-up summary in code so it always agrees with the data."""
    summary: dict[str, Any] = {}
    if "fact" in report:
        results = report["fact"]["results"]
        counts: dict[str, int] = {}
        for r in results:
            counts[r["worst_severity"]] = counts.get(r["worst_severity"], 0) + 1
        critical = [
            {"segment_id": r["segment_id"], "claim": c["claim"],
             "verdict": c["verdict"], "reason": c["reason"]}
            for r in results for c in r["claims"] if c["severity"] == "critical"
        ]
        summary["fact"] = {
            "segments": len(results),
            "worst_severity_counts": counts,
            "critical_claims": critical,
        }
    if "style" in report:
        results = report["style"]["results"]
        scored = [r for r in results if not r.get("error")]
        vio_counts: dict[str, int] = {}
        for r in scored:
            for v in r["violations"]:
                vio_counts[v["type"]] = vio_counts.get(v["type"], 0) + 1
        worst = sorted(scored, key=lambda r: r["score"])[:5]
        summary["style"] = {
            "segments": len(results),
            "errors": len(results) - len(scored),
            "average_score": (
                round(sum(r["score"] for r in scored) / len(scored), 2)
                if scored else 0.0
            ),
            "violation_counts": vio_counts,
            "worst_segments": [
                {"segment_id": r["segment_id"], "score": r["score"]} for r in worst
            ],
        }
    if "flow" in report:
        flow = report["flow"]
        summary["flow"] = {
            "overall": flow["overall"],
            "date_location_openers": flow["counts"]["date_location_openers"],
            "critical_findings": sum(
                1 for f in flow["findings"] if f["severity"] == "critical"),
        }
    if "shots" in report:
        results = report["shots"]["results"]
        scored = [r for r in results if not r.get("error")]
        illustrates = [r for r in scored if r["verdict"] == "illustrates"]
        summary["shots"] = {
            "segments": len(results),
            "errors": len(results) - len(scored),
            "average_staging": (
                round(sum(r["staging_score"] for r in scored) / len(scored), 2)
                if scored else 0.0
            ),
            "illustrates_count": len(illustrates),
            "weakest_segments": [
                {"segment_id": r["segment_id"], "staging_score": r["staging_score"]}
                for r in sorted(scored, key=lambda r: r["staging_score"])[:5]
            ],
        }
    if "retention" in report:
        ret = report["retention"]
        loops = ret.get("loops") or []
        summary["retention"] = {
            "overall": ret.get("overall", 0),
            "hook_score": (ret.get("hook") or {}).get("score", 0),
            "roadmap_present": (ret.get("roadmap") or {}).get("present", False),
            "unfired_setups": [
                {"setup_segment": l.get("setup_segment"), "setup": l.get("setup")}
                for l in loops if l.get("status") == "unfired"
            ],
            "never_closed_questions": [
                q.get("question") for q in (ret.get("open_questions") or [])
                if q.get("status") == "never"
            ],
        }
    # The panel's pass/fail contract: critical (or unreviewable) fact issues
    # block; style and flow inform. Mirrors the CLI exit code.
    fact_results = report.get("fact", {}).get("results", [])
    summary["passed"] = not any(
        r["worst_severity"] in ("critical", "error") for r in fact_results
    )
    return summary


def run_panel(
    script_path: str | Path,
    panels: Sequence[str] = _PANELS,
    report_path: str | Path | None = None,
    brief_path: str | Path | None = None,
    model: str = _DEFAULT_MODEL,
) -> dict:
    """Run the adversarial review panel over a scored script.

    Args:
        script_path: Path to scored_script.yaml.
        panels: Which reviewers to run, any of ("fact", "style", "flow").
        report_path: When given, the aggregated report is also written there
            as JSON (parents created).
        brief_path: Explicit research brief path; by default the brief is
            auto-discovered at <project>/research/research_brief.json
            relative to the script.
        model: Gemini model name for all reviewers.

    Returns:
        Aggregated report dict: per-panel results plus a code-computed
        "summary" (so the roll-up can never disagree with the raw results).
    """
    from lib.scored_script import load_scored_script

    unknown = set(panels) - set(_PANELS)
    if unknown:
        raise ValueError(f"unknown panels {sorted(unknown)}; valid: {_PANELS}")

    script_path = Path(script_path)
    script = load_scored_script(script_path)

    brief: dict | None = None
    resolved_brief = Path(brief_path) if brief_path else _find_brief(script_path)
    if resolved_brief and resolved_brief.exists():
        brief = json.loads(resolved_brief.read_text(encoding="utf-8"))
        _log.info("Using research brief: %s", resolved_brief)
    else:
        _log.warning("No research brief found — fact vetting uses model knowledge only")

    report: dict[str, Any] = {
        "script": str(script_path),
        "title": script.title,
        "model": model,
        "panels": [p for p in _PANELS if p in panels],
        "brief": str(resolved_brief) if resolved_brief else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if "fact" in panels:
        _log.info("Running fact vetting (%d segments)", len(script.segments))
        report["fact"] = {"results": fact_vetting(script, brief, model=model)}
    if "style" in panels:
        _log.info("Running style review (%d segments)", len(script.segments))
        report["style"] = {"results": style_review(script, model=model)}
    if "flow" in panels:
        _log.info("Running flow review (whole script)")
        report["flow"] = flow_review(script, model=model)
    if "shots" in panels:
        _log.info("Running shot doctor (visual staging)")
        report["shots"] = {"results": shot_review(script, model=model)}
    if "retention" in panels:
        _log.info("Running retention architecture review (whole script)")
        report["retention"] = retention_review(script, model=model)

    report["summary"] = _summarize(report)

    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        _log.info("Report written: %s", out)
    return report


# ---------------------------------------------------------------------------
# CLI — readable worst-first summary
# ---------------------------------------------------------------------------

def _clip(text: str, width: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _print_fact(results: list[dict]) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["worst_severity"]] = counts.get(r["worst_severity"], 0) + 1
    print("\n== FACT VETTING ==")
    print("Worst severity per segment: "
          + ", ".join(f"{k}={v}" for k, v in sorted(
              counts.items(), key=lambda kv: -_SEVERITY[kv[0]])))
    ordered = sorted(results, key=lambda r: -_SEVERITY[r["worst_severity"]])
    for r in ordered:
        if r["worst_severity"] == "info" and not r.get("error"):
            continue
        if r.get("error"):
            print(f"  {r['segment_id']}  ERROR: {r['error']}")
            continue
        for c in r["claims"]:
            if c["severity"] == "info":
                continue
            print(f"  {r['segment_id']}  [{c['severity']}] {c['verdict']}: "
                  f"{_clip(c['claim'], 100)}")
            print(f"             -> {_clip(c['reason'], 160)}")


def _print_style(results: list[dict]) -> None:
    scored = [r for r in results if not r.get("error")]
    avg = sum(r["score"] for r in scored) / len(scored) if scored else 0.0
    print(f"\n== STYLE REVIEW ==  average {avg:.1f}/10 over {len(scored)} segments")
    for r in results:
        if r.get("error"):
            print(f"  {r['segment_id']}  ERROR: {r['error']}")
    for r in sorted(scored, key=lambda r: r["score"])[:10]:
        print(f"  {r['segment_id']}  score {r['score']}/10")
        for v in r["violations"]:
            print(f"      [{v['type']}] {_clip(v['evidence'], 110)}")
            if v["fix"]:
                print(f"      fix: {_clip(v['fix'], 160)}")


def _print_flow(flow: dict) -> None:
    print(f"\n== FLOW REVIEW ==  overall {flow['overall']}/10")
    if flow.get("error"):
        print(f"  ERROR: {flow['error']}")
    c = flow["counts"]
    print(f"  Date/location slate openers: {c['date_location_openers']} of "
          f"{c['segments']} segments (model {c['model_reported']}, "
          f"heuristic {c['heuristic_detected']})")
    if flow["date_location_openers"]:
        print("    " + ", ".join(flow["date_location_openers"]))
    for f in sorted(flow["findings"], key=lambda f: -_SEVERITY[f["severity"]]):
        segs = ",".join(f["segments"][:8]) or "-"
        print(f"  [{f['severity']}] {f['issue']} ({segs})")
        if f["suggestion"]:
            print(f"      -> {_clip(f['suggestion'], 180)}")


def _print_shots(results: list[dict]) -> None:
    scored = [r for r in results if not r.get("error")]
    avg = (sum(r["staging_score"] for r in scored) / len(scored)) if scored else 0.0
    n_ill = sum(1 for r in scored if r["verdict"] == "illustrates")
    print(f"\n== SHOT DOCTOR ==  average staging {avg:.1f}/10 over {len(scored)} "
          f"segments; {n_ill} merely illustrate their beat")
    for r in results:
        if r.get("error"):
            print(f"  {r['segment_id']}  ERROR: {r['error']}")
    for r in sorted(scored, key=lambda r: r["staging_score"])[:10]:
        if r["staging_score"] >= 8:
            continue
        print(f"  {r['segment_id']}  staging {r['staging_score']}/10 "
              f"[{r['verdict']}]  core: {_clip(r['dramatic_core'], 90)}")
        if r["missed_device"]:
            print(f"      missed device: {r['missed_device']}")
        if r["staged_prompt"]:
            print(f"      staged: {_clip(r['staged_prompt'], 200)}")
        if r["overlay_lines"]:
            print(f"      overlay: {r['overlay_lines']}")
        if r["support_assets_hint"]:
            print(f"      support assets: {r['support_assets_hint']}")


def _print_retention(ret: dict) -> None:
    print(f"\n== RETENTION ARCHITECTURE ==  overall {ret.get('overall', 0)}/10")
    if ret.get("error"):
        print(f"  ERROR: {ret['error']}")
        return
    hook = ret.get("hook") or {}
    print(f"  hook {hook.get('score', '?')}/10  sells_payload={hook.get('sells_payload')}")
    for i in (hook.get("issues") or [])[:3]:
        print(f"    - {_clip(str(i), 140)}")
    rm = ret.get("roadmap") or {}
    print(f"  roadmap {rm.get('score', '?')}/10  present={rm.get('present')}"
          + (f"  fits: {_clip(str(rm.get('where_it_fits', '')), 100)}" if not rm.get("present") else ""))
    for l in (ret.get("loops") or []):
        if l.get("status") == "unfired":
            print(f"  UNFIRED SETUP {l.get('setup_segment')}: {_clip(str(l.get('setup', '')), 110)}")
    for q in (ret.get("open_questions") or []):
        if q.get("status") == "never":
            print(f"  NEVER CLOSED ({q.get('planted')}): {_clip(str(q.get('question', '')), 110)}")
    for f in (ret.get("top_fixes") or [])[:5]:
        print(f"  fix: {_clip(str(f), 160)}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m lib.script_review",
        description="Adversarial script-review panel (fact / style / flow / shots / retention).",
    )
    parser.add_argument("script", help="Path to scored_script.yaml")
    parser.add_argument("--report", help="Write full JSON report to this path")
    parser.add_argument(
        "--panel", default="fact,style,flow,shots,retention",
        help="Comma-separated subset of fact,style,flow,shots,retention (default: all)")
    parser.add_argument(
        "--brief", help="Path to research_brief.json (default: auto-discover)")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Windows consoles default to cp1252 — narration text contains em dashes
    # and other punctuation that must not crash the panel's own output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # The CLI must work standalone: importing the tool contract loads .env
    # (GOOGLE_API_KEY) as a side effect, same as every other repo entry point.
    try:
        import tools.base_tool  # noqa: F401
    except ImportError:
        pass  # running outside the repo — rely on the ambient environment

    panels = tuple(p.strip() for p in args.panel.split(",") if p.strip())
    report = run_panel(
        args.script, panels=panels, report_path=args.report,
        brief_path=args.brief, model=args.model,
    )

    print(f"\nScript review panel — {report['title']} "
          f"(panels: {', '.join(report['panels'])})")
    if "fact" in report:
        _print_fact(report["fact"]["results"])
    if "style" in report:
        _print_style(report["style"]["results"])
    if "flow" in report:
        _print_flow(report["flow"])
    if "shots" in report:
        _print_shots(report["shots"]["results"])
    if "retention" in report:
        _print_retention(report["retention"])

    passed = report["summary"]["passed"]
    print(f"\nPANEL: {'PASSED' if passed else 'FAILED'} "
          "(fails on critical or unreviewable fact findings)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
