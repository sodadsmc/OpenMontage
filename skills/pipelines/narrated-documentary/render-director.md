# Render Director - Narrated Documentary Pipeline

## When To Use

The assembly manifest is locked with precise cuts, volumes,
animations, and framing. You now render the final video by compositing
footage, voice-over, and music into a single deliverable with a
uniform color grade.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifact | assembly_manifest | Complete cut list with timing |
| Tool | `video_compose` | Primary render engine (Remotion + FFmpeg) |
| Tool | `audio_mixer` | Voice-over and music mixing |
| Tool | `color_grade` | Uniform LUT application |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Renders the assembly manifest into a final MP4 file. Applies a uniform
color grade across all clips (critical for mixed-source documentary
footage), mixes voice-over and music at correct levels, and verifies
the output matches the assembly plan.

## Inputs

- **assembly_manifest** — cuts array with source paths, timing, volume,
  animation, and framing

## Outputs

- **render_report** — version, output path, duration, verification notes

## Workflow

### 1. Pre-Render Validation

Before rendering, validate every source path in the assembly manifest:

- All clip files exist at their specified paths
- All clip files are playable (ffprobe returns valid metadata)
- Voice-over audio files exist for all scenes
- Music track file exists

If any file is missing, STOP. Do not render a partial video — surface
the missing file to the user.

### 2. Apply Color Grade

Mixed-source documentary footage comes from wildly different cameras
and eras. A uniform LUT is what makes a 2005 CSB investigation clip
sit next to a 2023 Pexels drone shot without jarring the viewer.

Select a LUT based on the segment plan's dominant mood:

| Mood Profile | LUT | Effect |
|-------------|-----|--------|
| tense/ominous | cool_desaturated_40 | Cooled highlights, pulled blacks |
| somber/reflective | warm_film_30 | Slight warmth, lifted shadows |
| urgent/crisis | high_contrast_50 | Crushed blacks, boosted midtones |
| neutral/technical | neutral_doc_15 | Minimal correction, clean look |

Apply the LUT at the composition level via `color_grade`, not per
clip. One LUT, one timeline, one consistent look.

```python
color_grade.execute({
    "input_manifest": assembly_manifest,
    "lut_profile": "cool_desaturated_40",
    "output_dir": "projects/<name>/renders/graded/",
})
```

### 3. Mix Audio

Layer the audio tracks in this order (top = highest priority):

1. **Voice-over narration** — Full volume (1.0)
2. **Music bed** — Ducked under narration (0.3-0.5 during speech,
   0.6-0.7 during gaps between sentences)
3. **Source audio** — Muted (volume = 0, per assembly manifest)

Use `audio_mixer` to produce the final audio mix:

```python
audio_mixer.execute({
    "voice_tracks": voice_manifest_paths,
    "music_track": music_manifest_path,
    "ducking": {
        "enabled": true,
        "voice_threshold_db": -20,
        "reduction_db": -12,
        "attack_ms": 100,
        "release_ms": 300,
    },
    "output_path": "projects/<name>/renders/audio_mix.wav",
})
```

### 4. Compose Final Video

Render the graded footage with the mixed audio:

```python
video_compose.execute({
    "operation": "render",
    "output_path": "projects/<name>/renders/final.mp4",
    "assembly_manifest": assembly_manifest,
    "audio_mix": "projects/<name>/renders/audio_mix.wav",
    "encoding": {
        "codec": "libx264",
        "crf": 18,
        "fps": 24,
        "pixel_format": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "192k",
    },
})
```

### 5. Post-Render Verification

After the render succeeds, probe the output:

- **Duration check:** Output duration should match assembly_manifest
  total within 1 second
- **Resolution check:** Should match target canvas (1920x1080 for
  YouTube, 1080x1920 for social short)
- **Audio check:** Verify audio stream exists. Check that voice-over
  is audible and music is present but ducked.
- **Visual check:** Extract frames at 10%, 50%, 90% of duration.
  Verify color grade is applied and no black/corrupt frames.
- **File size check:** Sanity check — a 5-minute 1080p H.264 at
  CRF 18 should be 50-150MB. Much smaller suggests encoding failure.

Record all verification results in the render report.

### 6. Emit The Render Report

```json
{
  "version": "1.0",
  "outputs": [
    {
      "path": "projects/<name>/renders/final.mp4",
      "format": "mp4",
      "codec": "h264",
      "audio_codec": "aac",
      "resolution": "1920x1080",
      "fps": 24,
      "duration_seconds": 312.5,
      "file_size_bytes": 89231456
    }
  ],
  "render_time_seconds": 67.2,
  "warnings": [],
  "verification_notes": [
    "Duration 312.5s matches assembly plan 312.0s (+0.5s)",
    "Resolution 1920x1080 matches target canvas",
    "Audio stream present, voice-over audible at sampled points",
    "Color grade cool_desaturated_40 applied uniformly",
    "Frame samples at 10/50/90% show no corruption"
  ],
  "metadata": {
    "pipeline": "narrated-documentary",
    "lut_applied": "cool_desaturated_40",
    "audio_mix_mode": "ducked"
  }
}
```

## Quality Bar

- Output file exists and passes ffprobe validation
- Duration within 1 second of assembly_manifest total
- Color grade applied uniformly (one LUT, whole timeline)
- Voice-over is audible and music is ducked under speech
- No black frames, corrupt segments, or audio dropouts
- Encoding matches documentary spec (H.264, CRF 18, 24fps, AAC 192k)

## Common Mistakes

- **Skipping color grade.** Mixed-source footage without a uniform LUT
  looks like a YouTube compilation, not a documentary. The LUT is
  non-negotiable.
- **Voice-over too quiet under music.** Music ducking must be
  aggressive enough that every word is clear. Test at multiple points.
- **Rendering at 30fps.** Documentary standard is 24fps. Do not
  upconvert — drop frames evenly if source clips are 30fps.
- **Not checking file size.** A 5-minute video that renders to 2MB is
  broken. Always sanity check.
- **Per-clip color grading.** One LUT for the whole timeline. Per-clip
  grading takes 10x longer and produces less consistent results.
- **Rendering without pre-validation.** A missing source file midway
  through a 5-minute render wastes time. Check all paths first.

## Tools Available

- `video_compose` — Primary render engine. Handles footage composition
  with Remotion for animated elements and FFmpeg for video processing.
- `audio_mixer` — Multi-track audio mixing with ducking support.
  Layers voice-over, music, and source audio at specified levels.
- `color_grade` — Uniform LUT application across a timeline of clips.
  Supports multiple LUT profiles for different documentary registers.
