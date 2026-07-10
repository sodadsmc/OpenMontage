"""Generate all voice segments + build narration track with silence beats."""
import json
import subprocess
import time
from pathlib import Path

from lib.script_parser import parse_showrunner_script
from tools.audio.elevenlabs_tts import ElevenLabsTTS

parsed = parse_showrunner_script("projects/therac-25-test/script_v5/script_raw.md")
out_dir = Path("projects/therac-25-test/assets/audio_v5")
out_dir.mkdir(parents=True, exist_ok=True)

tts = ElevenLabsTTS()
VOICE_ID = "onwK4e9ZLuTAKqWW03F9"  # Daniel
PDICT_ID = "YiawY4G8SCQv8kvJZqZA"
PDICT_VERSION = "rUFMp2Iw6JskMZpH3WBF"

# Step 1: Generate all voice segments
print("Generating %d voice segments..." % len(parsed.voice_segments))
total_chars = 0
for seg in parsed.voice_segments:
    out_path = out_dir / f"seg_{seg.index:03d}.mp3"
    if out_path.exists() and out_path.stat().st_size > 1024:
        print("  seg_%03d: skip (exists)" % seg.index)
        total_chars += len(seg.text)
        continue

    result = tts.execute({
        "text": seg.text,
        "voice_id": VOICE_ID,
        "model_id": "eleven_multilingual_v2",
        "stability": 0.6,
        "similarity_boost": 0.8,
        "style": 0.15,
        "output_format": "mp3_44100_192",
        "output_path": str(out_path),
        "pronunciation_dictionary_id": PDICT_ID,
        "pronunciation_dictionary_version_id": PDICT_VERSION,
    })

    if result.success:
        total_chars += len(seg.text)
        print("  seg_%03d: OK (%d chars)" % (seg.index, len(seg.text)))
    else:
        print("  seg_%03d: FAILED: %s" % (seg.index, result.error[:80]))

print("\nTotal chars: %d" % total_chars)

# Step 2: Build ordered parts list (audio + silence)
print("\nBuilding narration track with silence beats...")
parts = []

timing_map = {}
for cue in parsed.timing_cues:
    key = cue.after_segment
    if key not in timing_map:
        timing_map[key] = []
    timing_map[key].append(cue)

for seg in parsed.voice_segments:
    seg_path = out_dir / f"seg_{seg.index:03d}.mp3"
    if seg_path.exists():
        parts.append(("audio", str(seg_path)))

    cues = timing_map.get(seg.index, [])
    for cue in cues:
        parts.append(("silence", cue.duration))

# Step 3: Generate silence files
silence_dir = out_dir / "silence"
silence_dir.mkdir(exist_ok=True)

concat_list = []
for i, (ptype, val) in enumerate(parts):
    if ptype == "audio":
        concat_list.append(val)
    elif ptype == "silence":
        sil_path = silence_dir / f"silence_{i}.mp3"
        if not sil_path.exists():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "anullsrc=r=44100:cl=stereo",
                "-t", str(val), "-c:a", "libmp3lame", "-b:a", "192k",
                str(sil_path),
            ], capture_output=True, timeout=10)
        concat_list.append(str(sil_path))

# Step 4: Concat
concat_file = out_dir / "concat.txt"
with open(concat_file, "w") as f:
    for path in concat_list:
        safe = str(Path(path).resolve()).replace("\\", "/")
        f.write("file '%s'\n" % safe)

output = out_dir / "narration_v5.mp3"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", str(concat_file), "-c:a", "libmp3lame", "-b:a", "192k",
    str(output),
], capture_output=True, timeout=60)

# Verify
p = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(output)],
    capture_output=True, text=True,
)
dur = float(p.stdout.strip())
silence_total = sum(p[1] for p in parts if p[0] == "silence")
print("Narration track: %.1fs (%.1f min)" % (dur, dur / 60))
print("Silence beats: %d cues, %.0fs total" % (
    len([p for p in parts if p[0] == "silence"]),
    silence_total,
))
