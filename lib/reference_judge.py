"""Reference-anchored vision review: checklist verdicts + candidate ranking for gold plates.

The original `reference_fidelity` gate failed (hallucinated a "C-arm" on good frames, demoted
to advisory) because of the TASK SHAPE: an absolute 0-10 score against a vague rubric, judged
partly from the model's memory. This module reshapes the task into the two forms vision
models are actually reliable at:

1. CHECKLIST (judge_frame): binary questions DERIVED FROM THE ASSET'S IDENTITY TOKENS,
   answered with the reference sheet + real photo IN CONTEXT. "Does the machine have a
   smooth rounded cylindrical treatment head hanging from a curved arm? yes/no" is
   verifiable; "rate fidelity 0-10" is not.
2. RANKING (rank_frames): "which of these N candidates matches the references best, and
   what mismatches does each have" — comparative judgment against visible references,
   which is far more reliable than absolute scoring.

Both are ADVISORY: they annotate and nominate; the operator ratifies. Nothing here ever
blocks or fails a job (the eyeball stays the bar — this shrinks WHAT the eyeball must do
from 'stare at everything' to 'ratify one nominated frame + read mismatch notes').

CLI (repo root):
    python -m lib.reference_judge nominate <project_id> <sid> [--apply]
        Rank every candidate still for a scene (keyframe reviews + proof frames) against
        the project's machine references and nominate the best as the scene gold plate
        (_gold_refs/{sid}.png). --apply copies the winner; default is a dry-run report.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# EMPIRICALLY VALIDATED 2026-07-03 on labeled seg_019 frames (run `selftest` after any change):
# gemini-2.5-flash FAILED both tasks (ranked the C-arm frame FIRST — the same blindness that
# sank the original fidelity gate); gemini-2.5-pro ranked all labeled frames correctly and
# diagnosed the C-arm precisely. This module therefore defaults to PRO — it runs rarely
# (a nomination per scene, a badge per authored still), so the cost is pennies. Do NOT
# downgrade to flash for speed; below pro this judge is worse than no judge.
_MODEL = os.environ.get("REFERENCE_JUDGE_MODEL", "gemini-2.5-pro")


def _genai_model():
    import google.generativeai as genai
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return None, None
    genai.configure(api_key=api_key)
    return genai, genai.GenerativeModel(
        _MODEL,
        generation_config=genai.types.GenerationConfig(
            temperature=0.1, max_output_tokens=4096,
            response_mime_type="application/json"),
    )


def _upload_all(genai, paths):
    ups = []
    for p in paths:
        ups.append(genai.upload_file(path=str(p), display_name=Path(p).stem))
    return ups


def _cleanup(genai, ups):
    for u in ups:
        try:
            genai.delete_file(u.name)
        except Exception:  # noqa: BLE001
            pass


_JUDGE_PROMPT = """The FIRST {n_refs} image(s) are REFERENCES for how "{name}" must look \
(model sheet and/or real photograph). The LAST image is a CANDIDATE frame from production.

Answer a strict CHECKLIST about the CANDIDATE by comparing it to the REFERENCES you can \
see (never from memory). The subject's required attributes:
{tokens}

Return ONLY JSON:
{{"checks": [{{"attribute": "<short name>", "pass": true|false, "note": "<what you see>"}}],
  "confusable": {{"is_confusable": true|false, "note": "<if the candidate shows a look-alike \
(e.g. a C-arm / CT donut instead of the reference design), say which>"}},
  "mismatches": ["<only the concrete visible differences from the references — empty if none>"]}}

Rules: one check per required attribute above. A check FAILS only for a VISIBLE difference \
in the candidate — not for style (illustration vs photo), lighting, camera angle, or crop. \
If the attribute is not visible in the candidate's framing, pass it with note "not visible \
in this framing"."""


