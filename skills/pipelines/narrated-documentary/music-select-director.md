# Music Select Director - Narrated Documentary Pipeline

## When To Use

The segment plan defines the mood arc of the video. You now need to
select or generate a music bed that supports the narration without
competing with it. Music is the emotional backbone — get it wrong and
the documentary feels like a slide deck with sound.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/music_manifest.schema.json` | Artifact validation |
| Prior artifact | segment_plan | Scene moods and pacing types |
| Tool | `music_select` | Browse and select from music library |
| Tool | `music_gen` | Generate music via API |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Checks the local music library first, then falls back to API-based
music generation. Matches track mood and energy to the segment plan's
emotional arc. Presents options to the user for approval before
locking the selection.

## Inputs

- **segment_plan** artifact: scenes with mood tags and pacing types

## Outputs

- **music_manifest** artifact: version, tracks array with path, mood,
  duration, and source

## Workflow

### 1. Analyze The Mood Arc

Before selecting music, map the segment plan's mood progression:

Read through all scenes in order and note the mood sequence, e.g.:
calm -> tense -> ominous -> urgent -> crisis -> somber -> reflective

This gives you the emotional shape the music needs to support. A
single track must either match this arc naturally or be neutral enough
to not conflict with any section.

### 2. Check Local Music Library

Always check `music_library/` first:

```python
music_select.execute({
    "operation": "list",
    "library_path": "music_library/",
})
```

For each available track, evaluate:
- **Mood match:** Does the track's energy match the video's dominant
  mood? A tense investigation documentary needs different music than
  an elegiac memorial piece.
- **Duration:** Track should be at least as long as the total video
  duration. Shorter tracks need looping, which is noticeable.
- **Instrumentation:** For narrated documentaries, prefer:
  - Low ambient drones (do not compete with voice)
  - Minimal percussion (percussion fights narration rhythm)
  - Strings or synth pads (sit under speech cleanly)
  - Avoid vocals (two voices at once is confusing)

### 3. Present Options To User

This stage requires human approval. Present 2-4 options:

```
MUSIC OPTIONS FOR "THE TEXAS CITY DISASTER"

1. [LIBRARY] ambient_dark_tension.mp3 (3:42)
   Mood: tense/ominous — low drone with subtle string swells
   Fits: escalation and crisis sections well, may need volume
   ducking during calm opening

2. [LIBRARY] documentary_somber.mp3 (4:15)
   Mood: somber/reflective — piano with pad background
   Fits: resolution sections well, may feel too soft for crisis

3. [GENERATE] AI-generated track
   Prompt: "dark ambient documentary score, tension building,
   low drone with occasional metallic hits, no percussion,
   4 minutes, minor key"
   Cost: ~$0.10
   Risk: Generated music can sound generic

RECOMMENDATION: Option 1 — the tension arc matches the
investigation-to-crisis-to-aftermath structure best.
```

Wait for user approval before proceeding.

### 4. Generate If Needed

If no library tracks fit and the user approves generation:

```python
music_gen.execute({
    "prompt": "dark ambient documentary score...",
    "duration_seconds": total_video_duration,
    "output_path": "projects/<name>/assets/music/generated_track.mp3",
})
```

After generation, verify:
- Track plays without artifacts
- Duration matches request (within 10%)
- Mood is appropriate (listen to a sample)

### 5. Emit The Music Manifest

```json
{
  "version": "1.0",
  "tracks": [
    {
      "track_path": "music_library/ambient_dark_tension.mp3",
      "mood": "tense",
      "duration_seconds": 222.0,
      "source": "library"
    }
  ]
}
```

Source values: "library", "generated", "user_provided"

## Quality Bar

- Local library checked before API generation
- At least 2 options presented to user
- Selected track mood matches the segment plan's dominant mood
- Track duration covers the full video length
- No vocals in the selected track (conflicts with narration)
- User approval obtained before locking selection

## Common Mistakes

- **Skipping the library check.** Generating music when a perfect
  track already exists in music_library/ wastes money and time.
- **Selecting music with vocals.** Vocals compete with narration. Even
  "background" vocals are distracting under a narrator's voice.
- **Not considering the full mood arc.** A track that fits the crisis
  section may be completely wrong for the calm opening. Evaluate the
  whole video, not just the most dramatic scene.
- **Short tracks.** A 2-minute track for a 5-minute video means
  audible looping. Always match or exceed the video duration.
- **Making the selection without user approval.** Music is subjective.
  The human_approval_default is true for this stage — present options
  and wait.
- **Over-producing the music bed.** Documentary music should be
  atmospheric, not cinematic-epic. The footage and narration carry the
  story; the music provides emotional texture underneath.

## Tools Available

- `music_select` — Browse and evaluate tracks in the local music
  library. Lists available tracks with metadata.
- `music_gen` — Generate music via API (ElevenLabs or other available
  provider). Supports prompt-based generation with duration control.
