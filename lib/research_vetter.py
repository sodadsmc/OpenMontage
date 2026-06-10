"""Research vetter — adversarial + deterministic gates for the research brief.

The research-director skill promises "if you don't find it and cite it, it
won't be in the video" — but nothing enforced the promise. The Therac v1 brief
shipped hand-assembled (4 sources, primary paper never deeply extracted), and
the gaps surfaced months later as fact-vetting false positives and missing
story beats. This module makes brief quality checkable at stage 1, where it
costs nothing:

  1. lint_brief()      — deterministic, free: counts, citations, source
                         diversity, page-cite discipline for primary claims.
  2. vet_brief()       — adversarial Gemini pass: what would a deep extraction
                         of the named primary source contain that is ABSENT?
                         Which citations claim more credibility than they show?
  3. build_claims_map() — narration claim -> data_point mapping ("claims trace
                         to the brief" as a checkable artifact, not prose).

CLI:
  python -m lib.research_vetter <research_brief.json>
      [--script <scored_script.yaml>] [--claims-map <out.json>]
      [--report <out.json>] [--model gemini-2.5-flash]
Exit 1 on lint errors or an unreviewable vet; LLM findings and unmapped claims
are reported (the script-side fact panel remains the hard gate for narration).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Shared panel machinery (batched per-item review with fail-closed retry, the
# Gemini model factory, and the adversarial system framing). Private to
# script_review by convention, but one implementation beats two drifting ones.
from lib.script_review import ADVERSARIAL_FRAMING, _make_model, _parse_json_response, _run_batches

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"

# Lint thresholds — the research-director skill's quality bar, enforced.
MIN_DATA_POINTS = 10
TARGET_DATA_POINTS = 20
MIN_TIMELINE_EVENTS = 5
MIN_SOURCE_DOMAINS = 3


# ---------------------------------------------------------------------------
# 1. Deterministic lint (free)
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    try:
        return url.split("//", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return url


def lint_brief(brief: dict) -> tuple[list[str], list[str]]:
    """Deterministic brief checks. Returns (errors, warnings).

    Errors block (the skill's stated minimums); warnings are the gap between
    minimum and target — visible, not blocking.
    """
    errors: list[str] = []
    warnings: list[str] = []

    dps = brief.get("data_points") or []
    if len(dps) < MIN_DATA_POINTS:
        errors.append(f"data_points: {len(dps)} < minimum {MIN_DATA_POINTS}")
    elif len(dps) < TARGET_DATA_POINTS:
        warnings.append(f"data_points: {len(dps)} below target {TARGET_DATA_POINTS}")

    for i, dp in enumerate(dps):
        if not dp.get("source_url"):
            errors.append(f"data_points[{i}] missing source_url: {str(dp.get('claim'))[:60]}")
        if not dp.get("credibility"):
            errors.append(f"data_points[{i}] missing credibility")
        if dp.get("credibility") == "primary_source" and not dp.get("page_or_section"):
            warnings.append(
                f"data_points[{i}] claims primary_source without page_or_section: "
                f"{str(dp.get('claim'))[:60]}")

    tl = brief.get("timeline_events") or []
    if len(tl) < MIN_TIMELINE_EVENTS:
        errors.append(f"timeline_events: {len(tl)} < minimum {MIN_TIMELINE_EVENTS}")
    uncited = [e for e in tl if not e.get("source_url")]
    if uncited:
        errors.append(f"{len(uncited)} timeline_events missing source_url")

    if not (brief.get("primary_source") or {}).get("url"):
        errors.append("primary_source missing or has no url")

    for s in brief.get("stakeholders") or []:
        if not s.get("verified_in"):
            warnings.append(f"stakeholder {s.get('name', '?')} missing verified_in status")

    domains = {_domain(dp.get("source_url", "")) for dp in dps if dp.get("source_url")}
    if len(domains) < MIN_SOURCE_DOMAINS:
        errors.append(f"source diversity: {len(domains)} distinct domains < {MIN_SOURCE_DOMAINS} "
                      f"({sorted(domains)})")

    if "unverifiable_claims" not in brief:
        warnings.append("no unverifiable_claims key — a deep pass always finds some")

    return errors, warnings


# ---------------------------------------------------------------------------
# 2. Adversarial extraction-completeness vet (Gemini)
# ---------------------------------------------------------------------------

VET_RUBRIC = """\
You are the RESEARCH VETTER for a documentary pipeline. You receive a research
brief (JSON) whose stated primary source is named inside it. Attack the brief's
DEPTH, not its facts:

1. "extraction_gaps": Knowing what the named primary source covers (use your
   knowledge of it if it is a well-known document), list classes of facts a
   DEEP extraction would contain that are absent or thin in the brief — e.g.
   mechanism details, per-incident specifics, organizational responses,
   investigator findings, stated lessons. Be concrete: name the missing fact,
   not just the category.
2. "citation_inflation": data_points whose credibility tier overstates the
   evidence shown (primary_source without a locating cite; claims that the
   named source does not actually contain).
3. "conflict_blindness": claims that commonly vary across sources for this
   topic where the brief records only one value with no conflict note.
4. "single_thread_risk": story-relevant areas resting on ONE source.

Score "depth_score" 1-10 (10 = a writer could draft the full episode from this
brief alone without re-research). Return ONLY JSON:
{"depth_score": N,
 "extraction_gaps": [{"missing": "...", "why_it_matters": "..."}],
 "citation_inflation": [{"claim": "...", "problem": "..."}],
 "conflict_blindness": [{"claim": "...", "variance": "..."}],
 "single_thread_risk": ["..."]}
"""


def vet_brief(brief: dict, model: str = _DEFAULT_MODEL) -> dict:
    """Adversarial depth review of the brief. Fail-closed: errors return
    verdict 'error' (never a silent pass)."""
    try:
        gem = _make_model(model)
        payload = json.dumps(brief, ensure_ascii=False)[:60_000]
        resp = gem.generate_content(
            "\n\n".join([ADVERSARIAL_FRAMING, VET_RUBRIC, f"RESEARCH BRIEF:\n{payload}"]))
        out = _parse_json_response(resp.text)
        out["verdict"] = "ok"
        return out
    except Exception as exc:  # noqa: BLE001
        _log.error("research vet failed: %s", str(exc)[:200])
        return {"verdict": "error", "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# 3. Claims map — narration claim -> data_point ids
# ---------------------------------------------------------------------------

CLAIMS_RUBRIC = """\
You map documentary narration claims to the research brief's data points.

You receive segments (id + narration) and an indexed list of data points
(dp_00, dp_01, ...). For EVERY checkable factual claim in each segment's
narration (names, dates, places, numbers, doses, causal/technical assertions,
institutional actions), find the supporting data point(s).

- "mapped": one or more data points directly support the claim.
- "partial": data points support part of it (say which part is uncovered).
- "unmapped": no data point supports it — the claim rests on nothing.
Atmospheric/emotional narration with no checkable content produces no entries.

Return ONLY a JSON array, one object per segment:
{"segment_id": "...",
 "claims": [{"claim": "...", "status": "mapped"|"partial"|"unmapped",
             "data_points": ["dp_07", ...], "note": "..."}]}
"""


def build_claims_map(script: Any, brief: dict,
                     model: str = _DEFAULT_MODEL) -> list[dict]:
    """Map every narration claim to brief data points (batched, fail-closed).

    This turns the manifest's "narration claims trace to research_brief
    data_points" from prose into an artifact: unmapped claims are the
    research debt, enumerated.
    """
    dps = brief.get("data_points") or []
    index = "\n".join(
        f"dp_{i:02d}: {str(dp.get('claim', ''))[:200]}" for i, dp in enumerate(dps))
    segments = list(script.segments)
    try:
        gem = _make_model(model)
    except Exception as exc:  # noqa: BLE001
        _log.error("Cannot initialize Gemini: %s", exc)
        return [{"segment_id": s.id, "error": str(exc)[:200], "claims": []}
                for s in segments]

    def _err(segment_id: str, why: str) -> dict:
        return {"segment_id": segment_id, "error": why[:200], "claims": []}

    def _norm(segment_id: str, raw: dict) -> dict:
        claims = []
        for c in raw.get("claims") or []:
            if not isinstance(c, dict):
                return _err(segment_id, "non-object claim entry")
            status = str(c.get("status", "")).lower()
            if status not in ("mapped", "partial", "unmapped"):
                return _err(segment_id, f"bad status {status!r}")
            claims.append({
                "claim": str(c.get("claim", "")).strip(),
                "status": status,
                "data_points": [str(x) for x in (c.get("data_points") or [])],
                "note": str(c.get("note", "")).strip(),
            })
        return {"segment_id": segment_id, "claims": claims}

    rubric = CLAIMS_RUBRIC + f"\n\nDATA POINTS ({len(dps)}):\n{index}"
    return _run_batches(
        gem, segments, rubric,
        lambda s: f'segment_id: {s.id}\nNARRATION: "{s.narration}"',
        _norm, _err, 6,
    )


def claims_summary(cmap: list[dict]) -> dict:
    counts = {"mapped": 0, "partial": 0, "unmapped": 0, "errors": 0}
    unmapped: list[dict] = []
    for seg in cmap:
        if seg.get("error"):
            counts["errors"] += 1
            continue
        for c in seg["claims"]:
            counts[c["status"]] += 1
            if c["status"] != "mapped":
                unmapped.append({"segment_id": seg["segment_id"], **c})
    return {"counts": counts, "needs_research": unmapped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m lib.research_vetter",
        description="Deterministic lint + adversarial depth vet + claims map for a research brief.")
    parser.add_argument("brief", help="Path to research_brief.json")
    parser.add_argument("--script", help="scored_script.yaml — build the claims map against it")
    parser.add_argument("--claims-map", help="Write the claims map JSON here")
    parser.add_argument("--report", help="Write the full vet report JSON here")
    parser.add_argument("--no-llm", action="store_true", help="Lint only (free)")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        import tools.base_tool  # noqa: F401  — loads .env standalone
    except ImportError:
        pass

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    report: dict[str, Any] = {"brief": args.brief}

    errors, warnings = lint_brief(brief)
    report["lint"] = {"errors": errors, "warnings": warnings}
    print(f"== LINT ==  {len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print(f"  ERROR  {e}")
    for w in warnings:
        print(f"  warn   {w}")

    vet: dict = {}
    if not args.no_llm:
        vet = vet_brief(brief, model=args.model)
        report["vet"] = vet
        if vet.get("verdict") == "error":
            print(f"\n== DEPTH VET ==  ERROR: {vet.get('error')}")
        else:
            print(f"\n== DEPTH VET ==  depth_score {vet.get('depth_score')}/10")
            for g in vet.get("extraction_gaps", [])[:8]:
                print(f"  gap: {g.get('missing', '')[:120]}")
            for c in vet.get("citation_inflation", [])[:5]:
                print(f"  citation: {c.get('claim', '')[:80]} -> {c.get('problem', '')[:80]}")

    if args.script:
        from lib.scored_script import load_scored_script
        script = load_scored_script(args.script)
        cmap = build_claims_map(script, brief, model=args.model)
        summary = claims_summary(cmap)
        report["claims_map_summary"] = summary
        c = summary["counts"]
        print(f"\n== CLAIMS MAP ==  mapped={c['mapped']} partial={c['partial']} "
              f"unmapped={c['unmapped']} seg_errors={c['errors']}")
        for u in summary["needs_research"][:10]:
            print(f"  [{u['status']}] {u['segment_id']}: {u['claim'][:100]}")
        if args.claims_map:
            out = Path(args.claims_map)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"map": cmap, "summary": summary},
                                      indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  claims map written: {out}")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    failed = bool(errors) or (vet.get("verdict") == "error" if not args.no_llm else False)
    print(f"\nVETTER: {'FAILED' if failed else 'PASSED'} "
          "(fails on lint errors or an unreviewable depth vet)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