def judge_frame(frame_path: str | Path, ref_paths: list[str | Path],
                subject_name: str, identity_tokens: list[str]) -> dict | None:
    """Checklist verdict for ONE candidate frame against visible references.
    Returns {"ok": bool, "mismatches": [...], "checks": [...]} or None on any failure
    (advisory — callers must treat None as 'no opinion', never as a failure)."""
    refs = [Path(p) for p in ref_paths if p and Path(p).exists()][:3]
    frame = Path(frame_path)
    if not (refs and frame.exists()):
        return None
    try:
        genai, model = _genai_model()
        if model is None:
            return None
        ups = _upload_all(genai, refs + [frame])
        toks = "\n".join(f"- {t}" for t in (identity_tokens or ["matches the reference design"]))
        out = json.loads(model.generate_content(
            ups + [_JUDGE_PROMPT.format(n_refs=len(refs), name=subject_name, tokens=toks)]).text)
        _cleanup(genai, ups)
        checks = out.get("checks") or []
        confus = (out.get("confusable") or {})
        mism = [m for m in (out.get("mismatches") or []) if m]
        if confus.get("is_confusable"):
            mism.insert(0, f"CONFUSABLE: {confus.get('note') or 'look-alike design'}")
        # ok must integrate the mismatch list too: on the labeled C-arm frame, pro passed every
        # per-attribute check but still reported the structural difference under "mismatches".
        ok = (not confus.get("is_confusable")) and all(c.get("pass") for c in checks) and not mism
        return {"ok": bool(ok), "mismatches": mism[:4], "checks": checks}
    except Exception as e:  # noqa: BLE001
        _log.warning("reference_judge: judge_frame failed for %s: %s", frame_path, e)
        return None


_RANK_PROMPT = """The FIRST {n_refs} image(s) are REFERENCES for how "{name}" must look. \
The remaining {n_cands} images are CANDIDATE frames, in order (candidate 1 = the image \
right after the references, then candidate 2, ...).

Required attributes:
{tokens}

Rank the candidates by how faithfully their depiction of {name} matches the REFERENCES \
(design fidelity only — ignore style/lighting/pose/crop differences). Return ONLY JSON:
{{"ranking": [<candidate numbers, best first>],
  "notes": [{{"candidate": <n>, "mismatches": ["<visible differences from the references>"]}}]}}"""


