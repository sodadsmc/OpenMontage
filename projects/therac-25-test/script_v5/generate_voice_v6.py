"""Generate TTS audio for all v6 scored script segments."""
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from lib.scored_script import load_scored_script
from tools.audio.elevenlabs_tts import ElevenLabsTTS

script = load_scored_script("projects/therac-25-test/script_v5/scored_script.yaml")
out_dir = Path("projects/therac-25-test/assets/audio_v6")
out_dir.mkdir(parents=True, exist_ok=True)

tts = ElevenLabsTTS()
VOICE_ID = "onwK4e9ZLuTAKqWW03F9"  # Daniel
PDICT_ID = "YiawY4G8SCQv8kvJZqZA"
PDICT_VERSION = "kiZq7TPX1DLFM4DGMV36"
OUTPUT_FORMAT = "mp3_44100_192"   # needs Creator tier; drop to _128 below it

# Preflight: probe the EXACT production request shape before the batch.
# /v1/user is useless for scoped keys (401s while TTS works), and tier gates
# (e.g. 192kbps needing Creator) only 403 on the real endpoint+format combo.
if any(not (out_dir / f"seg_{s.index:03d}.mp3").exists() for s in script.segments):
    probe = tts.execute({
        "text": "Preflight.", "voice_id": VOICE_ID,
        "model_id": "eleven_multilingual_v2",
        "output_format": OUTPUT_FORMAT,
        "output_path": str(out_dir / "_preflight.mp3"),
        "pronunciation_dictionary_id": PDICT_ID,
        "pronunciation_dictionary_version_id": PDICT_VERSION,
        "with_timestamps": True,
        "timestamps_output_path": str(out_dir / "_preflight.alignment.json"),
    })
    if not probe.success:
        print(f"PREFLIGHT FAILED — fix the key/tier before spending: {probe.error}")
        print("(Scoped keys 401 on /v1/user while TTS works; a 403 here names "
              "the real gate — read its JSON body.)")
        sys.exit(1)
    for p in (out_dir / "_preflight.mp3", out_dir / "_preflight.alignment.json"):
        if p.exists():
            p.unlink()

# Stability 0.5 per the channel's TTS research (0.6+ flattens inflection).
# The known failure mode at low stability is hallucinated emphasis on very
# short fragments, so fragment-heavy segments override upward. A/B at review.
STABILITY_DEFAULT = 0.5
STABILITY_OVERRIDES = {
    "seg_006": 0.6, "seg_009": 0.6, "seg_017": 0.6, "seg_020": 0.6,
    "seg_024": 0.6, "seg_028": 0.6, "seg_029": 0.6, "seg_030": 0.6,
}

print(f"Generating {len(script.segments)} voice segments...")
total_chars = 0
generated = 0
skipped = 0

for seg in script.segments:
    out_path = out_dir / f"seg_{seg.index:03d}.mp3"
    align_path = out_dir / f"seg_{seg.index:03d}.alignment.json"

    if out_path.exists() and out_path.stat().st_size > 1024:
        # Resume-safe: never regenerate audio just to get an alignment.
        note = "" if align_path.exists() else " (no alignment — pre-timestamps audio)"
        print(f"  {seg.id}: skip (exists, {out_path.stat().st_size} bytes){note}")
        total_chars += len(seg.narration)
        skipped += 1
        continue

    result = tts.execute({
        "text": seg.narration,
        "voice_id": VOICE_ID,
        "model_id": "eleven_multilingual_v2",
        "stability": STABILITY_OVERRIDES.get(seg.id, STABILITY_DEFAULT),
        "similarity_boost": 0.8,
        "style": 0.15,
        "output_format": OUTPUT_FORMAT,
        "output_path": str(out_path),
        "pronunciation_dictionary_id": PDICT_ID,
        "pronunciation_dictionary_version_id": PDICT_VERSION,
        "with_timestamps": True,
        "timestamps_output_path": str(align_path),
    })

    if result.success:
        total_chars += len(seg.narration)
        generated += 1
        print(f"  {seg.id}: OK ({len(seg.narration)} chars, alignment -> {align_path.name})")
    else:
        print(f"  {seg.id}: FAILED: {result.error[:80]}")

print(f"\nTotal: {generated} generated, {skipped} skipped, {total_chars} chars")

# Build narration track with silence beats — FAIL CLOSED on missing segments.
# (A failed TTS once shipped a master silently 8s short; the sync gate caught
# it a full pipeline-run later. Never concat around a hole.)
missing = [seg.id for seg in script.segments
           if not (out_dir / f"seg_{seg.index:03d}.mp3").exists()]
if missing:
    print(f"\nABORT: {len(missing)} segment(s) missing audio: {', '.join(missing)}")
    print("Narration master NOT rebuilt. Fix the TTS failures above and rerun.")
    sys.exit(1)

print("\nBuilding narration track with silence beats...")
parts = []

for seg in script.segments:
    seg_path = out_dir / f"seg_{seg.index:03d}.mp3"
    parts.append(("audio", str(seg_path)))
    if seg.silence_after_s > 0:
        parts.append(("silence", seg.silence_after_s))

# Generate silence files
silence_dir = out_dir / "silence"
silence_dir.mkdir(exist_ok=True)

concat_list = []
for i, (ptype, val) in enumerate(parts):
    if ptype == "audio":
        concat_list.append(val)
    elif ptype == "silence":
        # Cache keyed by DURATION, not position — the old positional key
        # (silence_{i}) silently served stale gaps after any silence_after_s
        # edit, defeating the change until the cache was hand-cleared.
        sil_path = silence_dir / f"silence_{float(val):g}s.mp3"
        if not sil_path.exists():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "anullsrc=r=44100:cl=stereo",
                "-t", str(val), "-c:a", "libmp3lame", "-b:a", "192k",
                str(sil_path),
            ], capture_output=True, timeout=10)
        concat_list.append(str(sil_path))

# Concat
concat_file = out_dir / "concat.txt"
with open(concat_file, "w") as f:
    for path in concat_list:
        safe = str(Path(path).resolve()).replace("\\", "/")
        f.write(f"file '{safe}'\n")

output = out_dir / "narration_v6.mp3"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", str(concat_file), "-c:a", "libmp3lame", "-b:a", "192k",
    str(output),
], capture_output=True, timeout=120)

# Verify
p = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", str(output)],
    capture_output=True, text=True,
)
dur = float(p.stdout.strip())
print(f"Narration track: {dur:.1f}s ({dur/60:.1f} min)")
print(f"Output: {output}")
