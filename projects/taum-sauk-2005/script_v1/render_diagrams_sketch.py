"""Render the Taum Sauk sketch diagrams (narration-synced, hand-inked, animated)
through render_template (frames -> ffmpeg CFR30 -> channel finishing) and install
to ai_segments/{sid}.mp4.

  python render_diagrams_sketch.py [seg_008 seg_015 ...]   # subset, default all
"""
import os, sys, json
os.environ.setdefault("CHANNEL_STYLE", "graphic-novel-disaster")
sys.path.insert(0, r"D:\OpenMontage2"); os.chdir(r"D:\OpenMontage2")
sys.path.insert(0, r"D:\OpenMontage2\projects\taum-sauk-2005\script_v1")
import taum_sketch as TS
from lib.sketch_diagrams import resolve_cues, render_template

AUD = "projects/taum-sauk-2005/assets/audio_v6"
AI = "projects/taum-sauk-2005/assets/ai_segments"
DUR = {s["id"]: s for s in json.load(open("projects/taum-sauk-2005/artifacts/duration_map_v6.json", encoding="utf-8"))["segments"]}


def main():
    want = sys.argv[1:] or list(TS.SCENES.keys())
    for sid in want:
        factory, cues = TS.SCENES[sid]
        align = json.load(open(f"{AUD}/{sid}.alignment.json", encoding="utf-8"))
        C = resolve_cues(align, cues)
        dur = DUR[sid]["total_duration_s"]
        out = f"{AI}/{sid}.mp4"
        # render RAW (no finishing): the taum timeline is ungraded, so the bright
        # hand-inked sketch matches the raw footage brightness (a heavy duotone
        # crushed the amber text to illegible). render_template(finish=False)
        # writes {out}_raw.mp4 -> move it to the canonical slot path.
        res = render_template(factory(C), dur, out, fps=15, finish=False)
        if res and os.path.exists(res) and os.path.abspath(res) != os.path.abspath(out):
            os.replace(res, out)
        print(f"{sid}: {out}  (slot {dur:.1f}s)", flush=True)
    print("SKETCH DIAGRAMS DONE", flush=True)


if __name__ == "__main__":
    main()