def rank_frames(frame_paths: list[str | Path], ref_paths: list[str | Path],
                subject_name: str, identity_tokens: list[str]) -> dict | None:
    """Order candidate frames by reference fidelity. Returns
    {"ranking": [paths best-first], "notes": {path: [mismatches]}} or None."""
    refs = [Path(p) for p in ref_paths if p and Path(p).exists()][:3]
    cands = [Path(p) for p in frame_paths if p and Path(p).exists()]
    if not (refs and len(cands) >= 2):
        return None
    try:
        genai, model = _genai_model()
        if model is None:
            return None
        ups = _upload_all(genai, refs + cands)
        toks = "\n".join(f"- {t}" for t in (identity_tokens or ["matches the reference design"]))
        out = json.loads(model.generate_content(
            ups + [_RANK_PROMPT.format(n_refs=len(refs), n_cands=len(cands),
                                       name=subject_name, tokens=toks)]).text)
        _cleanup(genai, ups)
        order = [int(i) for i in (out.get("ranking") or []) if 1 <= int(i) <= len(cands)]
        notes = {}
        for n in (out.get("notes") or []):
            try:
                notes[str(cands[int(n["candidate"]) - 1])] = (n.get("mismatches") or [])[:4]
            except Exception:  # noqa: BLE001
                pass
        return {"ranking": [str(cands[i - 1]) for i in order], "notes": notes}
    except Exception as e:  # noqa: BLE001
        _log.warning("reference_judge: rank_frames failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI: nominate a scene gold plate from existing candidate frames (dry-run by default)

def _machine_refs(proj: Path) -> tuple[str, list[str], list[Path]]:
    bible = json.loads((proj / "artifacts" / "asset_bible_v6.json").read_text(encoding="utf-8"))
    asset = next((a for a in bible.get("assets", [])
                  if a.get("type") == "location" and (a.get("identity_tokens") or [])), None)
    if asset is None:
        return "the machine", [], []
    refs: list[Path] = []
    sheet = asset.get("reference_sheet") or ""
    if sheet:
        p = Path(sheet)
        if not p.is_absolute():
            p = proj.parent.parent / sheet
        if p.is_file():
            refs.append(p)
    for url in (asset.get("reference_images") or []):
        name = (url or "").rstrip("/").rsplit("/", 1)[-1]
        if name:
            hit = next(iter(proj.glob(f"assets/**/{name}")), None)
            if hit and hit.is_file() and hit not in refs:
                refs.append(hit)
                break
    return (asset.get("subject") or "the machine"), (asset.get("identity_tokens") or []), refs


def _selftest() -> int:
    """Regression harness on LABELED seg_019 frames (operator-adjudicated 2026-07-03).
    Run after any model/prompt change: `python -m lib.reference_judge selftest`.
    PASS requires: both GOOD frames ranked above both BAD frames, and the C-arm frame
    flagged (checklist ok=False)."""
    proj = Path("projects/therac-25-test")
    name, tokens, refs = _machine_refs(proj)
    rv = proj / "assets" / "ai_segments" / "_keyframe_review"
    good = [rv / "seg_019__ab3e0cf29e11/b2/keyframe.png", rv / "seg_019__ab3e0cf29e11/b3/keyframe.png"]
    bad = [rv / "seg_019__fbfb049de3d7/b3/keyframe.png", rv / "seg_019__6bd43860ec31/b2/keyframe.png"]
    if not all(p.exists() for p in good + bad):
        print("labeled frames missing — selftest unavailable on this machine")
        return 1
    out = rank_frames(good + bad, refs, name, tokens)
    ranking = (out or {}).get("ranking") or []
    pos = {p: i for i, p in enumerate(ranking)}
    rank_ok = ranking and max(pos.get(str(g), 99) for g in good) < min(pos.get(str(b), -1) for b in bad)
    v = judge_frame(bad[0], refs, name, tokens)
    flag_ok = bool(v and not v["ok"])
    for i, p in enumerate(ranking, 1):
        print(f"  rank {i}: {p}")
    print(f"ranking separates good/bad: {rank_ok}")
    print(f"C-arm frame flagged by checklist: {flag_ok}")
    print("SELFTEST", "PASS" if (rank_ok and flag_ok) else "FAIL")
    return 0 if (rank_ok and flag_ok) else 1


def main(argv: list[str]) -> int:
    if argv and argv[0] == "selftest":
        return _selftest()
    if len(argv) < 3 or argv[0] != "nominate":
        print(__doc__)
        return 2
    pid, sid = argv[1], argv[2]
    apply = "--apply" in argv
    proj = Path("projects") / pid
    name, tokens, refs = _machine_refs(proj)
    if not refs:
        print("no local machine references found — curate real photos/sheet first")
        return 1
    cands = sorted(set(
        list(proj.glob(f"assets/ai_segments/_keyframe_review/{sid}__*/b*/keyframe.png"))
        + list(proj.glob(f"assets/ai_segments/_keyframe_proof/{sid}/*.png"))))
    cands = [c for c in cands if c.stat().st_size > 100_000][:10]
    if len(cands) < 2:
        print(f"only {len(cands)} candidate frame(s) found for {sid} — nothing to rank")
        return 1
    print(f"ranking {len(cands)} candidates for {sid} against {[r.name for r in refs]} ...")
    out = rank_frames(cands, refs, name, tokens)
    if not out or not out.get("ranking"):
        print("ranking FAILED — no nomination")
        return 1
    for i, p in enumerate(out["ranking"], start=1):
        mism = out["notes"].get(p) or []
        print(f"  {i}. {p}" + (f"   [{'; '.join(mism)}]" if mism else ""))
    winner = Path(out["ranking"][0])
    plate = proj / "assets" / "ai_segments" / "_gold_refs" / f"{sid}.png"
    if apply:
        import shutil
        plate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(winner), str(plate))
        print(f"\nNOMINATED + APPLIED: {winner} -> {plate}")
        print("(ratify by eyeball; replace the file to veto)")
    else:
        print(f"\nNOMINATION (dry run): {winner}\n(pass --apply to install as {plate})")
    return 0


if __name__ == "__main__":
    try:
        from lib.env_loader import load_env
        load_env()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main(sys.argv[1:]))
