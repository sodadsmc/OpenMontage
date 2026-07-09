"""Entity census: read the scored script FIRST, find the people/places/things
that repeat or carry the story, and demand reference sheets for them BEFORE any
scene production.

Why (2026-07-08, the machine-identity day): the Therac-25 — the literal title
character — went through the whole episode without a faithful reference sheet.
The bible's sheet had drifted from the real research photos, the room canonical
showed a different machine entirely, and eleven shots shipped with the wrong
machine before an episode-level watch-through caught it. Every recurring entity
that matters must be identified up front, given a sheet grounded on REAL
reference material, and operator-approved before segment one is generated.

Pipeline position: STAGE -1 — run before build_asset_bible / any generation.

CLI:
  python -m lib.entity_census <project_id> [--script PATH] [--bible PATH]
  # writes artifacts/entity_census.json and prints the gap table
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

_log = logging.getLogger(__name__)

CENSUS_MODEL = os.environ.get("CENSUS_MODEL", "gemini-2.5-flash")

_PROMPT = """You are auditing a documentary script before production begins.
Below are all narration segments (id: text), plus visual descriptions.

List every PERSON, PLACE, THING, or ORGANIZATION that either (a) appears in
more than one segment, or (b) is central to the story even if mentioned once.
For each, return a JSON object:
  name             - canonical short name
  type             - "person" | "place" | "thing" | "organization"
  segments         - list of segment ids where it appears (by name OR clear reference)
  mentions         - total mention count
  importance       - 1-5 (5 = the story cannot be told without it)
  needs_sheet      - true if it will be DEPICTED on screen repeatedly and so
                     needs a visual reference sheet for consistency (title
                     objects, recurring people, recurring rooms/machines);
                     false for abstract or single-mention items
  why              - one short sentence

Return ONLY a JSON array, no prose.

SCRIPT:
{script}
"""


def _load_script_text(script_path: Path) -> tuple[str, list[str]]:
    import yaml
    doc = yaml.safe_load(script_path.read_text(encoding="utf-8"))
    lines, seg_ids = [], []
    for seg in doc.get("segments", []):
        sid = seg.get("id", "?")
        seg_ids.append(sid)
        narr = " ".join(str(seg.get("narration", "")).split())
        lines.append(f"{sid}: {narr}")
        vis = seg.get("visual") or {}
        desc = vis.get("description")
        if desc:
            lines.append(f"{sid} (visual): {desc}")
    return "\n".join(lines), seg_ids


def _llm_census(script_text: str) -> list[dict] | None:
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    body = json.dumps({
        "contents": [{"parts": [{"text": _PROMPT.format(script=script_text)}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "max_output_tokens": 16384},
    }).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{CENSUS_MODEL}:generateContent?key={key}")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.load(r)
        txt = resp["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(txt)
    except Exception as exc:  # noqa: BLE001
        _log.warning("entity_census: LLM extraction failed (%s)", exc)
        return None


def _heuristic_census(script_text: str) -> list[dict]:
    """Keyless fallback: capitalized-phrase frequency. Crude — flagged as such."""
    hits: Counter[str] = Counter()
    seg_map: dict[str, set[str]] = {}
    for line in script_text.splitlines():
        m = re.match(r"(seg_\d+)", line)
        sid = m.group(1) if m else "?"
        for phrase in re.findall(r"\b(?:[A-Z][a-zA-Z0-9-]+(?:\s+[A-Z][a-zA-Z0-9-]+)*)", line):
            if len(phrase) < 4:
                continue
            hits[phrase] += 1
            seg_map.setdefault(phrase, set()).add(sid)
    out = []
    for name, n in hits.most_common(40):
        segs = sorted(seg_map.get(name, set()))
        if n < 2:
            continue
        out.append({"name": name, "type": "thing", "segments": segs,
                    "mentions": n, "importance": min(5, 1 + n // 3),
                    "needs_sheet": n >= 3, "why": "heuristic (no GOOGLE_API_KEY)"})
    return out


def _bible_coverage(entity: dict, bible: dict | None) -> str:
    """MISSING / CANONICAL_ONLY / SHEET — how well the bible covers this entity."""
    if not bible:
        return "NO_BIBLE"
    assets = bible.get("assets") or bible
    items = assets.items() if isinstance(assets, dict) else [
        (a.get("asset_id"), a) for a in assets]
    tokens = [t for t in re.split(r"[^a-z0-9]+", entity["name"].lower()) if len(t) > 2]
    best = "MISSING"
    for aid, a in items:
        if not isinstance(a, dict):
            continue
        hay = f"{aid} {a.get('name', '')} {a.get('description', '')}".lower()
        if tokens and all(t in hay for t in tokens) or any(
                t in str(aid).lower() for t in tokens):
            if a.get("reference_sheet"):
                return "SHEET"
            if a.get("canonical_reference_image"):
                best = "CANONICAL_ONLY"
    return best


def run_census(project_id: str, script_path: Path | None = None,
               bible_path: Path | None = None) -> dict:
    proj = Path("projects") / project_id
    script_path = script_path or (proj / "script_v5" / "scored_script.yaml")
    bible_path = bible_path or (proj / "artifacts" / "asset_bible_v6.json")
    script_text, seg_ids = _load_script_text(script_path)

    entities = _llm_census(script_text)
    source = "llm"
    if entities is None:
        entities = _heuristic_census(script_text)
        source = "heuristic"

    bible = None
    if bible_path.exists():
        bible = json.loads(bible_path.read_text(encoding="utf-8"))
    for e in entities:
        e["bible_coverage"] = _bible_coverage(e, bible)
        e["action_needed"] = bool(
            e.get("needs_sheet") and e["bible_coverage"] != "SHEET")

    entities.sort(key=lambda e: (-int(e.get("importance", 0)),
                                 -int(e.get("mentions", 0))))
    report = {"project": project_id, "source": source,
              "segments": len(seg_ids), "entities": entities}
    out = proj / "artifacts" / "entity_census.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["_path"] = str(out)
    return report


def print_report(report: dict) -> None:
    print(f"Entity census ({report['source']}) - {report['segments']} segments")
    print(f"{'entity':<28} {'type':<13} {'imp':>3} {'ment':>4} "
          f"{'segs':>4}  {'bible':<15} action")
    for e in report["entities"]:
        flag = "** BUILD SHEET **" if e.get("action_needed") else ""
        print(f"{e['name'][:27]:<28} {e.get('type', '?'):<13} "
              f"{e.get('importance', 0):>3} {e.get('mentions', 0):>4} "
              f"{len(e.get('segments', [])):>4}  {e.get('bible_coverage'):<15} {flag}")
    print(f"\nwrote {report.get('_path')}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--script", type=Path, default=None)
    ap.add_argument("--bible", type=Path, default=None)
    args = ap.parse_args()
    rep = run_census(args.project_id, args.script, args.bible)
    print_report(rep)
