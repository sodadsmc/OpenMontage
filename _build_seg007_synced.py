"""Produce the canonical narration-synced seg_007 diagram (the LOCKED-IN method).

The scene + the motion primitives now live in lib.sketch_diagrams
(``synced_linac`` / ``LINAC_SYNC_CUES`` / the fired_beam/scatter_rays/beam_flow/
pulse_glow helpers). This is the thin runner: resolve the cue times from seg_007's
word alignment, render in the channel hand-drawn style, mux the narration, and
write the canonical clip.

Run:  python _build_seg007_synced.py [--frame T]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from lib.word_timing import load_alignment, words
from lib import sketch_diagrams as sk

ALIGN = "projects/therac-25-test/assets/audio_v6/seg_007.alignment.json"
NARR = "projects/therac-25-test/assets/audio_v6/seg_007.mp3"
OUT_DIR = Path("projects/therac-25-test/assets/visuals_synced")
RAW = OUT_DIR / "seg_007_sketch.mp4"
FINAL = OUT_DIR / "seg_007_sketch_final.mp4"
CANON = OUT_DIR / "seg_007_diagram.mp4"   # the locked-in canonical seg_007 clip


def main() -> int:
    align = load_alignment(ALIGN)
    total = words(align)[-1]["end_s"]
    C = sk.resolve_cues(align, sk.LINAC_SYNC_CUES)
    print("cues:", {k: round(v, 2) for k, v in C.items()})
    draw = sk.synced_linac(C)

    if "--frame" in sys.argv:
        ti = float(sys.argv[sys.argv.index("--frame") + 1])
        out = OUT_DIR / f"_frames/sketch_{int(ti)}s.png"
        sk.render_frame(draw, ti, total, out)
        print("frame:", out)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    styled = sk.render_template(draw, total, RAW, fps=15, finish=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(styled), "-i", NARR,
                    "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest", str(FINAL), "-loglevel", "error"], check=True)
    # canonical copy under a clean name (what the assembly reads for seg_007)
    subprocess.run(["ffmpeg", "-y", "-i", str(FINAL), "-c", "copy", str(CANON),
                    "-loglevel", "error"], check=True)
    print("final:", FINAL, "| canonical:", CANON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
