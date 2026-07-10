"""Staged review: generate ONE ~3-minute chunk of the tail, report its cost, build a
contact sheet for fast eyeballing, and STOP for inspection BEFORE spending on the rest.
Catch mistakes early and fix the pipeline between chunks.

The per-shot Gemini QC gate (fail-closed) runs DURING generation, so artifacted/off-meaning
clips are caught + retried (or the build stops). After a chunk lands, inspect the cut + the
contact sheet, then run the next chunk only if it's good.

  # preview the chunk's cost (no spend)
  python staged_chunk.py --from seg_010 --until seg_019 --name chunk1
  # generate it, report cost, build the contact sheet, then stop for review
  python staged_chunk.py --from seg_010 --until seg_019 --name chunk1 --yes
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BUILD = "projects/therac-25-test/script_v5/build_preview.py"
RENDERS = Path("projects/therac-25-test/renders")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_seg", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--name", required=True, help="output basename, e.g. chunk1")
    ap.add_argument("--yes", action="store_true", help="actually spend on this chunk")
    a = ap.parse_args()

    from lib.cost_ledger import report as cost_report

    cmd = [sys.executable, BUILD, "--from", a.from_seg, "--until", a.until, "--out-name", a.name]
    if not a.yes:
        print(f"=== COST PREVIEW for {a.name} ({a.from_seg}..{a.until}) — no spend ===")
        subprocess.run(cmd)
        print("\n  Re-run with --yes to generate this chunk.")
        return 0

    before = cost_report()["total_usd"]
    print(f"=== GENERATING {a.name} ({a.from_seg}..{a.until}) ===")
    r = subprocess.run(cmd + ["--yes"])
    if r.returncode != 0:
        print(f"\n!! {a.name} generation FAILED (rc={r.returncode}) — fix the cause before spending more.")
        return 1

    cut = RENDERS / f"{a.name}.mp4"
    after = cost_report()
    spent = round(after["total_usd"] - before, 2)
    print(f"\n=== {a.name} DONE — cost this chunk: ${spent:.2f} ===")
    for prov, b in sorted(after["by_provider"].items(), key=lambda x: -x[1]["cost_usd"]):
        print(f"     {prov:12s} ${b['cost_usd']:.2f} cumulative")

    # contact sheet: one thumbnail every ~15s, tiled, for fast eyeballing of every beat
    if cut.exists():
        contact = RENDERS / f"{a.name}_contact.png"
        subprocess.run(["ffmpeg", "-y", "-i", str(cut), "-vf",
                        "fps=1/15,scale=360:-1,tile=4x4", "-frames:v", "1",
                        str(contact), "-loglevel", "error"])
        print(f"\n  REVIEW:\n    cut          -> {cut}\n    contact sheet-> {contact}")
    print(f"\n  Inspect, fix the pipeline/beats if needed, THEN run the next chunk.")
    print(f"  Running tally: python -m lib.cost_ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
