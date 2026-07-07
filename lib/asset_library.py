"""Asset library — a queryable catalog of every paid/approved visual asset in a project,
so scene prep starts with "shop first" instead of regenerating what we already own.

Reuse is deterministic, free, and on-model BY CONSTRUCTION; regeneration is a paid dice-roll
(operator directive, 2026-07-06). Proven reuse moves this catalogs for:
  - approved stills as DIRECT keyframes (copy over _keyframe_review/.../keyframe.png)
  - gold plates for per-beat grounding (_gold_refs + content-regex sidecar)
  - approved beat clips (use-clip / splice), Veo one-offs, FLF morph frames
  - $0 derivations: last-frame extraction, drain_endpoint, stat cards

Project-agnostic: everything is keyed by project id under a projects root — nothing in here
is specific to one documentary.

CLI (from the workspace root):
  python -m lib.asset_library index <pid>            build artifacts/asset_library.json + summary
  python -m lib.asset_library find  <pid> <regex>    search labels/prompts/scenes/asset ids
  python -m lib.asset_library list  <pid> [kind]     dump records (optionally one kind)
Options: --projects-dir DIR (default ./projects, or $OPENMONTAGE_PROJECTS_DIR)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

KINDS = ("gold_plate", "take", "beat_clip", "keyframe", "veo_proof", "bible_asset")


def _projects_dir(override: str | None = None) -> Path:
    return Path(override or os.environ.get("OPENMONTAGE_PROJECTS_DIR") or "projects")


def _rel(p: Path, root: Path) -> str:
    """Repo-relative POSIX path (stable across machines; never absolute)."""
    try:
        return p.relative_to(root.parent.parent).as_posix() if root.name == "" else p.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return p.as_posix()


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scene_verdicts(proj: Path) -> dict:
    """scene_id -> latest human verdict, and rid -> approved, from the append-only event log."""
    verdicts, approved_rids = {}, set()
    log = proj / "feedback" / "events.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") == "human_verdict" and e.get("scene_id"):
                verdicts[e["scene_id"]] = (e.get("payload") or {}).get("verdict")
            elif e.get("type") == "revision_approved":
                rid = (e.get("payload") or {}).get("revision_id")
                if rid:
                    approved_rids.add(rid)
    return {"scenes": verdicts, "rids": approved_rids}


def build_index(pid: str, projects_dir: Path) -> list[dict]:
    proj = projects_dir / pid
    if not proj.is_dir():
        raise SystemExit(f"project not found: {proj}")
    seen = _scene_verdicts(proj)
    records: list[dict] = []

    def add(kind: str, path: Path, scene: str = "", label: str = "", **extra):
        records.append({"kind": kind, "project": pid, "scene": scene,
                        "path": path.as_posix(), "label": label,
                        "approved": bool(extra.pop("approved", False)), **extra})

    # 1) Gold plates + their content-regex sidecars (what each plate is FOR).
    gold = proj / "assets" / "ai_segments" / "_gold_refs"
    sidecars = {}
    if gold.is_dir():
        for sc in gold.glob("*.beats.json"):
            sid = sc.name.replace(".beats.json", "")
            for ent in (_read_json(sc) or []):
                if isinstance(ent, dict) and ent.get("file"):
                    sidecars[ent["file"]] = ent.get("match", "")
        for png in sorted(gold.glob("*.png")):
            m = re.match(r"(seg_\d+)", png.stem)
            add("gold_plate", png, scene=(m.group(1) if m else png.stem),
                label=f"grounding plate (matches: {sidecars.get(png.name, 'scene fallback')})",
                approved=True)  # plates are curated FROM approved frames by definition

    # 2) Takes + their per-beat clips (lane/prompt/label/hard_shot recorded for regen & reuse).
    idx = _read_json(proj / "artifacts" / "ai_segments_takes.json") or {}
    for sid, entry in idx.items():
        takes = entry.get("takes", entry) if isinstance(entry, dict) else entry
        if not isinstance(takes, list):
            continue
        scene_ok = seen["scenes"].get(sid) == "approve"
        for t in takes:
            ok = scene_ok or t.get("verdict") in ("accepted", "approved")
            p = proj.parent.parent / (t.get("path") or "")
            add("take", p, scene=sid,
                label=t.get("label") or f"take {t.get('take')} ({t.get('lane') or t.get('provider') or ''})",
                approved=ok, take=t.get("take"), verdict=t.get("verdict"),
                cost_usd=t.get("cost_usd"), source=t.get("source") or "generated")
            for b in (t.get("beats") or []):
                if b.get("status") != "generated" or not b.get("clip"):
                    continue
                add("beat_clip", proj.parent.parent / b["clip"], scene=sid,
                    label=b.get("label") or "", approved=ok, take=t.get("take"),
                    beat=b.get("idx"), lane=b.get("lane"), hard_shot=bool(b.get("hard_shot")),
                    dur=b.get("dur"), prompt=(b.get("prompt") or "")[:400])

    # 3) Authored keyframe stills (paid Nano frames — reusable as direct keyframes/plates).
    review = proj / "assets" / "ai_segments" / "_keyframe_review"
    if review.is_dir():
        for setdir in sorted(review.iterdir()):
            if not setdir.is_dir() or "__" not in setdir.name:
                continue
            sid, rid = setdir.name.split("__", 1)
            meta = {f.get("idx"): f for f in (_read_json(setdir / "keyframes.json") or [])
                    if isinstance(f, dict)}
            for kf in sorted(setdir.glob("b*/keyframe.png")):
                bidx = int(kf.parent.name[1:]) if kf.parent.name[1:].isdigit() else None
                add("keyframe", kf, scene=sid,
                    label=(meta.get(bidx) or {}).get("label", ""),
                    approved=rid in seen["rids"], revision=rid, beat=bidx)

    # 4) Veo one-offs (expensive reference-lane clips + their proof frames).
    veo = proj / "assets" / "ai_segments" / "_veo_proof"
    if veo.is_dir():
        for f in sorted(veo.iterdir()):
            if f.suffix.lower() in (".mp4", ".png", ".jpg"):
                add("veo_proof", f, scene=f.stem.split("__")[0].split("_veo")[0],
                    label="veo reference-lane artifact")

    # 5) Bible assets (sheets, canonicals, identity tokens, real reference photos).
    bibles = sorted((proj / "artifacts").glob("asset_bible*.json"))
    bible = _read_json(bibles[-1]) if bibles else None
    assets = (bible or {}).get("assets", bible) or {}
    items = assets.items() if isinstance(assets, dict) else [(a.get("asset_id"), a) for a in assets]
    for aid, a in items:
        if not isinstance(a, dict):
            continue
        add("bible_asset", proj / "assets" / "asset_bible" / f"{aid}.png", scene="",
            label=(a.get("description") or "")[:200], approved=True, asset_id=aid,
            type=a.get("type"), has_sheet=bool(a.get("reference_sheet")),
            n_tokens=len(a.get("identity_tokens") or []),
            n_real_photos=len(a.get("reference_images") or []))

    out = proj / "artifacts" / "asset_library.json"
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    return records


def _load(pid: str, projects_dir: Path) -> list[dict]:
    p = projects_dir / pid / "artifacts" / "asset_library.json"
    recs = _read_json(p)
    if recs is None:
        recs = build_index(pid, projects_dir)
    return recs


def _fmt(r: dict) -> str:
    flags = "*" if r.get("approved") else " "  # ASCII: Windows consoles are cp1252
    extra = " ".join(f"{k}={r[k]}" for k in ("lane", "beat", "take", "asset_id") if r.get(k) is not None)
    return f"[{flags}] {r['kind']:<11} {r.get('scene') or '-':<8} {extra:<24} {r.get('label','')[:70]}\n    {r['path']}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["index", "find", "list"])
    ap.add_argument("pid")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--projects-dir", default=None)
    ap.add_argument("--approved-only", action="store_true")
    args = ap.parse_args(argv)
    pdir = _projects_dir(args.projects_dir)

    if args.cmd == "index":
        recs = build_index(args.pid, pdir)
        counts = {}
        for r in recs:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        print(f"indexed {len(recs)} assets -> {pdir / args.pid / 'artifacts' / 'asset_library.json'}")
        for k in KINDS:
            print(f"  {k:<12} {counts.get(k, 0)}")
        return

    recs = _load(args.pid, pdir)
    if args.approved_only:
        recs = [r for r in recs if r.get("approved")]
    if args.cmd == "list":
        recs = [r for r in recs if not args.query or r["kind"] == args.query]
    else:  # find
        rx = re.compile(args.query, re.I)
        recs = [r for r in recs if rx.search(json.dumps(r, ensure_ascii=False))]
    for r in recs:
        print(_fmt(r))
    print(f"-- {len(recs)} match(es)")


if __name__ == "__main__":
    main()
